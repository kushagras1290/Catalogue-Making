from __future__ import annotations

import csv
import io
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen

from .normalizers import clean_text

LOGGER = logging.getLogger(__name__)

SHEETS_READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
USER_AGENT = "gempundit-catalog-automation/1.0"
SPREADSHEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,256}$")
GID_RE = re.compile(r"^\d{1,32}$")
MAX_RANGE_LENGTH = 512
RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
CATALOGUE_HEADER_NAMES = {
    "sku",
    "vendor_sku",
    "gemstone_raw",
    "gemstone2_raw",
    "gem_type_raw",
    "price",
    "carat_weight",
    "ratti_weight",
}
GENERATED_TITLE_MARKERS = {
    "01_input_raw_products",
    "input raw products",
    "paste/import raw catalogue data",
}

TextFetcher = Callable[[str, Mapping[str, str], float], str]


class GoogleSheetsError(RuntimeError):
    """Base error for Google Sheets input failures."""


class GoogleSheetsConfigError(GoogleSheetsError):
    """Raised when connector configuration is incomplete or unsafe."""


class GoogleSheetsFetchError(GoogleSheetsError):
    """Raised when Google Sheets cannot be fetched."""


class GoogleSheetsParseError(GoogleSheetsError):
    """Raised when spreadsheet data cannot be converted into catalogue rows."""


@dataclass(frozen=True)
class ParsedSpreadsheetUrl:
    spreadsheet_id: str
    sheet_gid: str | None = None


@dataclass(frozen=True)
class GoogleSheetsConfig:
    spreadsheet_id: str
    value_range: str
    service_account_file: Path | None = None
    public_export: bool = False
    sheet_gid: str | None = None
    timeout_seconds: float = 15.0
    max_retries: int = 2

    def validate(self) -> None:
        if not SPREADSHEET_ID_RE.fullmatch(self.spreadsheet_id):
            raise GoogleSheetsConfigError("GOOGLE_SHEET_ID is missing or malformed.")
        if self.sheet_gid and not GID_RE.fullmatch(self.sheet_gid):
            raise GoogleSheetsConfigError("Google sheet gid is malformed.")
        if not self.value_range and not self.sheet_gid:
            raise GoogleSheetsConfigError("GOOGLE_SHEET_RANGE is required when no sheet gid is provided.")
        if len(self.value_range) > MAX_RANGE_LENGTH:
            raise GoogleSheetsConfigError("GOOGLE_SHEET_RANGE is too long.")
        if any(ord(char) < 32 for char in self.value_range):
            raise GoogleSheetsConfigError("GOOGLE_SHEET_RANGE contains control characters.")
        if self.timeout_seconds <= 0:
            raise GoogleSheetsConfigError("Google Sheets timeout must be greater than zero.")
        if self.max_retries < 0:
            raise GoogleSheetsConfigError("Google Sheets retry count cannot be negative.")
        if self.service_account_file and not self.service_account_file.exists():
            raise GoogleSheetsConfigError("Google service account file does not exist.")


def parse_spreadsheet_url(value: str) -> ParsedSpreadsheetUrl:
    """Extract spreadsheet id and gid from a Google Sheets URL or raw id."""
    text = clean_text(value)
    if not text:
        raise GoogleSheetsConfigError("Google spreadsheet URL or id is required.")
    if "://" not in text:
        return ParsedSpreadsheetUrl(spreadsheet_id=text)

    parsed = urlparse(text)
    match = re.search(r"/spreadsheets/d/([^/]+)", parsed.path)
    if not match:
        raise GoogleSheetsConfigError("Google spreadsheet URL must contain /spreadsheets/d/{id}.")

    query_gid = parse_qs(parsed.query).get("gid", [None])[0]
    fragment_gid = parse_qs(parsed.fragment).get("gid", [None])[0]
    return ParsedSpreadsheetUrl(spreadsheet_id=match.group(1), sheet_gid=query_gid or fragment_gid)


def a1_range_for_sheet(sheet_name: str, cells: str = "A:ZZ") -> str:
    title = clean_text(sheet_name)
    if not title:
        raise GoogleSheetsConfigError("Google sheet name cannot be empty.")
    escaped_title = title.replace("'", "''")
    return f"'{escaped_title}'!{cells}"


