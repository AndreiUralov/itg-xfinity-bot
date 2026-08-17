"""Historical job hints for smart suggestions."""

from __future__ import annotations

from collections import Counter
from typing import Any

from bot.storage import read_all_rows


def lookup_job_hint(job_number: str | int) -> dict[str, Any] | None:
    """Return typical work type and amount for a job number from history."""
    jn = str(job_number).strip()
    if not jn:
        return None

    rows = [r for r in read_all_rows() if str(r.get("job_number")) == jn]
    if not rows:
        return None

    totals_by_save: dict[str, float] = {}
    for row in rows:
        key = row.get("recorded_at", "")[:19]
        totals_by_save[key] = totals_by_save.get(key, 0.0) + float(row["item_total"])

    typical_total = round(sum(totals_by_save.values()) / len(totals_by_save), 2)
    work_types = Counter(r.get("work_type", "") for r in rows if r.get("work_type"))
    subtypes = Counter()
    for row in rows:
        for part in (row.get("subtype_codes") or "").split(";"):
            part = part.strip()
            if part:
                subtypes[part] += 1

    hookups = Counter(r.get("hookup_type", "") for r in rows if r.get("hookup_type"))
    addresses = Counter(r.get("address", "") for r in rows if r.get("address"))

    return {
        "job_number": jn,
        "work_type": work_types.most_common(1)[0][0] if work_types else "",
        "subtype_codes": [s for s, _ in subtypes.most_common(5)],
        "hookup_type": hookups.most_common(1)[0][0] if hookups else "",
        "address": addresses.most_common(1)[0][0] if addresses else "",
        "typical_total": typical_total,
        "times_saved": len(totals_by_save),
    }


def format_job_hint(hint: dict[str, Any]) -> str:
    wt = hint.get("work_type") or "?"
    total = hint.get("typical_total", 0)
    times = hint.get("times_saved", 1)
    suffix = f" (в базе {times}×)" if times > 1 else ""
    return f"💡 Job# {hint['job_number']} — {wt}, обычно <b>${total:.2f}</b>{suffix}"
