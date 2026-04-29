from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .excel_io import read_reference_sheet
from .normalizers import norm_key, boolish, clean_text

@dataclass(frozen=True)
class LookupRepository:
    gem_types: dict[str, dict[str, Any]]
    gemstone2: dict[str, dict[str, Any]]
    colors: dict[str, dict[str, Any]]
    certifications: dict[str, dict[str, Any]]
    shipping: dict[str, dict[str, Any]]
    astro: dict[str, dict[str, Any]]
    config: dict[str, str]
    naming_rules: list[dict[str, Any]]

    @classmethod
    def from_workbook(cls, path: str | Path) -> "LookupRepository":
        gem_types = _keyed(read_reference_sheet(path, "10_Master_Gem_Types"), "gem_type")
        gemstone2 = _keyed(read_reference_sheet(path, "11_Master_Gemstone2"), "gemstone2")
        colors = _keyed(read_reference_sheet(path, "12_Color_Mapping"), "certificate_colour_raw")
        certifications = _keyed(read_reference_sheet(path, "13_Certification_Stickers"), "certification_lab")
        shipping = _keyed(read_reference_sheet(path, "14_Shipping_Rules"), "shipping_days")
        astro = _keyed(read_reference_sheet(path, "16_Astro_Flags"), "gemstone")
        config_rows = read_reference_sheet(path, "20_Admin_Config")
        config = {clean_text(r.get("key")): clean_text(r.get("value")) for r in config_rows if clean_text(r.get("key"))}
        naming_rules = [r for r in read_reference_sheet(path, "19_Naming_Rules") if boolish(r.get("is_active"), True)]
        return cls(gem_types, gemstone2, colors, certifications, shipping, astro, config, naming_rules)

    def get_config(self, key: str, default: str = "") -> str:
        return self.config.get(key, default)

def _keyed(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not boolish(row.get("is_active"), True):
            continue
        value = row.get(key)
        nkey = norm_key(value)
        if nkey and nkey not in result:
            result[nkey] = row
    return result
