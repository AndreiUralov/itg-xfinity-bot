"""Resolve ATN Work Area (Broward / Miami) from job address."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "work_areas.json"

ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


def _load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _normalize(address: str) -> str:
    return re.sub(r"\s+", " ", address.upper().strip())


def _extract_zip(address: str) -> str | None:
    match = ZIP_RE.search(address)
    return match.group(1) if match else None


def _match_zip(zip_code: str, area_cfg: dict) -> bool:
    if zip_code in area_cfg.get("zip_codes", []):
        return True
    for prefix in area_cfg.get("zip_prefixes", []):
        if zip_code.startswith(prefix):
            return True
    return False


def _match_city(address: str, area_cfg: dict) -> bool:
    cities = sorted(area_cfg.get("cities", []), key=len, reverse=True)
    for city in cities:
        if city in address:
            return True
    return False


def resolve_work_area(address: str, default: str | None = None) -> tuple[str, str]:
    """
    Returns (work_area, source) where source is zip | city | default | ambiguous.
    """
    cfg = _load_config()
    fallback = default or cfg.get("default", "Broward")
    normalized = _normalize(address)

    zip_code = _extract_zip(normalized)
    if zip_code:
        matches = []
        for area_name, area_cfg in cfg["areas"].items():
            if _match_zip(zip_code, area_cfg):
                matches.append(area_name)
        if len(matches) == 1:
            return matches[0], "zip"
        if len(matches) > 1:
            # Prefer longer / more specific — rare for zip
            return matches[0], "zip"

    city_matches = []
    for area_name, area_cfg in cfg["areas"].items():
        if _match_city(normalized, area_cfg):
            city_matches.append(area_name)

    if len(city_matches) == 1:
        return city_matches[0], "city"
    if len(city_matches) > 1:
        # e.g. address mentions both — prefer Broward for border cities if zip missing
        if "Broward" in city_matches:
            return "Broward", "city"
        return city_matches[0], "city"

    return fallback, "default"


def is_confident(source: str) -> bool:
    return source in ("zip", "city")
