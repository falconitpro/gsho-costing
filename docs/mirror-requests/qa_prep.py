import json, sys
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.db import SessionLocal
from app.models import Factory, LegalEntity, MarkupPolicy
from tests.test_mirror_offers import _deal, RUN
with SessionLocal() as db:
    cn = db.scalar(select(LegalEntity).where(LegalEntity.code == "cn"))
    cn.name = "Альянс-Импекс"
    cn.channels = {"mail_profile": "alliance", "mail_from": "rfq@alliance-impex.example", "wecom_profile": "alliance"}
    ai = Factory(name="Альянс-Импекс", country="CN", supplier_status="group_company", legal_entity_id=cn.id, default_currency="CNY")
    x = Factory(name="Shandong Lingong Heavy", country="CN", default_currency="CNY", contacts=[{"name": "Li", "email": "sales@lingong.example", "roles": ["rfq"]}])
    y = Factory(name="Henan Yuchai Machinery", country="CN", default_currency="CNY", contacts=[{"name": "Wang", "email": "export@yuchai.example"}])
    db.add_all([ai, x, y])
    p = db.scalars(select(MarkupPolicy).order_by(MarkupPolicy.version_no.desc())).first()
    p.status = "approved"
    p.rules = {**p.rules, "gsho_default": {cn.id: 7.0}, "gsho_by_machine_type": [{"entity_id": cn.id, "machine_type": "Самосвал", "pct": 9.0}], "gsho_by_supplier": []}
    db.commit()
    ids = {"ai": ai.id, "x": x.id, "y": y.id}
with TestClient(app) as c:
    did, pos_id = _deal(c)
json.dump({**ids, "deal_id": did, "pos_id": pos_id}, open("prep.json", "w"))
print(did)
