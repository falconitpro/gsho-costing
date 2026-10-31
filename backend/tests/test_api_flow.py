"""Сквозной тест API на живой PostgreSQL: менеджер → логист → сервис → КД → (ФД) → утверждение.

Запускается, когда задан DATABASE_URL (в CI — сервис postgres). Без БД — пропускается.
"""
from __future__ import annotations

import os
import tempfile

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.rfq_template import build_rfq_template  # noqa: E402
from scripts.seed import run as seed  # noqa: E402
from tests.test_rfq_import import FILLED, ZIP  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, LOG, SVC, CD, FD = "manager@example.com", "logist@example.com", "service@example.com", "cd@example.com", "fd@example.com"


@pytest.fixture(scope="module")
def client():
    seed()
    with TestClient(app) as c:
        yield c


def _ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:800])
    return r.json() if r.content else None


def test_full_flow(client, tmp_path):
    # --- менеджер: сделка, RFQ, вариант, оплата
    clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
    deal = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "Месторождение Северное",
                                                              "service_flag": True, "target_hours": 10000, "kp_due": "2026-11-15"}), 201)
    did = deal["id"]
    assert deal["number"].startswith("D-") and "calc_result" not in deal
    rfq = build_rfq_template(str(tmp_path / "rfq.xlsx"), FILLED, ZIP)
    with open(rfq, "rb") as f:
        prev = _ok(client.post(f"/api/deals/{did}/rfq/preview", headers=H(MGR), files={"file": ("rfq.xlsx", f)}))
    assert prev["template_version"] == "v1" and not prev["missing_required"]
    with open(rfq, "rb") as f:
        imp = _ok(client.post(f"/api/deals/{did}/rfq/import", headers=H(MGR), files={"file": ("rfq.xlsx", f)}), 201)
    pos = imp["position"]
    assert pos["model"] == "XDR80T" and pos["qty"] == 2 and pos["currency"] == "CNY"
    schemes = _ok(client.get("/api/refs/payment-schemes", headers=H(MGR)))
    _ok(client.patch(f"/api/deals/{did}", headers=H(MGR), json={"client_payments": schemes[0]["lines"]}))
    var = _ok(client.post(f"/api/deals/{did}/variants", headers=H(MGR), json={"name": "Базовый", "position_ids": [pos["id"]]}), 201)
    d = _ok(client.get(f"/api/deals/{did}", headers=H(MGR)))
    assert d["checks"] == [], d["checks"]
    assert _ok(client.post(f"/api/deals/{did}/send-to-calc", headers=H(MGR)))["status"] == "logistics_calc"
    # задачи логиста и сервиса созданы
    assert any(t["deal_id"] == did for t in _ok(client.get("/api/tasks/my", headers=H(LOG))))
    assert any(t["deal_id"] == did for t in _ok(client.get("/api/tasks/my", headers=H(SVC))))
    # менеджер не может редактировать сделку на расчёте
    assert client.patch(f"/api/deals/{did}", headers=H(MGR), json={"site": "x"}).status_code == 409

    # --- логист: партия, статьи, ТН ВЭД
    batch = _ok(client.post(f"/api/deals/{did}/logistics/batches", headers=H(LOG), json={
        "name": "Партия 1", "transport_units": 2, "transit_days": 18, "customs_days": 6,
        "items": {"intl_freight": {"amount": 150000, "currency": "CNY"}, "insurance_pct": 0.3, "broker_rub": 450000, "rf_delivery_rub": 1500000},
        "position_ids": [pos["id"]]}), 201)
    assert batch["items"]["checks"]["intl_freight"]["history_avg"] is None     # истории ещё нет
    lg = _ok(client.put(f"/api/deals/{did}/logistics/positions/{pos['id']}", headers=H(LOG), json={
        "domestic_logistics": {"amount": 48000, "currency": "CNY"}, "export_formalities": {"amount": 6000, "currency": "CNY"},
        "hs_code": "8704101090", "util_fee_rub": 1200000, "certification_rub": 850000}))
    assert lg["logistics"]["duty_pct"] == 5.0 and lg["logistics"]["vat_pct"] == 20.0   # ставки из справочника
    # ручная ставка без комментария отклоняется
    r = client.put(f"/api/deals/{did}/logistics/positions/{pos['id']}", headers=H(LOG), json={"hs_code": "8704101090", "duty_pct": 2.0})
    assert r.status_code == 422
    assert _ok(client.post(f"/api/deals/{did}/logistics/complete", headers=H(LOG)))["status"] == "service_calc"
    # логист не видит цену и расчёт
    dl = _ok(client.get(f"/api/deals/{did}", headers=H(LOG)))
    assert "price_rub" not in dl["variants"][0] and "service_calc" not in dl["positions"][0]

    # --- сервис
    dfl = _ok(client.get(f"/api/deals/{did}/service/positions/{pos['id']}/defaults", headers=H(SVC)))
    assert dfl["target_hours"] == 10000 and len(dfl["spare_parts"]) == 3
    res = _ok(client.post(f"/api/deals/{did}/service/positions/{pos['id']}/calc", headers=H(SVC), json={
        "regulations": [{"name": "ТО-1", "interval_hours": 250, "operations": [{"name": "Осмотр", "hours": 2, "qualification": "mechanic", "spare_part_rows": []}]},
                        {"name": "ТО-2", "interval_hours": 500, "operations": [{"name": "Масло и фильтры", "hours": 4, "qualification": "engineer_2", "spare_part_rows": [4, 5]}]}],
        "show_in_kp": True}))
    assert res["executions"] == {"ТО-1": 40, "ТО-2": 20} and res["total_rub_part"] > 0
    assert _ok(client.post(f"/api/deals/{did}/service/complete", headers=H(SVC)))["status"] == "calc_ready"
    assert client.get(f"/api/deals/{did}/calc", headers=H(SVC)).status_code == 403

    # --- КД: расчёт, цена по целевой марже, утверждение без виз
    calc = _ok(client.get(f"/api/deals/{did}/calc", headers=H(CD)))
    base = calc["result"]["variants"][var["id"]]["base"]
    assert set(base["items_rub"]) == {"factory_price", "domestic_logistics", "export_formalities", "intl_freight", "insurance", "customs_duty",
                                      "import_vat", "broker_util", "certification", "rf_delivery", "finance", "service", "warranty_reserve", "overhead"}
    assert base["items_rub"]["service"] > 0 and base["items_rub"]["customs_duty"] > 0 and base["cash_gap"]["amount_rub"] < 0
    assert calc["result"]["fx_components"] and calc["threshold_pct"] == 3.0
    p = _ok(client.post(f"/api/deals/{did}/price", headers=H(CD), json={"variant_id": var["id"], "mode": "margin", "value": 8.0}))
    assert abs(p["result"]["base"]["margin_pct"] - 8.0) < 0.05 and p["approval_flags"]["margin_below_threshold"] is False
    sub = _ok(client.post(f"/api/deals/{did}/submit", headers=H(CD)))
    assert sub["status"] == "approved", sub
    vers = _ok(client.get(f"/api/deals/{did}/versions", headers=H(CD)))
    assert vers[0]["reason"] == "approval" and vers[0]["excel_path"] and vers[0]["excel_manager_path"] and vers[0]["kp_path"]
    # менеджеру — версия без полного Excel, файл 04 закрыт
    vm = _ok(client.get(f"/api/deals/{did}/versions", headers=H(MGR)))
    assert "excel_path" not in vm[0] and vm[0]["excel_manager_path"]
    assert client.get(f"/api/files/{vers[0]['excel_path']}", headers=H(MGR)).status_code == 403
    assert client.get(f"/api/files/{vers[0]['excel_manager_path']}", headers=H(MGR)).status_code == 200
    assert client.get(f"/api/files/{vers[0]['excel_path']}", headers=H(CD)).status_code == 200
    dm = _ok(client.get(f"/api/deals/{did}", headers=H(MGR)))
    assert dm["variants"][0]["price_rub"] and "calc_result" not in dm     # после утверждения менеджер видит цену
    # --- менеджер: КП отправлено, исход
    assert _ok(client.post(f"/api/deals/{did}/kp-sent", headers=H(MGR), json={"recipients": ["mech@rudnik.example"]}))["status"] == "kp_sent"
    assert _ok(client.post(f"/api/deals/{did}/outcome", headers=H(MGR), json={"outcome": "won", "reason": "лучшая цена"}))["status"] == "won"
    # --- план/факт
    _ok(client.post(f"/api/deals/{did}/fact", headers=H(LOG), json={"kind": "cost_item", "cost_item_code": "intl_freight", "amount": 2000000, "on": "2027-03-01"}), 201)
    assert client.post(f"/api/deals/{did}/fact", headers=H(LOG), json={"kind": "cost_item", "cost_item_code": "service", "amount": 1, "on": "2027-03-01"}).status_code == 403
    pf = _ok(client.get(f"/api/deals/{did}/plan-fact", headers=H(FD)))
    row = next(r for r in pf["rows"] if r["code"] == "intl_freight")
    assert row["fact"] == 2000000 and row["deviation_rub"] is not None
    # журнал
    audit = _ok(client.get(f"/api/deals/{did}/audit", headers=H(CD)))
    assert any(a["action"] == "status" for a in audit)