def read_google_sheet(config: GoogleSheetsConfig, fetch_text: TextFetcher | None = None) -> list[dict[str, Any]]:
    config.validate()
    if config.public_export:
        csv_text = _fetch_public_csv(config, fetch_text)
        return rows_from_csv(csv_text)
    values = _fetch_values_api(config)
    return rows_from_values(values)


def rows_from_csv(csv_text: str) -> list[dict[str, Any]]:
    if _looks_like_html(csv_text):
        raise GoogleSheetsParseError(
            "Google Sheets public export did not return CSV. Check sheet sharing or use service-account auth."
        )
    reader = csv.reader(io.StringIO(csv_text, newline=""))
    return rows_from_values(list(reader))


def rows_from_values(values: list[list[Any]]) -> list[dict[str, Any]]:
    if not values:
        return []

    row_index = 0
    headers = _clean_headers(values[row_index])
    if _looks_like_generated_title_row(headers):
        row_index += 2
        if row_index >= len(values):
            return []
        headers = _clean_headers(values[row_index])

    if not any(headers):
        raise GoogleSheetsParseError("Google sheet header row is empty.")

    rows: list[dict[str, Any]] = []
    for raw_row in values[row_index + 1 :]:
        item = {
            headers[index]: raw_row[index] if index < len(raw_row) else None
            for index in range(len(headers))
            if headers[index]
        }
        if any(clean_text(value) for value in item.values()):
            rows.append(item)
    return rows


def _fetch_public_csv(config: GoogleSheetsConfig, fetch_text: TextFetcher | None) -> str:
    url = _build_public_export_url(config)
    LOGGER.info(
        "google_sheets_public_fetch_started",
        extra={"spreadsheet_id": _redacted_sheet_id(config.spreadsheet_id), "range": config.value_range},
    )
    headers = {"User-Agent": USER_AGENT}
    if fetch_text:
        return fetch_text(url, headers, config.timeout_seconds)
    return _fetch_text_with_retries(url, headers, config.timeout_seconds, config.max_retries)


def _fetch_values_api(config: GoogleSheetsConfig) -> list[list[Any]]:
    session = _authorized_session(config)
    url = _build_values_api_url(config)
    LOGGER.info(
        "google_sheets_api_fetch_started",
        extra={"spreadsheet_id": _redacted_sheet_id(config.spreadsheet_id), "range": config.value_range},
    )
    try:
        payload = _request_json_with_retries(session, url, config)
    finally:
        session.close()

    values = payload.get("values", [])
    if not isinstance(values, list):
        raise GoogleSheetsParseError("Google Sheets API returned an invalid values payload.")
    return values


def _authorized_session(config: GoogleSheetsConfig):
    try:
        import google.auth
        from google.auth.exceptions import DefaultCredentialsError
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
    except ImportError as exc:
        raise GoogleSheetsConfigError(
            "Install google-auth[requests] to read private Google Sheets."
        ) from exc

    try:
        if config.service_account_file:
            credentials = service_account.Credentials.from_service_account_file(
                str(config.service_account_file),
                scopes=[SHEETS_READONLY_SCOPE],
            )
        else:
            credentials, _ = google.auth.default(scopes=[SHEETS_READONLY_SCOPE])
    except (DefaultCredentialsError, ValueError, OSError) as exc:
        raise GoogleSheetsConfigError("Google credentials could not be loaded.") from exc
    return AuthorizedSession(credentials, refresh_timeout=config.timeout_seconds)


def _request_json_with_retries(session: Any, url: str, config: GoogleSheetsConfig) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(config.max_retries + 1):
        try:
            response = session.request(
                "GET",
                url,
                timeout=config.timeout_seconds,
                max_allowed_time=config.timeout_seconds,
            )
            if response.status_code in RETRYABLE_STATUS_CODES and attempt < config.max_retries:
                _sleep_before_retry(attempt)
                continue
            if response.status_code >= 400:
                raise GoogleSheetsFetchError(_safe_http_error(response.status_code, response.text))
            return response.json()
        except GoogleSheetsFetchError:
            raise
        except Exception as exc:  # google-auth and requests expose several transport exceptions.
            last_error = exc
            if attempt >= config.max_retries:
                break
            _sleep_before_retry(attempt)
    raise GoogleSheetsFetchError("Google Sheets API request failed.") from last_error


