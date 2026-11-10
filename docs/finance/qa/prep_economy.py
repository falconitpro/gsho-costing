import json, sys
sys.path.insert(0, ".")
from tests import test_finance_economy as T
from fastapi.testclient import TestClient
from app.main import app
H = T.H
with TestClient(app) as c:
    did, var_id, pos_id = T._deal(c)
    c.post(f"/api/deals/{did}/fact", headers=H(T.LOG), json={"kind": "cost_item", "cost_item_code": "intl_freight", "amount": 1_600_000, "on": T.TODAY.isoformat()})
    obs = {o["source"]["line_key"]: o for o in c.get(f"/api/finance/obligations?deal_id={did}&limit=100", headers=H(T.FD)).json()["items"]}
    acct = T._account(c)
    T._pay(c, acct, "out", "1900000.00", 10, obs[f"factory:{pos_id}:1"]["id"], "out1")
    T._pay(c, acct, "in", "3000000.00", 5, obs[f"client:{var_id}:1"]["id"], "in1")
    num = c.get(f"/api/deals/{did}", headers=H(T.FD)).json()["number"]
    json.dump({"deal_id": did, "number": num}, open("prep.json", "w"))
    print(did, num)
