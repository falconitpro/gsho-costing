"""Стенд для браузерной проверки PR 4: обязательства Берг-Хаус → Альянс-Импекс и Альянс-Импекс → внешний завод."""
import json, sys
sys.path.insert(0, ".")
from datetime import date, timedelta
from decimal import Decimal
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Factory, FinanceObligation, LegalEntity
from scripts.seed import run as seed
seed()
T = date.today()
with SessionLocal() as db:
    ru = db.scalar(select(LegalEntity).where(LegalEntity.code == "ru"))
    cn = db.scalar(select(LegalEntity).where(LegalEntity.code == "cn")); cn.name = "Альянс-Импекс"
    ai = Factory(name="Альянс-Импекс", country="CN", supplier_status="group_company", legal_entity_id=cn.id)
    x = Factory(name="Shandong Lingong Heavy", country="CN")
    db.add_all([ai, x]); db.flush()
    def ob(eid, d, amt, cur, on, key, kind, cid, name, due=None):
        db.add(FinanceObligation(source_type="contract", source_id=f"qa-{key}", source_line_key=key, direction=d, amount=Decimal(amt), currency=cur, legal_due_on=due or on,
                                 forecast_on=on, legal_entity_id=eid, title=f"QA: {name}", nature="contractual", is_active=True, sync_status="ok",
                                 counterparty_kind=kind, counterparty_id=cid, counterparty_name_snapshot=name))
    ob(ru.id, "out", "777000", "CNY", T + timedelta(days=6), "ru-ai-1", "factory", ai.id, "Альянс-Импекс, аванс 30 %")
    ob(ru.id, "out", "1813000", "CNY", T + timedelta(days=40), "ru-ai-2", "factory", ai.id, "Альянс-Импекс, 70 % после готовности", due=T - timedelta(days=2))
    ob(cn.id, "out", "720000", "CNY", T + timedelta(days=8), "cn-x-1", "factory", x.id, "Shandong Lingong Heavy, аванс 30 %")
    db.commit()
print("ok")
