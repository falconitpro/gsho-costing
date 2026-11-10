"""Стенд для браузерной проверки рамочных договоров: рамочный договор с поставщиком и сделка на стадии контракта."""
import json, sys
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
from app.db import SessionLocal
from app.main import app
from app.models import Factory
from scripts.seed import run as seed
sys.path.insert(0, "tests")
from test_framework_contracts import _deal, H, CD
seed()
with SessionLocal() as db:
    f = Factory(name="Shandong Lingong Heavy", country="CN", default_currency="CNY"); db.add(f); db.commit(); fid = f.id
with TestClient(app) as c:
    k = c.post("/api/legal/contracts", headers=H(CD), json={"kind": "supplier", "cp_kind": "factory", "cp_id": fid, "subject": "рамочный договор поставки ГШО",
                                                           "amount": 12_000_000, "currency": "CNY"}).json()
    did = _deal(c, fid, 2_400_000)
json.dump({"deal_id": did, "contract_id": k["id"]}, open("prep_framework.json", "w")); print(did)
