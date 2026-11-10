import json, sys
from datetime import date, datetime, timezone
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
from app.main import app
from tests.test_finance_api import H, _own_bank, _signed_client_contract, FD, LAW, RUN
with TestClient(app) as c:
    k = _signed_client_contract(c, 500000, [{"share_pct": 40, "event": "contract", "offset_days": 3, "offset_kind": "working"},
                                            {"share_pct": 60, "event": "contract", "offset_days": 20, "offset_kind": "calendar"}])
    c.post(f"/api/legal/contracts/{k['id']}/facts", headers=H(FD), json={"kind": "pay", "key": "2", "paid_on": date.today().isoformat()})
    ent_id = c.get("/api/legal/contracts", headers=H(LAW)).json()["entities"][0]["id"]
    acct = c.post("/api/finance/accounts", headers=H(FD), json={"legal_entity_id": ent_id, "bank_profile_id": _own_bank(c, ent_id, "RUB"), "title": "Расчётный RUB"}).json()
    t = c.post("/api/finance/transactions", headers=H(FD, f"q-tx-{RUN}"), json={"account_id": acct["id"], "direction": "in", "amount": "77000.00", "currency": "RUB",
        "booked_at": datetime.now(timezone.utc).isoformat(), "counterparty_name": "АО «Рудник»", "purpose": "оплата по счёту 15", "external_ref": "п/п 881"}).json()
    c.post(f"/api/finance/transactions/{t['id']}/post", headers=H(FD, f"q-post-{RUN}"))
    json.dump({"tx": t["id"], "number": k["number"]}, open("prep.json", "w"))
    print(k["number"])