def _fetch_text_with_retries(
    url: str,
    headers: Mapping[str, str],
    timeout_seconds: float,
    max_retries: int,
) -> str:
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            request = Request(url, headers=dict(headers), method="GET")
            with urlopen(request, timeout=timeout_seconds) as response:
                return response.read().decode(_response_charset(response.headers), errors="replace")
        except HTTPError as exc:
            if exc.code not in RETRYABLE_STATUS_CODES or attempt >= max_retries:
                raise GoogleSheetsFetchError(_safe_http_error(exc.code, exc.read().decode("utf-8", "replace"))) from exc
            last_error = exc
        except URLError as exc:
            last_error = exc
            if attempt >= max_retries:
                break
        _sleep_before_retry(attempt)
    raise GoogleSheetsFetchError("Google Sheets public CSV request failed.") from last_error


def _build_values_api_url(config: GoogleSheetsConfig) -> str:
    encoded_range = quote(config.value_range, safe="")
    query = urlencode(
        {
            "majorDimension": "ROWS",
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        }
    )
    return f"https://sheets.googleapis.com/v4/spreadsheets/{config.spreadsheet_id}/values/{encoded_range}?{query}"


def _build_public_export_url(config: GoogleSheetsConfig) -> str:
    if config.sheet_gid:
        params = {"format": "csv", "gid": config.sheet_gid}
        _, cells = _split_a1_range(config.value_range)
        if cells:
            params["range"] = cells
        return f"https://docs.google.com/spreadsheets/d/{config.spreadsheet_id}/export?{urlencode(params)}"

    sheet_name, cells = _split_a1_range(config.value_range)
    if not sheet_name:
        raise GoogleSheetsConfigError("Public Google Sheets export requires a sheet name or gid.")
    params = {"tqx": "out:csv", "sheet": sheet_name}
    if cells:
        params["range"] = cells
    return f"https://docs.google.com/spreadsheets/d/{config.spreadsheet_id}/gviz/tq?{urlencode(params)}"


def _split_a1_range(value_range: str) -> tuple[str, str | None]:
    text = clean_text(value_range)
    if not text:
        return "", None
    if "!" not in text:
        return _unquote_sheet_title(text), None
    sheet_name, cells = text.split("!", 1)
    return _unquote_sheet_title(sheet_name), cells or None


def _unquote_sheet_title(sheet_name: str) -> str:
    text = clean_text(sheet_name)
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1].replace("''", "'")
    return text


def _clean_headers(row: list[Any]) -> list[str]:
    return [clean_text(value) for value in row]


def _looks_like_generated_title_row(headers: list[str]) -> bool:
    non_empty_headers = [header for header in headers if header]
    if len(non_empty_headers) > 2:
        return False
    if any(header.casefold() in CATALOGUE_HEADER_NAMES for header in non_empty_headers):
        return False
    title_text = " ".join(non_empty_headers).casefold()
    return any(marker in title_text for marker in GENERATED_TITLE_MARKERS)


def _looks_like_html(value: str) -> bool:
    prefix = value.lstrip()[:500].casefold()
    return prefix.startswith("<!doctype html") or "<html" in prefix


def _response_charset(headers: Any) -> str:
    content_type = headers.get("content-type", "")
    match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    return match.group(1) if match else "utf-8"


def _safe_http_error(status_code: int, response_text: str) -> str:
    message = "Google Sheets request failed."
    try:
        parsed = json.loads(response_text)
        message = clean_text(parsed.get("error", {}).get("message")) or message
    except (json.JSONDecodeError, AttributeError):
        safe_text = clean_text(response_text)
        if safe_text:
            message = safe_text[:200]
    return f"{message} HTTP status={status_code}."


def _sleep_before_retry(attempt: int) -> None:
    delay_seconds = min(2.0, 0.25 * (2**attempt)) + random.uniform(0, 0.1)
    time.sleep(delay_seconds)


def _redacted_sheet_id(spreadsheet_id: str) -> str:
    if len(spreadsheet_id) <= 8:
        return "****"
    return f"{spreadsheet_id[:4]}...{spreadsheet_id[-4:]}"
