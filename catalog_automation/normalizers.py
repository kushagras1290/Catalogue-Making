import re
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional

SPACE_RE = re.compile(r"\s+")
SLUG_BAD_RE = re.compile(r"[^a-z0-9]+")

def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = unicodedata.normalize("NFKC", text)
    return SPACE_RE.sub(" ", text)

def norm_key(value: Any) -> str:
    return clean_text(value).casefold()

def to_decimal(value: Any) -> Optional[Decimal]:
    text = clean_text(value).replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None

def q2(value: Optional[Decimal]) -> Optional[Decimal]:
    if value is None:
        return None
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def derive_weights(carat_value: Any, ratti_value: Any) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """Return `(carat, ratti)` using GemPundit conversion rules.

    Business rule:
    - 1 ratti = 0.91 carat
    - 1 carat = 1.1 ratti
    """
    carat = to_decimal(carat_value)
    ratti = to_decimal(ratti_value)

    if carat is None and ratti is not None:
        carat = ratti * Decimal("0.91")
    if ratti is None and carat is not None:
        ratti = carat * Decimal("1.1")
    return q2(carat), q2(ratti)

def slugify(value: Any) -> str:
    text = clean_text(value).lower()
    text = text.replace("&", " and ")
    text = SLUG_BAD_RE.sub("-", text).strip("-")
    text = re.sub(r"-+", "-", text)
    return text

def boolish(value: Any, default: bool = True) -> bool:
    text = norm_key(value)
    if text in {"false", "0", "no", "n", "inactive", "disabled"}:
        return False
    if text in {"true", "1", "yes", "y", "active", "enabled"}:
        return True
    return default
