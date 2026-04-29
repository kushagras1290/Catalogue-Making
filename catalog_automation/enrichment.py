from __future__ import annotations

from decimal import Decimal
from typing import Any

from .normalizers import clean_text, norm_key, derive_weights, slugify, to_decimal
from .repository import LookupRepository

MAGENTO_FIELDS = [
    "attribute_set_id", "product_type", "sku", "name", "meta_title", "url_key",
    "sku_for_vendor_product", "treatment", "carat_weight", "classification",
    "cutting_style", "dimensions", "j_colour", "name2", "offers", "vendor_id",
    "weight_ratti", "shipping_days", "hsn_code", "price", "special_price",
    "weight_carats", "origin", "gem_composition", "tax_class_id",
    "certification_sticker", "dispatch_days", "astro_status", "product_visibility",
    "validation_status", "validation_notes",
]

def enrich_rows(raw_rows: list[dict[str, Any]], repo: LookupRepository) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []

    for idx, raw in enumerate(raw_rows, start=1):
        if not _has_product_data(raw):
            continue
        row, issues = enrich_one(idx, raw, repo)
        sku_key = norm_key(row.get("sku"))
        if sku_key:
            if sku_key in seen:
                issues.append(("Blocker", "sku", "Duplicate SKU", "Make SKU unique before upload"))
            seen.add(sku_key)
        output.append(row)
        for severity, field, issue, fix in issues:
            validations.append({
                "row_id": idx,
                "sku": row.get("sku", ""),
                "severity": severity,
                "field": field,
                "issue": issue,
                "recommended_fix": fix,
                "status": "Open",
            })
    if not validations:
        validations.append({"row_id": "", "sku": "", "severity": "Info", "field": "all", "issue": "No validation issues found", "recommended_fix": "Proceed", "status": "Closed"})
    return output, validations

