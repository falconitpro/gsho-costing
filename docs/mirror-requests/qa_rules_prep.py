"""Стенд для браузерной проверки правил цепочек: Альянс-Импекс, поставщик из Китая, сделка с позицией."""
import json, sys
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.db import SessionLocal
from app.main import app
from app.models import Factory, LegalEntity, MarkupPolicy, Position
from scripts.seed import run as seed
H = lambda e: {"X-Dev-User": e}
seed()
with SessionLocal() as db:
    cn = db.scalar(select(LegalEntity).where(LegalEntity.code == "cn")); cn.name = "Альянс-Импекс"
    db.add_all([Factory(name="Альянс-Импекс", country="CN", supplier_status="group_company", legal_entity_id=cn.id, default_currency="CNY"),
                Factory(name="Shandong Lingong Heavy", country="CN", default_currency="CNY", contacts=[{"name": "Li", "email": "sales@lingong.example", "roles": ["rfq"]}])])
    p = db.scalars(select(MarkupPolicy).order_by(MarkupPolicy.version_no.desc())).first(); p.status = "approved"
    p.rules = {**p.rules, "gsho_default": {cn.id: 8.0}}
    db.commit()
    x = db.scalar(select(Factory).where(Factory.name == "Shandong Lingong Heavy"))
with TestClient(app) as c:
    cl = c.get("/api/refs/clients", headers=H("manager@example.com")).json()[0]
    did = c.post("/api/deals", headers=H("manager@example.com"), json={"client_id": cl["id"], "site": "карьер «Северный»"}).json()["id"]
    with SessionLocal() as db:
        pos = Position(deal_id=did, factory_id=x.id, model="ZK-90", machine_type="Самосвал", qty=2, unit_price=1, currency="CNY"); db.add(pos); db.commit(); pid = pos.id
json.dump({"deal_id": did, "pos_id": pid}, open("prep_rules.json", "w")); print(did)
