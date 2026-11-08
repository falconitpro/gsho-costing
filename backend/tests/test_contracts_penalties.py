"""Реестр договоров (этап 3): обязательства по оплате и исполнению, просрочки от фактической даты события, расчёт неустоек
с пределом, решения по неустойкам, неустойки и платежи договоров в денежном потоке."""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
FD, LAW, LOG = "fd@example.com", "lawyer@example.com", "logist@example.com"
PDF = ("scan.pdf", b"%PDF-1.4 test", "application/pdf")


def test_contract_obligations_and_penalties():
    seed()
    today = date.today()
    with TestClient(app) as c:
        ent = c.get("/api/legal/contracts", headers=H(LAW)).json()["entities"][0]
        k = c.post("/api/legal/contracts", headers=H(LOG), json={
            "kind": "logistics", "own_entity_id": ent["id"], "cp_name": "ООО «Перевозчик-Пени»", "cp_inn": "7700000099", "subject": "перевозка", "amount": 1000000, "currency": "RUB",
            "signatory": {"name": "Иванов И. И.", "basis": "Устав"}, "signed_on": (today - timedelta(days=40)).isoformat(),
            "payment_terms": [{"share_pct": 100, "event": "contract", "offset_days": 10, "offset_kind": "calendar"}],
            "performance_terms": [{"code": "delivery", "event": "contract", "offset_days": 20, "offset_kind": "calendar"}]}).json()
        cid = k["id"]
        assert c.post(f"/api/legal/contracts/{cid}/facts", headers=H(LAW), json={"kind": "pay", "key": "1", "paid_on": today.isoformat()}).status_code == 409   # не подписан
        c.post(f"/api/legal/contracts/{cid}/files", headers=H(LOG), files={"file": PDF}, data={"kind": "signed_scan"})
        k = c.post(f"/api/legal/contracts/{cid}/status", headers=H(LAW), json={"status": "active"}).json()
        assert k["status"] == "active"

        ob = c.get(f"/api/legal/contracts/{cid}/obligations", headers=H(LOG)).json()
        pay, perf = ob["payments"][0], ob["performance"][0]
        assert pay["status"] == "overdue" and pay["days_late"] == 30 and pay["debtor"] == "us"
        assert perf["status"] == "overdue" and perf["days_late"] == 20 and perf["debtor"] == "cp"
        pens = {p["reason"]: p for p in ob["penalties"]}
        assert pens["late_payment"]["payer"] == "us" and pens["late_payment"]["amount"] == 30000 and pens["late_payment"]["status"] == "accruing"     # 0,1 % × 30 дн.
        assert pens["late_delivery"]["payer"] == "cp" and pens["late_delivery"]["amount"] == 20000                                                  # 0,1 % × 20 дн.
        assert ob["totals"]["payable_accruing"] == 30000 and ob["totals"]["receivable_accruing"] == 20000

        # исполнено с просрочкой → неустойка начислена окончательно
        c.post(f"/api/legal/contracts/{cid}/facts", headers=H(LOG), json={"kind": "perf", "key": "0", "done_on": (today - timedelta(days=10)).isoformat()})
        k = c.post(f"/api/legal/contracts/{cid}/facts", headers=H(FD), json={"kind": "pay", "key": "1", "paid_on": (today - timedelta(days=25)).isoformat()}).json()
        pens = {p["reason"]: p for p in k["obligations"]["penalties"]}
        assert pens["late_delivery"]["amount"] == 10000 and pens["late_delivery"]["status"] == "accrued"
        assert pens["late_payment"]["amount"] == 5000 and pens["late_payment"]["status"] == "accrued"

        # решения по неустойкам — юрист, ФД, ЗГД; «не взыскиваем» — с причиной
        key = pens["late_delivery"]["key"]
        assert c.post(f"/api/legal/contracts/{cid}/facts", headers=H(LOG), json={"kind": "pen", "key": key, "status": "claimed"}).status_code == 403
        assert c.post(f"/api/legal/contracts/{cid}/facts", headers=H(LAW), json={"kind": "pen", "key": key, "status": "waived"}).status_code == 422
        k = c.post(f"/api/legal/contracts/{cid}/facts", headers=H(LAW), json={"kind": "pen", "key": key, "status": "recognized", "amount": 9000, "comment": "согласовали скидку"}).json()
        p = next(x for x in k["obligations"]["penalties"] if x["key"] == key)
        assert p["status"] == "recognized" and p["amount"] == 9000
        assert k["obligations"]["totals"]["receivable"] == 9000 and k["obligations"]["totals"]["payable"] == 5000 and k["obligations"]["totals"]["net"] == 4000

        # денежный поток: признанная неустойка к получению; оплаченный платёж договора не попадает
        m = c.get("/api/summary/money", headers=H(FD)).json()
        mine = [r for r in m["rows"] if r.get("deal_number") == k["number"]]
        assert any(r["source"] == "penalty" and r["direction"] == "in" and r["amount"] == 9000 for r in mine)
        assert not any(r["source"] == "contract" for r in mine)
        reg = c.get("/api/legal/contracts?q=Перевозчик-Пени", headers=H(LAW)).json()
        assert reg["items"][0]["exec"]["pen_in"] == 9000 and reg["summary"]["penalties_out_rub"] >= 5000
        assert c.get("/api/workplace", headers=H(FD)).status_code == 200

        # общий предел ответственности: 10 % цены
        k = c.post(f"/api/legal/contracts/{cid}/facts", headers=H(LAW), json={"kind": "pen", "key": key, "clear": True}).json()
        assert next(x for x in k["obligations"]["penalties"] if x["key"] == key)["status"] == "accrued"
