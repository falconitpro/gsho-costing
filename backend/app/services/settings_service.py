"""«Ставки и нормативы» с историей: действующее значение ключа = запись с valid_to IS NULL."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RateSetting

DEFAULTS: dict[str, Any] = {
    "margin_threshold_pct": 3.0,
    "scenario_dk": {"pessimistic": 5.0, "optimistic": -3.0},
    "scenario_dt": {"pessimistic": 30, "optimistic": -10},
    "use_historical_volatility": True,
    "vol_multipliers": {"pessimistic": 1.0, "optimistic": 0.6},
    "fx_weights": {"forward": 0.4, "trend": 0.3, "expert": 0.3},
    "fx_history_years": 3,
    "key_rate_pct": 16.0,
    "pboc_rate_pct": 3.1,
    "foreign_rates_pct": {"CNY": 3.1, "USD": 4.5, "EUR": 2.5},
    "fx_expert_points": {},
    "financing_rate_pct": 18.0,
    "deposit_rate_pct": None,
    "conversion_fee_pct": 0.5,
    "transfer_fee_rub": 3000.0,
    "guarantee_rate_pct": 3.0,
    "vat_offset_days": 90,
    "import_vat_in_cost": False,
    "overhead_scale": [{"up_to_rub": 50_000_000, "pct": 5.0}, {"up_to_rub": 200_000_000, "pct": 4.0}, {"up_to_rub": None, "pct": 3.0}],
    "overhead_actual_pct_last_year": None,
    "warranty_reserve_pct_by_type": {"default": 2.0},
    "unplanned_reserve_pct": 5.0,
    "labour_rates_rub_per_hour": {"engineer_1": 3500, "engineer_2": 2800, "mechanic": 2000},
    "region_norms": {"default": {"travel_rub": 30000, "lodging_rub_per_day": 4000, "per_diem_rub": 1500, "travel_hours": 16}},
    "hours_per_month_by_type": {"default": 400},
    "cash_gap_limit_rub": None,
    "gd_deal_limit_rub": None,
    "price_deviation_pct": 10.0,
    "logistics_deviation_pct": 15.0,
    "plan_fact_deviation_pct": 10.0,
    "sla_days": {"logistics": 2, "service": 2, "cd": 1, "fd": 1, "gd": 1, "manager_rework": 1},
    "kp_fx_clause_pct": 5.0,
    "kp_validity_days": 30,
    "commissioning_days": 30,
    "approval_ladder": [
        {"name": "Коммерческая", "role": "cd", "condition": "always", "sla_days": 1},
        {"name": "Финансовая", "role": "fd", "condition": "margin_below_threshold|cash_gap_above_limit", "sla_days": 1},
        {"name": "Генеральная", "role": "gd", "condition": "deal_above_gd_limit|fd_declined|overhead_override", "sla_days": 1},
    ],
}


def get_all(db: Session) -> dict[str, Any]:
    rows = db.scalars(select(RateSetting).where(RateSetting.valid_to.is_(None))).all()
    out = dict(DEFAULTS)
    for r in rows:
        out[r.key] = r.value
    return out


def get(db: Session, key: str) -> Any:
    return get_all(db).get(key)


def set_value(db: Session, key: str, value: Any, user_id: str | None, comment: str = "") -> RateSetting:
    now = datetime.now(timezone.utc)
    for r in db.scalars(select(RateSetting).where(RateSetting.key == key, RateSetting.valid_to.is_(None))).all():
        r.valid_to = now
    row = RateSetting(key=key, value=value, changed_by=user_id, comment=comment, valid_from=now)
    db.add(row)
    return row


def history(db: Session, key: str) -> list[RateSetting]:
    return db.scalars(select(RateSetting).where(RateSetting.key == key).order_by(RateSetting.valid_from.desc())).all()
