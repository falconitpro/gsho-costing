"""Реестр договоров (этап 1): создаёт любой сотрудник, нумерация, типовые неустойки, проверка условий оплаты, график по событиям,
подписание со сканом (и ЭДО), блокировка подписанного, доп. соглашения, видимость."""
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
MGR, CD, GD, FD, LAW, LOG, OBS = "manager@example.com", "cd@example.com", "gd@example.com", "fd@example.com", "lawyer@example.com", "logist@example.com", "observer@example.com"
PDF = ("scan.pdf", b"%PDF-1.4 test", "application/pdf")


def test_contract_registry_lifecycle():
    seed()
    with TestClient(app) as c:
        cat = c.get("/api/legal/contracts/catalog", headers=H(MGR)).json()
        assert "logistics" in cat["kinds"] and "act_signed" in cat["events"] and cat["penalty_defaults"]["late_delivery"]["rate"] == 0.1
        ent = c.get("/api/legal/contracts", headers=H(LAW)).json()["entities"][0]
        # любой сотрудник заводит договор и становится куратором; номер — П-<код>-<год>-NNN; типовые неустойки подставлены
        r = c.post("/api/legal/contracts", headers=H(LOG), json={"kind": "logistics", "own_entity_id": ent["id"], "cp_name": "ООО «ТрансЛогистик»", "cp_inn": "7701234567",
                                                                 "subject": "перевозка ГШО Забайкальск — Алдан", "amount": 1200000, "currency": "rub"})
        assert r.status_code == 201, r.text
        k = r.json()
        assert k["direction"] == "expense" and k["number"].startswith("П-") and k["number"].endswith(f"-{date.today().year}-" + k["number"].rsplit("-", 1)[1]) and k["currency"] == "RUB"
        assert k["status"] == "draft" and k["curator_name"] and {p["reason"] for p in k["penalties"]} == {"late_delivery", "late_payment"}
        dl = next(p for p in k["penalties"] if p["reason"] == "late_delivery")
        assert dl["payer"] == "cp" and dl["cap_value"] == 5 and k["liability_cap_pct"] == 10          # расходный: за просрочку платит контрагент
        # без условий оплаты договор не готов
        assert any("условия оплаты" in e for e in k["check"]["errors"])
        bad = c.put(f"/api/legal/contracts/{k['id']}", headers=H(LOG), json={"payment_terms": [{"share_pct": 50, "event": "contract", "offset_days": 5, "offset_kind": "working"}]}).json()
        assert any("100 %" in e for e in bad["check"]["errors"])
        assert c.put(f"/api/legal/contracts/{k['id']}", headers=H(LOG), json={"payment_terms": [{"share_pct": 100, "event": "nope"}]}).status_code == 422
        k = c.put(f"/api/legal/contracts/{k['id']}", headers=H(LOG), json={"payment_terms": [
            {"share_pct": 30, "event": "contract", "offset_days": 5, "offset_kind": "working"},
            {"share_pct": 70, "event": "act_signed", "offset_days": 10, "offset_kind": "calendar"}], "law": {"claim_days": 30}, "signatory": {"name": "Иванов И. И.", "basis": "Устав"},
            "performance_terms": [{"code": "delivery", "event": "contract", "offset_days": 30, "offset_kind": "calendar"}]}).json()
        assert k["check"]["ok"] and "в течение 5 рабочих дней после события «подписание договора»" in k["terms_text"]
        # график: до подписания срока нет; после — срок от даты подписания в рабочих днях
        assert k["schedule"][0]["due_on"] is None and k["schedule"][0]["amount"] == 360000.0
        signed = date(2026, 11, 6)   # пятница
        k = c.put(f"/api/legal/contracts/{k['id']}", headers=H(LAW), json={"signed_on": signed.isoformat()}).json()
        assert k["schedule"][0]["due_on"] == "2026-11-13" and k["schedule"][0]["due_legal"] is True       # 5 рабочих дней с учётом выходных
        assert k["schedule"][1]["due_on"] is None                                                        # акт ещё не подписан
        # «действует» — только со сканом; при ЭДО — ещё и файл ЭДО
        assert c.post(f"/api/legal/contracts/{k['id']}/status", headers=H(LOG), json={"status": "active"}).status_code == 403
        r = c.post(f"/api/legal/contracts/{k['id']}/status", headers=H(LAW), json={"status": "active"})
        assert r.status_code == 422 and "скан" in r.text
        c.put(f"/api/legal/contracts/{k['id']}", headers=H(LAW), json={"sign_method": "edo", "edo": {"operator": "Диадок"}})
        c.post(f"/api/legal/contracts/{k['id']}/files", headers=H(LOG), files={"file": PDF}, data={"kind": "signed_scan"})
        assert "ЭДО" in c.post(f"/api/legal/contracts/{k['id']}/status", headers=H(LAW), json={"status": "active"}).text
        c.post(f"/api/legal/contracts/{k['id']}/files", headers=H(LAW), files={"file": ("doc.sig", b"sig", "application/octet-stream")}, data={"kind": "edo"})
        k = c.post(f"/api/legal/contracts/{k['id']}/status", headers=H(FD), json={"status": "active"}).json()
        assert k["status"] == "active" and k["locked"] and k["has_scan"]
        # подписанный: условия — только доп. соглашением; номер/дату — ЗГД или КД; скан не удаляется
        assert c.put(f"/api/legal/contracts/{k['id']}", headers=H(LAW), json={"amount": 1}).status_code == 409
        assert c.put(f"/api/legal/contracts/{k['id']}", headers=H(LOG), json={"notes": "x"}).status_code == 403
        assert c.put(f"/api/legal/contracts/{k['id']}", headers=H(LAW), json={"cp_number": "TL-15"}).status_code == 403
        assert c.put(f"/api/legal/contracts/{k['id']}", headers=H(GD), json={"cp_number": "TL-15"}).json()["cp_number"] == "TL-15"
        assert c.put(f"/api/legal/contracts/{k['id']}", headers=H(LAW), json={"original": {"received_on": "2026-11-10", "location": "архив, папка 3"}}).status_code == 200
        scan = next(f for f in c.get(f"/api/legal/contracts/{k['id']}", headers=H(LAW)).json()["files"] if f["kind"] == "signed_scan")
        assert c.delete(f"/api/legal/contracts/{k['id']}/files/{scan['id']}", headers=H(GD)).status_code == 409
        assert c.get(f"/api/legal/contracts/{k['id']}/files/{scan['id']}", headers=H(LOG)).status_code == 200
        # доп. соглашение: номер от договора, стороны унаследованы
        ds = c.post("/api/legal/contracts", headers=H(LOG), json={"parent_id": k["id"], "subject": "изменение маршрута", "amount": 150000}).json()
        assert ds["doc_type"] == "amendment" and ds["number"] == f"{k['number']}/ДС-1" and ds["cp_name"] == "ООО «ТрансЛогистик»" and ds["direction"] == "expense"
        assert c.get(f"/api/legal/contracts/{k['id']}", headers=H(LOG)).json()["children"][0]["id"] == ds["id"]
        # реестр: фильтры, поиск, сводка; видимость — куратор и руководители/юрист/наблюдатель, чужой менеджер не видит
        reg = c.get("/api/legal/contracts?direction=expense&status=active", headers=H(LAW)).json()
        assert any(x["id"] == k["id"] for x in reg["items"]) and all(x["status"] == "active" for x in reg["items"]) and reg["summary"]["active"] >= 1
        assert c.get("/api/legal/contracts?q=ТрансЛогистик", headers=H(OBS)).json()["items"][0]["id"] == k["id"]
        assert all(x["id"] != k["id"] for x in c.get("/api/legal/contracts", headers=H(MGR)).json()["items"])
        assert c.get(f"/api/legal/contracts/{k['id']}", headers=H(MGR)).status_code == 404
        # доходный договор: за просрочку поставки платим мы, за просрочку оплаты — клиент
        cl = c.get("/api/refs/clients", headers=H(CD)).json()[0]
        inc = c.post("/api/legal/contracts", headers=H(CD), json={"kind": "client_parts", "own_entity_id": ent["id"], "cp_kind": "client", "cp_id": cl["id"], "amount": 500000,
                                                                  "payment_terms": [{"share_pct": 100, "event": "fixed_date", "event_on": (date.today() - timedelta(days=3)).isoformat()}]}).json()
        assert inc["direction"] == "income" and inc["number"].startswith("К-") and inc["cp_name"] == cl["name"]
        assert {p["reason"]: p["payer"] for p in inc["penalties"]} == {"late_delivery": "us", "late_payment": "cp"}
        assert inc["schedule"][0]["days_overdue"] == 3                                                     # фиксированная дата прошла — просрочка
        # расторжение и аннулирование — с причиной
        assert c.post(f"/api/legal/contracts/{inc['id']}/status", headers=H(CD), json={"status": "cancelled"}).status_code == 422
        assert c.post(f"/api/legal/contracts/{inc['id']}/status", headers=H(CD), json={"status": "cancelled", "comment": "клиент отказался"}).json()["status"] == "cancelled"
