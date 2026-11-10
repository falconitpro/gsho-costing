import json, sys
from datetime import date, timedelta
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
from app.main import app
H = lambda e: {"X-Dev-User": e}
LAW, LOG, MGR = "lawyer@example.com", "logist@example.com", "manager@example.com"
today = date.today()
with TestClient(app) as c:
    ent = c.get("/api/legal/contracts", headers=H(LAW)).json()["entities"][0]
    k = c.post("/api/legal/contracts", headers=H(LOG), json={"kind": "logistics", "own_entity_id": ent["id"], "cp_name": "ООО «ТрансЛайн»", "cp_inn": "7700000098",
        "subject": "перевозка", "amount": 1000000, "currency": "RUB", "signatory": {"name": "Иванов И. И.", "basis": "Устав"}, "signed_on": (today - timedelta(days=40)).isoformat(),
        "payment_terms": [{"share_pct": 100, "event": "contract", "offset_days": 10, "offset_kind": "calendar"}],
        "performance_terms": [{"code": "delivery", "event": "contract", "offset_days": 20, "offset_kind": "calendar"}],
        "security": {"kind": "bank_guarantee", "amount": 100000, "currency": "RUB", "valid_to": (today + timedelta(days=12)).isoformat(), "bank": "ПАО Банк"}}).json()
    c.post(f"/api/legal/contracts/{k['id']}/files", headers=H(LOG), files={"file": ("scan.pdf", b"%PDF-1.4 x", "application/pdf")}, data={"kind": "signed_scan"})
    c.post(f"/api/legal/contracts/{k['id']}/status", headers=H(LAW), json={"status": "active"})
    ob = c.get(f"/api/legal/contracts/{k['id']}/obligations", headers=H(LAW)).json()
    rec = next(p for p in ob["penalties"] if p["payer"] == "cp")
    c.post(f"/api/legal/contracts/{k['id']}/facts", headers=H(LAW), json={"kind": "pen", "key": rec["key"], "status": "claimed", "amount": 18000})
    from app.db import SessionLocal
    from app.models import Deal, ExecutionStage
    cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
    d = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "Карьер Южный"}).json()
    with SessionLocal() as db:
        deal = db.get(Deal, d["id"])
        from app.core.workflow import DealStatus
        deal.status = DealStatus.CONTRACT
        deal.bank_guarantee = {"required": True, "amount": 500000, "currency": "RUB", "until": (today + timedelta(days=90)).isoformat(), "bank": "АО Банк"}
        db.add(ExecutionStage(deal_id=deal.id, code="lc_opened", title="Открытие аккредитива клиентом", sort=40, amount=7000000, currency="RUB", responsible_roles=["fd"], planned_on=today + timedelta(days=5)))
        db.commit()
    json.dump({"contract_id": k["id"], "number": k["number"], "deal_id": d["id"]}, open("prep.json", "w"))
    print(k["number"])
