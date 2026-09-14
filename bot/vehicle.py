"""Per-user vehicle arrangement — set once, applied to every week until changed.

Only ATG rental is deducted on the ATN invoice. Own car, credit, and non-ATG
rental are personal costs and must not be subtracted when comparing with ATN.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

OWNED = "owned"
CREDIT = "credit"
RENTAL_ATG = "rental_atg"
RENTAL_OTHER = "rental_other"

VEHICLE_KINDS = (OWNED, CREDIT, RENTAL_ATG, RENTAL_OTHER)

DEFAULT_ATG_WEEKLY = 150.0

KIND_LABELS_RU = {
    OWNED: "своя машина",
    CREDIT: "кредит",
    RENTAL_ATG: "аренда ATG",
    RENTAL_OTHER: "аренда (не ATG)",
}


@dataclass(frozen=True)
class VehicleSettings:
    kind: str
    weekly_amount: float = 0.0
    configured: bool = False

    @property
    def deducts_on_invoice(self) -> bool:
        return self.kind == RENTAL_ATG and self.weekly_amount > 0

    @property
    def invoice_truck(self) -> float:
        return round(self.weekly_amount, 2) if self.deducts_on_invoice else 0.0

    @property
    def personal_weekly(self) -> float:
        if self.kind in (CREDIT, RENTAL_OTHER) and self.weekly_amount > 0:
            return round(self.weekly_amount, 2)
        return 0.0


def default_vehicle_settings() -> VehicleSettings:
    """Backward-compatible default: ATG rental $150/week, until the user sets their own."""
    return VehicleSettings(kind=RENTAL_ATG, weekly_amount=DEFAULT_ATG_WEEKLY, configured=False)


def _settings_key(telegram_user_id: int) -> str:
    from bot.users import user_settings_key

    return f"vehicle_{user_settings_key(telegram_user_id)}"


def get_vehicle_settings(telegram_user_id: int | None) -> VehicleSettings:
    if not telegram_user_id:
        return default_vehicle_settings()

    from bot.settings_store import get_setting

    raw = get_setting(_settings_key(telegram_user_id))
    if not raw:
        return default_vehicle_settings()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return default_vehicle_settings()

    kind = str(data.get("kind") or "")
    if kind not in VEHICLE_KINDS:
        return default_vehicle_settings()
    try:
        amount = round(float(data.get("weekly_amount") or 0), 2)
    except (TypeError, ValueError):
        amount = 0.0
    if kind == OWNED:
        amount = 0.0
    if kind == RENTAL_ATG and amount <= 0:
        amount = DEFAULT_ATG_WEEKLY
    return VehicleSettings(kind=kind, weekly_amount=max(0.0, amount), configured=True)


def set_vehicle_settings(telegram_user_id: int, kind: str, weekly_amount: float = 0.0) -> VehicleSettings:
    if kind not in VEHICLE_KINDS:
        raise ValueError(f"Unknown vehicle kind: {kind}")
    amount = 0.0 if kind == OWNED else round(max(0.0, float(weekly_amount)), 2)
    if kind == RENTAL_ATG and amount <= 0:
        amount = DEFAULT_ATG_WEEKLY

    from bot.settings_store import set_setting

    payload = json.dumps({"kind": kind, "weekly_amount": amount}, ensure_ascii=False)
    set_setting(_settings_key(telegram_user_id), payload)
    return VehicleSettings(kind=kind, weekly_amount=amount, configured=True)


def invoice_truck_deduction(
    settings: VehicleSettings,
    *,
    full_week: bool = True,
    db: dict | None = None,
) -> float:
    """Amount subtracted as Truck on the ATN invoice. Zero unless ATG rental."""
    if not settings.deducts_on_invoice:
        return 0.0
    amount = settings.invoice_truck
    if full_week:
        return amount
    truck_cfg = (db or {}).get("deductions", {}).get("truck", {})
    full = float(truck_cfg.get("full_week") or DEFAULT_ATG_WEEKLY)
    partial = float(truck_cfg.get("partial_week_example") or 0)
    if full <= 0:
        return amount
    return round(amount * (partial / full), 2)


def resolve_invoice_truck(
    telegram_user_id: int | None,
    *,
    full_week: bool = True,
    db: dict | None = None,
) -> float:
    settings = get_vehicle_settings(telegram_user_id)
    return invoice_truck_deduction(settings, full_week=full_week, db=db)


def format_vehicle_line(settings: VehicleSettings) -> str:
    label = KIND_LABELS_RU.get(settings.kind, settings.kind)
    unset = "" if settings.configured else " <i>(пока по умолчанию)</i>"
    if settings.kind == OWNED:
        return f"🚗 {label} · без платежа{unset}"
    amount = f"${settings.weekly_amount:,.2f}/нед"
    if settings.deducts_on_invoice:
        return f"🚗 {label} · {amount} · вычет в инвойсе ATN{unset}"
    return f"🚗 {label} · {amount} · не в инвойсе ATN{unset}"
