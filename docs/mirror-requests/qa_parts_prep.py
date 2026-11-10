"""Подготовка стенда для проверки зеркальных запросов запчастей в браузере (PR 2): компания группы, поставщик, деталь, заявка."""
import json, sys
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.db import SessionLocal
from app.models import Factory, LegalEntity, MarkupPolicy
from app.services.suppliers import mark
from scripts.seed import run as seed
H = lambda e: {"X-Dev-User": e}
seed()
with SessionLocal() as db:
    cn = db.scalar(select(LegalEntity).where(LegalEntity.code == "cn"))
    cn.name = "Альянс-Импекс"
    cn.channels = {"mail_profile": "alliance", "mail_from": "rfq@alliance-impex.example", "wecom_profile": "alliance"}
    ai = Factory(name="Альянс-Импекс", country="CN", supplier_status="group_company", legal_entity_id=cn.id, default_currency="CNY")
    x = Factory(name="Ningbo Hydraulic Parts", country="CN", default_currency="CNY", contacts=[{"name": "Chen", "email": "parts@ningbo.example", "roles": ["rfq"]}])
    db.add_all([ai, x]); db.flush()
    mark(db, ai.id, "parts"); mark(db, x.id, "parts")
    p = db.scalars(select(MarkupPolicy).order_by(MarkupPolicy.version_no.desc())).first()
    p.status = "approved"
    p.rules = {**p.rules, "default": {**(p.rules.get("default") or {}), cn.id: 12.0}}
    db.commit()
    ids = {"ai": ai.id, "x": x.id}
with TestClient(app) as c:
    cl = c.get("/api/refs/clients", headers=H("manager@example.com")).json()[0]
    c.post("/api/parts/catalog", headers=H("cd@example.com"), json={"article": "HF-6141", "name_ru": "Фильтр гидравлический", "names": {"cn": "液压滤芯"}})
    r = c.post("/api/parts/requests", headers=H("manager@example.com"), json={"client_id": cl["id"], "lines_text": "HF-6141\t\t4"}).json()
json.dump({**ids, "request_id": r["id"]}, open("prep_parts.json", "w"))
print(r["number"])
