"""Parse quick text job entry without a screenshot."""

from __future__ import annotations

import re
from typing import Any

WORK_TYPE_ALIASES: dict[str, str] = {
    "trouble": "Trouble Call",
    "trouble call": "Trouble Call",
    "tc": "Trouble Call",
    "t": "Trouble Call",
    "service": "Service Change",
    "service change": "Service Change",
    "sc": "Service Change",
    "change": "Service Change",
    "install": "New Install",
    "new install": "New Install",
    "ni": "New Install",
    "self": "Self Install",
    "self install": "Self Install",
    "si": "Self Install",
    "special": "Special Request",
    "special request": "Special Request",
    "sr": "Special Request",
}

HOOKUP_ALIASES = {
    "aerial": "Aerial",
    "underground": "Underground",
    "ug": "Underground",
}


def parse_quick_input(text: str) -> dict[str, Any] | None:
    """
    Examples:
      549110 trouble
      job 508836 tc aerial
      498945 service change
    """
    raw = text.strip()
    if not raw or raw.startswith("/"):
        return None

    lower = raw.lower()
    if lower.startswith("job "):
        lower = lower[4:].strip()
        raw = raw[4:].strip()

    m = re.match(r"^(\d{5,7})\b(.*)$", lower)
    if not m:
        return None

    job_number = m.group(1)
    rest = m.group(2).strip()

    result: dict[str, Any] = {"job_number": job_number, "subtype_codes": []}

    if not rest:
        return result

    for alias in sorted(WORK_TYPE_ALIASES, key=len, reverse=True):
        if rest == alias or rest.startswith(alias + " "):
            result["work_type"] = WORK_TYPE_ALIASES[alias]
            rest = rest[len(alias) :].strip()
            break

    for alias in sorted(HOOKUP_ALIASES, key=len, reverse=True):
        if rest == alias or rest.endswith(" " + alias):
            result["hookup_type"] = HOOKUP_ALIASES[alias]
            if rest == alias:
                rest = ""
            else:
                rest = rest[: -len(alias)].strip()
            break

    if rest and not result.get("work_type"):
        for alias, work_type in WORK_TYPE_ALIASES.items():
            if alias in rest:
                result["work_type"] = work_type
                break

    return result
