"""Normalize Tech360 work types — Self Install is a New Install variant, not its own type."""

from __future__ import annotations

from typing import Any

SELF_INSTALL_MARKERS = ("SELF INSTALL", "RQ4", "R.Q.4.")
SELF_INSTALL_PRODUCT_CODE = "R.Q.4."


def _subtype_blob(subtype_codes: list[str] | None) -> str:
    return " ".join(str(code).upper() for code in (subtype_codes or []))


def is_new_install_self(extracted: dict[str, Any]) -> bool:
    if (extracted.get("work_type") or "").strip() != "New Install":
        return False
    blob = _subtype_blob(extracted.get("subtype_codes"))
    return any(marker in blob for marker in SELF_INSTALL_MARKERS)


def normalize_extracted(extracted: dict[str, Any]) -> dict[str, Any]:
    """Map legacy Self Install work type to New Install + Self Install subtype."""
    work_type = (extracted.get("work_type") or "").strip()
    codes = list(extracted.get("subtype_codes") or [])

    if work_type == "Self Install":
        extracted["work_type"] = "New Install"
        if not is_new_install_self(extracted):
            codes.append("Self Install")
        extracted["subtype_codes"] = codes

    return extracted
