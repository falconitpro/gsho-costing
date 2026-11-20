"""История логистики в одном режиме (D-111): все суммы — на 1 машину, как вводятся с D-087 / D-105.

До этого в истории были вперемешку суммы на всю партию (позицию) и на 1 машину, а проверка «отклонение от истории»
сравнивала их между собой (и суммы в разных валютах) — отсюда ложные требования комментария. Теперь:
- по каждой сделке в истории — значения последнего завершённого расчёта логистики (повторное завершение после доработки
  заменяет записи сделки, а не добавляет дубли);
- проверка сравнивает только суммы в той же валюте;
- разово после обновления история пересобирается из текущих данных сделок (суммы уже на 1 машину после 031);
  дата записи — дата первого расчёта сделки.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import Deal, LogisticsHistory, RateSetting

MARK = "_migration_logistics_history_v2"


def _rows(deal: Deal, on: date, vat_pct: float = 22.0) -> list[LogisticsHistory]:
    from . import logistics_vat as lv
    out = []
    for b in deal.batches:
        it = b.items or {}
        machines = sum(max(1, int(p.qty or 1)) for p in deal.positions if p.batch_id == b.id) or 1
        for key in ("intl_freight", "broker_rub", "rf_delivery_rub"):
            val = it.get(key)
            amount, cur = (val.get("amount"), val.get("currency")) if isinstance(val, dict) else (val, "RUB")
            try:
                amount = float(amount) if amount not in (None, "") else 0.0
            except (TypeError, ValueError):
                amount = 0.0
            if amount and key in lv.BATCH_ITEMS and lv.included(it, key):   # D-122: в историю — без НДС
                amount = lv.split(amount, True, vat_pct)[0]
            if amount:
                if not it.get("per_unit"):           # запись старого режима, не переведённая 031, — на всю партию
                    amount = amount / machines
                out.append(LogisticsHistory(item_code=key, route=b.route, region=deal.region, amount=round(amount, 2), currency=(cur or "RUB").upper(),
                                            per_unit=round(amount * machines / max(b.transport_units or 1, 1), 2), on=on, deal_id=deal.id, batch_id=b.id))
    for p in deal.positions:
        lg = p.logistics or {}
        n = max(1, int(p.qty or 1))
        for key in ("domestic_logistics", "export_formalities"):
            val = lg.get(key) or {}
            try:
                amount = float(val.get("amount") or 0) if isinstance(val, dict) else 0.0
            except (TypeError, ValueError):
                amount = 0.0
            if amount:
                if not lg.get("per_unit"):
                    amount = amount / n
                out.append(LogisticsHistory(item_code=key, machine_type=p.machine_type, amount=round(amount, 2),
                                            currency=(val.get("currency") or "CNY").upper(), on=on, deal_id=deal.id))
    return out


def record_deal(db: Session, deal: Deal, on: Optional[date] = None) -> int:
    """Записи сделки в истории — по текущему расчёту логистики (прежние записи сделки заменяются)."""
    db.execute(delete(LogisticsHistory).where(LogisticsHistory.deal_id == deal.id, LogisticsHistory.source == "plan"))
    from . import settings_service
    rows = _rows(deal, on or date.today(), float(settings_service.get(db, "sales_vat_pct") or 22.0))
    db.add_all(rows)
    return len(rows)


def rebuild_once(db: Session) -> int:
    """Разово: история всех сделок, у которых она есть, — заново из текущих данных (на 1 машину), дата — первая запись сделки."""
    if db.scalar(select(RateSetting.id).where(RateSetting.key == MARK).limit(1)):
        return 0
    firsts: dict[str, date] = {}
    for r in db.scalars(select(LogisticsHistory).where(LogisticsHistory.deal_id.is_not(None), LogisticsHistory.source == "plan")).all():
        firsts[r.deal_id] = min(firsts.get(r.deal_id, r.on), r.on)
    n = 0
    for did, on in firsts.items():
        deal = db.get(Deal, did)
        if deal is None:
            continue
        n += record_deal(db, deal, on)
    now = datetime.now(timezone.utc)   # закрытая запись — служебная отметка, в настройки не попадает
    db.add(RateSetting(key=MARK, value=True, comment="история логистики — на 1 машину (D-111)", valid_from=now, valid_to=now))
    db.commit()
    return n