def test_low_margin_goes_to_fd_visa(client, tmp_path):
    clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
    factories = _ok(client.get("/api/refs/factories", headers=H(MGR)))
    deal = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "Карьер №2"}), 201)
    did = deal["id"]
    # ручной ввод позиции (без файла RFQ) с техданными: поля L попадают логисту
    pos = _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"factory_id": factories[0]["id"], "model": "TG-1000 LHD",
                                                                              "machine_type": "ПДМ", "qty": 1, "unit_price": 1650000, "currency": "CNY",
                                                                              "logistics_params": {"1.14": {"name": "Масса", "value": 24000, "unit": "кг", "is_l": True}},
                                                                              "tech_params": {"1.3": {"name": "Грузоподъёмность", "value": 10, "unit": "т"}}}), 201)
    assert pos["logistics_params"]["1.14"]["value"] == 24000 and pos["tech_params"]["_source_file"] == "ручной ввод"
    upd = _ok(client.patch(f"/api/deals/{did}/positions/{pos['id']}", headers=H(MGR), json={"qty": 2, "tech_params": {"1.4": {"name": "Ковш", "value": 5, "unit": "м³"}}}))
    assert upd["qty"] == 2 and "1.3" in upd["tech_params"] and "1.4" in upd["tech_params"]
    _ok(client.patch(f"/api/deals/{did}/positions/{pos['id']}", headers=H(MGR), json={"qty": 1}))
    schemes = _ok(client.get("/api/refs/payment-schemes", headers=H(MGR)))
    _ok(client.patch(f"/api/deals/{did}", headers=H(MGR), json={"client_payments": schemes[1]["lines"]}))
    var = _ok(client.post(f"/api/deals/{did}/variants", headers=H(MGR), json={"name": "V", "position_ids": [pos["id"]]}), 201)
    _ok(client.post(f"/api/deals/{did}/send-to-calc", headers=H(MGR)))
    # отклонение от истории (первая сделка дала 150 000) выше 15 % → без комментария 422, с комментарием — принимается
    items = {"intl_freight": {"amount": 60000, "currency": "CNY"}, "broker_rub": 200000, "rf_delivery_rub": 500000}
    assert client.post(f"/api/deals/{did}/logistics/batches", headers=H(LOG), json={"items": items, "position_ids": [pos["id"]]}).status_code == 422
    items["comments"] = {"intl_freight": "одна машина, сборная партия", "broker_rub": "упрощённое оформление", "rf_delivery_rub": "ближний регион"}
    _ok(client.post(f"/api/deals/{did}/logistics/batches", headers=H(LOG), json={"items": items, "position_ids": [pos["id"]]}), 201)
    _ok(client.put(f"/api/deals/{did}/logistics/positions/{pos['id']}", headers=H(LOG), json={"hs_code": "8429519000", "util_fee_rub": 600000}))
    assert _ok(client.post(f"/api/deals/{did}/logistics/complete", headers=H(LOG)))["status"] == "calc_ready"
    _ok(client.post(f"/api/deals/{did}/price", headers=H(CD), json={"variant_id": var["id"], "mode": "margin", "value": 1.0}))
    sub = _ok(client.post(f"/api/deals/{did}/submit", headers=H(CD)))
    assert sub["status"] == "fd_visa" and sub["approval_flags"]["margin_below_threshold"]
    assert client.post(f"/api/deals/{did}/visa", headers=H(CD), json={"approved": True}).status_code == 403
    assert _ok(client.post(f"/api/deals/{did}/visa", headers=H(FD), json={"approved": True, "comment": "стратегический клиент"}))["status"] == "approved"
    # возврат на доработку создаёт версию и задачу логисту
    rw = _ok(client.post(f"/api/deals/{did}/rework", headers=H(CD), json={"block": "logistics", "reason": "уточнить фрахт"}))
    assert rw["status"] == "rework" and rw["version_no"] >= 2
    assert any(t["deal_id"] == did and t["block"] == "logistics" for t in _ok(client.get("/api/tasks/my", headers=H(LOG))))
    board = _ok(client.get("/api/board", headers=H(FD)))
    assert any(d["id"] == did for d in board["rework"]["deals"])
