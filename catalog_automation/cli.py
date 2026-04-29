from __future__ import annotations

import argparse
import os
from pathlib import Path

from .excel_io import read_reference_sheet, read_sheet, write_workbook
from .enrichment import enrich_rows
from .google_sheets_io import (
    GoogleSheetsConfig,
    GoogleSheetsConfigError,
    GoogleSheetsError,
    a1_range_for_sheet,
    parse_spreadsheet_url,
    read_google_sheet,
)
from .repository import LookupRepository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Magento import workbook from GemPundit catalogue data.")
    parser.add_argument("--reference", required=True, help="Path to normalized reference workbook.")
    parser.add_argument(
        "--input-source",
        choices=("workbook", "google-sheet"),
        default=os.getenv("CATALOG_INPUT_SOURCE", "workbook"),
        help="Raw product source. Use google-sheet to read rows from Google Sheets.",
    )
    parser.add_argument("--input", required=False, help="Raw input workbook. Defaults to --reference.")
    parser.add_argument("--input-sheet", default="01_Input_Raw_Products", help="Raw input sheet name.")
    parser.add_argument("--google-sheet-url", default=os.getenv("GOOGLE_SHEET_URL"), help="Google Sheets URL.")
    parser.add_argument("--google-sheet-id", default=os.getenv("GOOGLE_SHEET_ID"), help="Google spreadsheet id.")
    parser.add_argument(
        "--google-sheet-range",
        default=os.getenv("GOOGLE_SHEET_RANGE"),
        help="A1 range to read, for example '01_Input_Raw_Products'!A:ZZ.",
    )
    parser.add_argument(
        "--google-service-account-file",
        default=os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
        help="Path to service account JSON. Defaults to GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_APPLICATION_CREDENTIALS.",
    )
    parser.add_argument(
        "--google-auth-mode",
        choices=("auto", "service-account", "public"),
        default=os.getenv("GOOGLE_SHEETS_AUTH_MODE", "auto"),
        help="auto uses credentials when present, otherwise public CSV export.",
    )
    parser.add_argument(
        "--google-timeout-seconds",
        type=float,
        default=_env_float("GOOGLE_SHEETS_TIMEOUT_SECONDS", 15.0),
        help="Timeout for Google Sheets HTTP calls.",
    )
    parser.add_argument(
        "--google-max-retries",
        type=int,
        default=_env_int("GOOGLE_SHEETS_MAX_RETRIES", 2),
        help="Retry count for transient Google Sheets API failures.",
    )
    parser.add_argument("--output", default="output/magento_import.xlsx", help="Output XLSX path.")
    args = parser.parse_args()
    if args.input_source not in {"workbook", "google-sheet"}:
        parser.error("CATALOG_INPUT_SOURCE must be workbook or google-sheet.")
    if args.google_auth_mode not in {"auto", "service-account", "public"}:
        parser.error("GOOGLE_SHEETS_AUTH_MODE must be auto, service-account, or public.")
    return args


def main() -> None:
    args = parse_args()
    reference_path = Path(args.reference)

    repo = LookupRepository.from_workbook(reference_path)
    try:
        raw_rows = _read_raw_rows(args, reference_path)
    except GoogleSheetsError as exc:
        raise SystemExit(f"Google Sheets input failed: {exc}") from exc

    output_rows, validation_rows = enrich_rows(raw_rows, repo)
    write_workbook(args.output, output_rows, validation_rows)
    ready = sum(1 for r in output_rows if r.get("validation_status") == "Ready")
    review = len(output_rows) - ready
    print(f"Generated: {args.output}")
    print(f"Rows: {len(output_rows)} | Ready: {ready} | Manual Review: {review}")


def _read_raw_rows(args: argparse.Namespace, reference_path: Path) -> list[dict[str, object]]:
    if args.input_source == "google-sheet":
        return read_google_sheet(_google_sheets_config(args))

    input_path = Path(args.input) if args.input else reference_path
    # Generated reference workbook has title rows and headers on row 3.
    if input_path == reference_path and args.input_sheet == "01_Input_Raw_Products":
        return read_reference_sheet(input_path, args.input_sheet)
    return read_sheet(input_path, args.input_sheet)


def _google_sheets_config(args: argparse.Namespace) -> GoogleSheetsConfig:
    parsed_url = parse_spreadsheet_url(args.google_sheet_url) if args.google_sheet_url else None
    spreadsheet_id = args.google_sheet_id or (parsed_url.spreadsheet_id if parsed_url else "")
    if not spreadsheet_id:
        raise GoogleSheetsConfigError("--google-sheet-id or --google-sheet-url is required.")
    if parsed_url and args.google_sheet_id and parsed_url.spreadsheet_id != args.google_sheet_id:
        raise GoogleSheetsConfigError("--google-sheet-id does not match --google-sheet-url.")

    service_account_file = Path(args.google_service_account_file) if args.google_service_account_file else None
    has_adc = bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
    public_export = args.google_auth_mode == "public" or (
        args.google_auth_mode == "auto" and service_account_file is None and not has_adc
    )
    if args.google_auth_mode == "service-account" and service_account_file is None and not has_adc:
        raise GoogleSheetsConfigError("--google-service-account-file is required for service-account mode.")

    return GoogleSheetsConfig(
        spreadsheet_id=spreadsheet_id,
        value_range=args.google_sheet_range or a1_range_for_sheet(args.input_sheet),
        service_account_file=service_account_file,
        public_export=public_export,
        sheet_gid=parsed_url.sheet_gid if parsed_url else None,
        timeout_seconds=args.google_timeout_seconds,
        max_retries=args.google_max_retries,
    )


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        raise SystemExit(f"{name} must be a number.")


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise SystemExit(f"{name} must be an integer.")


if __name__ == "__main__":
    main()
