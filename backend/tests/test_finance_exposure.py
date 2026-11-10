"""«Финансы», этап 5: «Обеспечение и неустойки» — чтение юридического блока, разделение начисляемых/предъявленных/прогноза (FIN-UI-007; D-138, D-139)."""
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
FD, LAW, LOG, CD, MGR = "fd@example.com", "lawyer@example.com", "logist@example.com", "cd@example.com", "manager@example.com"
PDF = ("scan.pdf", b"%PDF-1.4 test", "application/pdf")


def test_legal_exposure_read_only():
    seed()
    today = date.today()
    with TestClient(app) as c:
        ent = c.get("/api/legal/contracts", headers=H(LAW)).json()["entities"][0]
        k = c.post("/api/legal/contracts", headers=H(LOG), json={
            "kind": "logistics", "own_entity_id": ent["id"], "cp_name": "ООО «Перевозчик-Обеспечение»", "cp_inn": "7700000098", "subject": "перевозка", "amount": 1000000,
            "currency": "RUB", "signatory": {"name": "Иванов И. И.", "basis": "Устав"}, "signed_on": (today - timedelta(days=40)).isoformat(),
            "payment_terms": [{"share_pct": 100, "event": "contract", "offset_days": 10, "offset_kind": "calendar"}],
            "performance_terms": [{"code": "delivery", "event": "contract", "offset_days": 20, "offset_kind": "calendar"}],
            "security": {"kind": "bank_guarantee", "amount": 100000, "currency": "RUB", "valid_to": (today + timedelta(days=12)).isoformat(), "bank": "ПАО Банк"}}).json()
        cid = k["id"]
        c.post(f"/api/legal/contracts/{cid}/files", headers=H(LOG), files={"file": PDF}, data={"kind": "signed_scan"})
        assert c.post(f"/api/legal/contracts/{cid}/status", headers=H(LAW), json={"status": "active"}).json()["status"] == "active"

        # права: раздел «Финансы»; КД по умолчанию раздела нет (D-116), менеджер тоже
        assert c.get("/api/finance/legal-exposure", headers=H(CD)).status_code == 403
        assert c.get("/api/finance/legal-exposure", headers=H(MGR)).status_code == 403
        assert c.get("/api/finance/legal-exposure?contract_id=00000000-0000-0000-0000-000000000000", headers=H(FD)).status_code == 404

        x = c.get(f"/api/finance/legal-exposure?contract_id={cid}", headers=H(FD)).json()
        sec = x["security"]
        assert len(sec) == 1 and sec[0]["kind_title"] == "банковская гарантия" and sec[0]["status"] == "expires_soon" and sec[0]["amount_rub"] == 100000
        assert sec[0]["contract"]["url"] == f"/legal?tab=contracts&open={cid}" and sec[0]["direction"] == "provided_by_us"
        pens = {p["reason_title"]: p for p in x["penalties"]}
        assert len(pens) == 2 and all(p["status"] == "accruing" and not p["in_forecast"] for p in pens.values())   # начисляемые — не денежный план
        assert x["totals"]["payable"]["accruing"] == 30000 and x["totals"]["receivable"]["accruing"] == 20000
        assert x["totals"]["receivable"]["in_forecast"] == 0 and x["note"].startswith("Условия и решения")

        # юрист предъявил неустойку → она в прогнозе (реестр обязательств), с ожидаемой датой
        rec = next(p for p in x["penalties"] if p["direction"] == "receivable")
        r = c.post(f"/api/legal/contracts/{cid}/facts", headers=H(LAW), json={"kind": "pen", "key": rec["key"], "status": "claimed", "amount": 18000})
        assert r.status_code == 200, r.text
        x = c.get(f"/api/finance/legal-exposure?contract_id={cid}&direction=receivable", headers=H(FD)).json()
        assert len(x["penalties"]) == 1
        p = x["penalties"][0]
        assert p["status"] == "claimed" and p["amount"] == 18000 and p["in_forecast"] and p["forecast"]["on"] and p["forecast"]["url"].startswith("/finance?tab=calendar&open=")
        assert x["totals"]["receivable"]["claimed"] == 18000 and x["totals"]["receivable"]["in_forecast"] == 18000
        ob = c.get(f"/api/finance/obligations/{p['forecast']['obligation_id']}", headers=H(FD)).json()
        assert ob["nature"] == "penalty" and ob["amount"] == "18000.00" and ob["direction"] == "in"

        # фильтры по виду и статусу
        assert c.get(f"/api/finance/legal-exposure?contract_id={cid}&kind=security", headers=H(FD)).json()["penalties"] == []
        assert len(c.get(f"/api/finance/legal-exposure?contract_id={cid}&kind=penalty&status=accruing", headers=H(FD)).json()["penalties"]) == 1
        # финансовый раздел ничего не меняет в договоре: условия те же
        assert c.get(f"/api/legal/contracts/{cid}", headers=H(LAW)).json()["security"]["amount"] == 100000

        # гарантия по сделке и аккредитив клиента (этап исполнения) — тоже в обеспечении
        from app.db import SessionLocal
        from app.models import Deal, ExecutionStage
        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        d = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "обеспечение"}).json()
        with SessionLocal() as db:
            deal = db.get(Deal, d["id"])
            deal.bank_guarantee = {"required": True, "amount": 500000, "currency": "RUB", "until": (today - timedelta(days=1)).isoformat(), "bank": "Банк"}
            db.add(ExecutionStage(deal_id=deal.id, code="lc_opened", title="Открытие аккредитива клиентом", sort=40, amount=7000000, currency="RUB", responsible_roles=["fd"],
                                  planned_on=today + timedelta(days=5)))
            db.commit()
        x = c.get(f"/api/finance/legal-exposure?deal_id={d['id']}", headers=H(FD)).json()
        kinds = {s["kind_title"]: s for s in x["security"]}
        assert kinds["банковская гарантия по сделке"]["status"] == "expired" and kinds["аккредитив клиента"]["status"] == "planned"
        assert kinds["аккредитив клиента"]["amount_rub"] == 7000000 and kinds["аккредитив клиента"]["deal"]["url"].endswith("?tab=execution")
        assert c.get("/api/finance/legal-exposure", headers=H(FD)).status_code == 200
