"""Normalize Tech360 work types — Self Install is a New Install variant, not its own type."""

from __future__ import annotations

from typing import Any

SELF_INSTALL_MARKERS = ("SELF INSTALL", "SELF-INSTALL", "RQ4", "R.Q.4.")
SELF_INSTALL_PRODUCT_CODE = "R.Q.4."
DEFAULT_TROUBLE_CALL_SUBTYPE = "HSD OUT"


def _subtype_blob(subtype_codes: list[str] | None) -> str:
    return " ".join(str(code).upper() for code in (subtype_codes or []))


def is_self_install_subtypes(work_type: str, subtype_codes: list[str] | None) -> bool:
    """New Install with Self Install / RQ4 subcode — bill as R.Q.4. only (ATN)."""
    if (work_type or "").strip() != "New Install":
        return False
    blob = _subtype_blob(subtype_codes)
    return any(marker in blob for marker in SELF_INSTALL_MARKERS)


def is_new_install_self(extracted: dict[str, Any]) -> bool:
    return is_self_install_subtypes(extracted.get("work_type") or "", extracted.get("subtype_codes"))


def apply_self_install_session(extracted: dict[str, Any], session: dict[str, Any]) -> bool:
    """
    Self Install is a subcode under New Install — ATN bills R.Q.4. only (no equipment/addons).
    Clears conflicting session state when detected.
    """
    if not is_new_install_self(extracted):
        return False

    codes = list(extracted.get("subtype_codes") or [])
    if not any("SELF INSTALL" in str(code).upper() for code in codes):
        codes.append("Self Install")
    extracted["subtype_codes"] = codes
    extracted["work_type"] = "New Install"

    session["product_code"] = SELF_INSTALL_PRODUCT_CODE
    session["equipment"] = []
    session["optional_addons"] = []
    session.pop("up_install_mode", None)
    return True


def normalize_extracted(extracted: dict[str, Any]) -> dict[str, Any]:
    """Map legacy Self Install work type to New Install + Self Install subtype."""
    work_type = (extracted.get("work_type") or "").strip()
    codes = list(extracted.get("subtype_codes") or [])

    if work_type == "Self Install":
        extracted["work_type"] = "New Install"
        if not is_new_install_self(extracted):
            codes.append("Self Install")
        extracted["subtype_codes"] = codes

    if work_type == "Trouble Call" and not codes:
        codes.append(DEFAULT_TROUBLE_CALL_SUBTYPE)
        extracted["subtype_codes"] = codes

    return extracted