def enrich_one(row_id: int, raw: dict[str, Any], repo: LookupRepository) -> tuple[dict[str, Any], list[tuple[str, str, str, str]]]:
    issues: list[tuple[str, str, str, str]] = []

    sku = clean_text(raw.get("sku"))
    if not sku:
        issues.append(("Blocker", "sku", "Missing SKU", "Populate sku"))

    gem_type_raw = clean_text(raw.get("gem_type_raw") or raw.get("gem_type"))
    gemstone2_raw = clean_text(raw.get("gemstone2_raw") or raw.get("gemstone2"))
    gemstone_raw = clean_text(raw.get("gemstone_raw") or raw.get("gemstone"))

    gem = repo.gem_types.get(norm_key(gem_type_raw)) if gem_type_raw else None
    g2 = repo.gemstone2.get(norm_key(gemstone2_raw)) if gemstone2_raw else None

    if gem is None and g2 is None:
        issues.append(("Blocker", "gem_type_raw", "No gem_type/gemstone2 lookup match", "Add mapping in 10_Master_Gem_Types or 11_Master_Gemstone2"))

    carat, ratti = derive_weights(raw.get("carat_weight"), raw.get("ratti_weight"))
    if carat is None:
        issues.append(("Blocker", "carat_weight", "Missing carat/ratti weight", "Provide carat_weight or ratti_weight"))

    price = to_decimal(raw.get("price"))
    special_price = to_decimal(raw.get("special_price"))
    if price is None or price <= 0:
        issues.append(("Blocker", "price", "Invalid price", "Price must be greater than zero"))
    if price is not None and special_price is not None and special_price > price:
        issues.append(("Error", "special_price", "special_price exceeds price", "Fix discount price"))

    color_raw = clean_text(raw.get("certificate_colour_raw") or raw.get("j_colour"))
    color = repo.colors.get(norm_key(color_raw)) if color_raw else None

    cert_raw = clean_text(raw.get("certification_lab"))
    cert = repo.certifications.get(norm_key(cert_raw)) if cert_raw else None
    if cert_raw and cert is None:
        issues.append(("Warning", "certification_lab", "Certification sticker not mapped", "Add mapping in 13_Certification_Stickers"))

    shipping_raw = clean_text(raw.get("shipping_days"))
    shipping = repo.shipping.get(norm_key(shipping_raw)) if shipping_raw else None
    if shipping_raw and shipping is None:
        issues.append(("Warning", "shipping_days", "Shipping rule not mapped", "Add mapping in 14_Shipping_Rules"))

    gemstone = _first(raw.get("gemstone_raw"), _val(gem, "gemstone"), _val(g2, "gemstone"))
    astro = repo.astro.get(norm_key(gemstone)) if gemstone else None

    product_type = _first(raw.get("product_type"), _val(gem, "product_type"), _val(g2, "default_product_type"), "Single Stone")
    treatment = _first(raw.get("treatment_raw"), _val(gem, "treatment"), _val(g2, "default_treatment"))
    cutting_style = _first(raw.get("cutting_style_raw"), _val(gem, "cutting_style"), _val(g2, "default_cutting_style"))
    classification = _first(_val(gem, "classification"), _val(g2, "default_classification"))
    hsn_code = _first(_val(gem, "hsn_code"), _val(g2, "default_hsn_code"))
    tax_class_id = _first(_val(gem, "tax_class_id"), _val(g2, "default_tax_class_id"), repo.get_config("default_tax_class_id"))
    composition = _first(_val(gem, "gem_composition"), _val(g2, "default_composition"), "Natural")
    origin = _first(raw.get("origin_raw"), _val(gem, "origin"), _val(g2, "default_origin"))
    j_colour = _first(_val(color, "j_colour"), _val(gem, "j_colour"))
    base_name = _first(gem_type_raw, gemstone2_raw, gemstone_raw, gemstone, "Gemstone")
    shape = clean_text(raw.get("shape_raw"))
    url_key = _first(raw.get("manual_url_key"), _val(gem, "url_key"), _val(g2, "default_url_key"), slugify(base_name))

    required_lookup_fields = {
        "url_key": url_key,
        "classification": classification,
        "j_colour": j_colour,
        "hsn_code": hsn_code,
        "tax_class_id": tax_class_id,
    }
    for field, value in required_lookup_fields.items():
        if not clean_text(value):
            issues.append(("Error", field, f"Missing {field}", f"Fix source lookup for {base_name}"))

    name = build_name(carat, base_name, shape, product_type)
    validation_status = "Ready" if not any(s in {"Blocker", "Error"} for s, *_ in issues) else "Manual Review"
    validation_notes = "; ".join(f"{field}: {issue}" for _, field, issue, _ in issues)

    row = {
        "attribute_set_id": repo.get_config("default_attribute_set_id", "20"),
        "product_type": product_type,
        "sku": sku,
        "name": name,
        "meta_title": f"{name} | GemPundit" if name else "",
        "url_key": url_key,
        "sku_for_vendor_product": clean_text(raw.get("vendor_sku")),
        "treatment": treatment,
        "carat_weight": _dec_str(carat),
        "classification": classification,
        "cutting_style": cutting_style,
        "dimensions": clean_text(raw.get("dimensions")),
        "j_colour": j_colour,
        "name2": base_name,
        "offers": "Catalog Automation",
        "vendor_id": clean_text(raw.get("vendor_id")),
        "weight_ratti": _dec_str(ratti),
        "shipping_days": shipping_raw,
        "hsn_code": hsn_code,
        "price": _dec_str(price, places=0),
        "special_price": _dec_str(special_price, places=0),
        "weight_carats": _dec_str(carat),
        "origin": origin,
        "gem_composition": composition,
        "tax_class_id": tax_class_id,
        "certification_sticker": _val(cert, "sticker_slug"),
        "dispatch_days": _val(shipping, "dispatch_text"),
        "astro_status": _val(astro, "astro_status"),
        "product_visibility": repo.get_config("default_product_visibility", "Catalog, Search"),
        "validation_status": validation_status,
        "validation_notes": validation_notes,
    }
    # Keep stable order
    return {field: row.get(field, "") for field in MAGENTO_FIELDS}, issues

def build_name(carat: Decimal | None, base_name: str, shape: str, product_type: str) -> str:
    parts = []
    if carat is not None:
        parts.append(f"{carat:.2f} Ct")
    parts.append(base_name)
    if shape:
        parts.append(shape)
    if norm_key(product_type) == "pair":
        parts.append("Pair")
    elif norm_key(product_type) not in {"beads", "rough", "set"}:
        parts.append("Gemstone")
    return " ".join(clean_text(p) for p in parts if clean_text(p))

def _val(row: dict[str, Any] | None, key: str) -> str:
    if not row:
        return ""
    return clean_text(row.get(key))

def _first(*values: Any) -> str:
    for value in values:
        text = clean_text(value)
        if text:
            return text
    return ""

def _dec_str(value: Decimal | None, places: int = 2) -> str:
    if value is None:
        return ""
    if places == 0:
        return str(value.quantize(Decimal("1")))
    return f"{value:.{places}f}"

def _has_product_data(raw: dict[str, Any]) -> bool:
    keys = ["sku", "gem_type_raw", "gemstone_raw", "gemstone2_raw", "price", "carat_weight", "ratti_weight"]
    return any(clean_text(raw.get(k)) for k in keys)
