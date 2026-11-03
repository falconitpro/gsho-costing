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
from app.services.rfq_template import fill_rfq_template  # noqa: E402
from scripts.seed import run as seed  # noqa: E402
from tests.test_rfq_import import FILLED, OPTION_NAMES, ZIP  # noqa: E402

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
    schema = _ok(client.get("/api/deals/rfq/template", headers=H(MGR)))
    assert len(schema["fields"]) >= 170 and any(f["is_l"] for f in schema["fields"])
    rfq = fill_rfq_template(str(tmp_path / "rfq.xlsx"), FILLED, ZIP, option_names=OPTION_NAMES)
    with open(rfq, "rb") as f:
        prev = _ok(client.post(f"/api/deals/{did}/rfq/preview", headers=H(MGR), files={"file": ("rfq.xlsx", f)}))
    assert prev["template_version"] == "v2" and not prev["missing_required"] and prev["filled_count"] >= 50
    with open(rfq, "rb") as f:
        imp = _ok(client.post(f"/api/deals/{did}/rfq/import", headers=H(MGR), files={"file": ("rfq.xlsx", f)}), 201)
    pos = imp["position"]
    assert pos["model"] == "XDR80T" and pos["qty"] == 2 and pos["currency"] == "CNY" and pos["options_price"] == 86000
    # весь опросник сохранён в карточке: заполненные поля по разделам + L-поля логисту
    rfq_all = pos["tech_params"]["_rfq"]
    assert rfq_all["total"] >= 170 and rfq_all["filled"] >= 50 and "1.8" in pos["tech_params"] and "1.5" in pos["logistics_params"]
    schemes = _ok(client.get("/api/refs/payment-schemes", headers=H(MGR)))
    # задача 1: условия оплаты менеджер не задаёт — только пожелания клиента; задача 5: вариантов не требуется (служебное предложение)
    assert client.patch(f"/api/deals/{did}", headers=H(MGR), json={"client_payments": schemes[0]["lines"]}).status_code == 403
    _ok(client.patch(f"/api/deals/{did}", headers=H(MGR), json={"client_payment_wishes": "30 % аванс, остальное после поставки"}))
    assert client.post(f"/api/deals/{did}/variants", headers=H(MGR), json={"name": "Базовый", "position_ids": [pos["id"]]}).status_code == 409
    d = _ok(client.get(f"/api/deals/{did}", headers=H(MGR)))
    assert d["checks"] == [], d["checks"]
    assert d["offer"] and d["offer"]["position_ids"] == [pos["id"]] and d["payment_terms_set"] is False
    var = d["offer"]
    # задача 4: таблица теххарактеристик из RFQ, запрос заводу (файл), обязательные параметры
    tp = _ok(client.get(f"/api/deals/{did}/positions/{pos['id']}/tech-params", headers=H(MGR)))
    assert len(tp["rows"]) >= 50 and any(r["value_ru"] for r in tp["rows"]) and tp["missing_required"] == []
    assert any(r["code"] == "1.5" and r["value_ru"] for r in tp["rows"])              # поле «L» (длина) — и в таблице для КП
    assert client.get(f"/api/deals/{did}/positions/{pos['id']}/tech-params/request/file", headers=H(MGR)).status_code == 200
    assert _ok(client.post(f"/api/deals/{did}/send-to-calc", headers=H(MGR)))["status"] == "logistics_calc"
    # задачи логиста и сервиса созданы
    assert any(t["deal_id"] == did for t in _ok(client.get("/api/tasks/my", headers=H(LOG))))
    assert any(t["deal_id"] == did for t in _ok(client.get("/api/tasks/my", headers=H(SVC))))
    # менеджер не может редактировать сделку на расчёте
    assert client.patch(f"/api/deals/{did}", headers=H(MGR), json={"site": "x"}).status_code == 409

    # --- логист: партия, статьи, ТН ВЭД
    batch = _ok(client.post(f"/api/deals/{did}/logistics/batches", headers=H(LOG), json={
        "name": "Партия 1", "transport_units": 2, "transit_days": 18, "customs_days": 6, "border_planned": "2027-03-20", "rf_delivery_days": 7,
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
    assert dfl["target_hours"] == 10000 and len([x for x in dfl["spare_parts"] if x.get("filled")]) == 3
    res = _ok(client.post(f"/api/deals/{did}/service/positions/{pos['id']}/calc", headers=H(SVC), json={
        "regulations": [{"name": "ТО-1", "interval_hours": 250, "operations": [{"name": "Осмотр", "hours": 2, "qualification": "mechanic", "spare_part_rows": []}]},
                        {"name": "ТО-2", "interval_hours": 500, "operations": [{"name": "Масло и фильтры", "hours": 4, "qualification": "engineer_2", "spare_part_rows": [9, 10]}]}],
        "show_in_kp": True}))
    assert res["executions"] == {"ТО-1": 40, "ТО-2": 20} and res["total_rub_part"] > 0
    assert _ok(client.post(f"/api/deals/{did}/service/complete", headers=H(SVC)))["status"] == "calc_ready"
    assert client.get(f"/api/deals/{did}/calc", headers=H(SVC)).status_code == 403

    # --- КД: расчёт, цена по целевой марже, условия оплаты (задача 1), утверждение без виз
    calc = _ok(client.get(f"/api/deals/{did}/calc", headers=H(CD)))
    base = calc["result"]["variants"][var["id"]]["base"]
    assert set(base["items_rub"]) == {"factory_price", "domestic_logistics", "export_formalities", "intl_freight", "insurance", "customs_duty",
                                      "import_vat", "broker_util", "certification", "rf_delivery", "finance", "service", "warranty_reserve", "overhead",
                                      "local_completion"}
    assert base["items_rub"]["service"] > 0 and base["items_rub"]["customs_duty"] > 0 and base["cash_gap"]["amount_rub"] < 0
    assert calc["result"]["fx_components"] and calc["threshold_pct"] == 3.0
    assert base["defaults_used"]["client_payments"] is True and any("типовой схеме" in w for w in calc["warnings"])
    assert calc["terms"][var["id"]][0]["delivery_term"]["promised"] % 7 == 0           # задача 3: срок клиенту кратен неделе
    p = _ok(client.post(f"/api/deals/{did}/price", headers=H(CD), json={"variant_id": var["id"], "mode": "margin", "value": 8.0}))
    assert abs(p["result"]["base"]["margin_pct"] - 8.0) < 0.05 and p["approval_flags"]["margin_below_threshold"] is False
    # без условий оплаты от КД утверждение не проходит
    assert client.post(f"/api/deals/{did}/submit", headers=H(CD)).status_code == 422
    terms = _ok(client.post(f"/api/deals/{did}/payment-terms", headers=H(CD), json={
        "client_payments": calc["payment_terms"]["default_client"], "factory_payments": {pos["id"]: calc["payment_terms"]["default_factory"]},
        "bank_guarantee": {"enabled": True, "mode": "one_time_pct", "value": 1.0}, "accept_wishes": True}))
    assert terms["cd_checks"] == [] and terms["result"]["variants"][var["id"]]["base"]["guarantee_cost_rub"] > 0
    dmg = _ok(client.get(f"/api/deals/{did}", headers=H(MGR)))
    assert dmg["payment_terms_set"] is True and dmg["client_payments"]                   # менеджер видит условия после ввода КД
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
    # --- менеджер: КП отправлено (история цен клиентам, задача 8), исход → стадия контракта (задача 2)
    assert _ok(client.post(f"/api/deals/{did}/kp-sent", headers=H(MGR), json={"recipients": ["mech@rudnik.example"]}))["status"] == "kp_sent"
    card_cd = _ok(client.get(f"/api/refs/clients/{clients[0]['id']}", headers=H(CD)))
    assert card_cd["kp_history"] and card_cd["kp_history"][0]["deal_number"] == deal["number"] and card_cd["kp_history"][0]["versions"]
    card_mgr = _ok(client.get(f"/api/refs/clients/{clients[0]['id']}", headers=H(MGR)))
    assert "kp_history" not in card_mgr and "last_kp" not in card_mgr                # историю КП видят только КД и ГД
    # задача 7: занесение в каталог — менеджер предлагает, КД подтверждает
    cand = _ok(client.get(f"/api/deals/{did}/catalog/candidates", headers=H(MGR)))
    assert cand[0]["model"] == "XDR80T"
    prop = _ok(client.post(f"/api/deals/{did}/catalog/propose", headers=H(MGR), json={"position_ids": [pos["id"]], "names": {pos["id"]: "XDR80T базовое"}}))
    assert len(prop["created"]) == 1 and prop["created"][0]["status"] == "pending"
    assert client.post(f"/api/catalog/executions/{prop['created'][0]['id']}/decision", headers=H(MGR), json={"approve": True}).status_code == 403
    ex = _ok(client.post(f"/api/catalog/executions/{prop['created'][0]['id']}/decision", headers=H(CD), json={"approve": True, "brand_name": "BH-80"}))
    assert ex["status"] == "approved"
    tree = _ok(client.get("/api/catalog/tree", headers=H(MGR)))
    assert any(e["name"] == "XDR80T базовое" for f in tree for t in f["types"] for m in t["models"] for e in m["executions"])
    out = _ok(client.post(f"/api/deals/{did}/outcome", headers=H(MGR), json={"outcome": "won", "reason": "лучшая цена"}))
    assert out["status"] == "contract"
    st = _ok(client.get(f"/api/deals/{did}/execution/stages", headers=H(MGR)))
    codes = {s["code"] for s in st["stages"]}
    assert {"contract_signed", "factory_contract", "client_advance", "factory_advance", "readiness", "border", "declaration", "delivery", "client_final", "commissioning"} <= codes
    assert client.post(f"/api/deals/{did}/execution/start", headers=H(MGR)).status_code == 409   # нет реквизитов контракта
    _ok(client.patch(f"/api/deals/{did}/execution/contract", headers=H(MGR), json={"contract_number": "К-2026-11", "contract_signed_at": "2026-12-01"}))
    assert _ok(client.post(f"/api/deals/{did}/execution/start", headers=H(MGR)))["status"] == "execution"
    st = _ok(client.get(f"/api/deals/{did}/execution/stages", headers=H(FD)))
    assert next(s for s in st["stages"] if s["code"] == "contract_signed")["done"]           # дата подписания из реквизитов = факт этапа
    # изменение условий контракта в исполнении → повторная виза → возврат в исполнение
    assert _ok(client.post(f"/api/deals/{did}/execution/terms-changed", headers=H(CD), json={"reason": "аванс 40 %"}))["status"] == "cd_approval"
    assert _ok(client.post(f"/api/deals/{did}/submit", headers=H(CD)))["status"] == "execution"
    st = _ok(client.get(f"/api/deals/{did}/execution/stages", headers=H(FD)))
    adv = next(s for s in st["stages"] if s["code"] == "client_advance")
    assert adv["can_set"] and adv["amount"] > 0
    assert client.post(f"/api/deals/{did}/execution/stages/{adv['id']}/actual", headers=H(MGR), json={"actual_on": "2026-12-10", "actual_amount": 1}).status_code == 403
    r = _ok(client.post(f"/api/deals/{did}/execution/stages/{adv['id']}/actual", headers=H(FD), json={"actual_on": "2026-12-10", "actual_amount": adv["amount"]}))
    assert next(s for s in r["stages"] if s["code"] == "client_advance")["done"]
    # --- план/факт (стадия исполнения)
    _ok(client.post(f"/api/deals/{did}/fact", headers=H(LOG), json={"kind": "cost_item", "cost_item_code": "intl_freight", "amount": 2000000, "on": "2027-03-01"}), 201)
    assert client.post(f"/api/deals/{did}/fact", headers=H(LOG), json={"kind": "cost_item", "cost_item_code": "service", "amount": 1, "on": "2027-03-01"}).status_code == 403
    pf = _ok(client.get(f"/api/deals/{did}/plan-fact", headers=H(FD)))
    row = next(r for r in pf["rows"] if r["code"] == "intl_freight")
    assert row["fact"] == 2000000 and row["deviation_rub"] is not None
    assert any(e["kind"] == "payment_in" for e in pf["entries"])                     # платёж ФД попал в план/факт
    # закрытие — только КД
    assert client.post(f"/api/deals/{did}/execution/close", headers=H(MGR), json={}).status_code == 403
    assert _ok(client.post(f"/api/deals/{did}/execution/close", headers=H(CD), json={"reason": "исполнено"}))["status"] == "closed"
    card = _ok(client.get(f"/api/refs/clients/{clients[0]['id']}", headers=H(CD)))
    assert card["funnel"]["won"] >= 1 and card["sum_won_rub"] > 0                       # закрытая выигранная сделка считается выигранной
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
    _ok(client.patch(f"/api/deals/{did}/positions/{pos['id']}", headers=H(MGR), json={"qty": 1,
        "production_term": {"value": 12, "unit": "weeks", "start_event": "factory_prepayment"}, "delivery_term": {"unit": "weeks", "start_event": "lc_opened"}}))
    var = _ok(client.get(f"/api/deals/{did}", headers=H(MGR)))["offer"]
    _ok(client.post(f"/api/deals/{did}/send-to-calc", headers=H(MGR)))
    # отклонение от истории (первая сделка дала 150 000) выше 15 % → без комментария 422, с комментарием — принимается
    items = {"intl_freight": {"amount": 60000, "currency": "CNY"}, "broker_rub": 200000, "rf_delivery_rub": 500000}
    assert client.post(f"/api/deals/{did}/logistics/batches", headers=H(LOG), json={"items": items, "position_ids": [pos["id"]]}).status_code == 422
    items["comments"] = {"intl_freight": "одна машина, сборная партия", "broker_rub": "упрощённое оформление", "rf_delivery_rub": "ближний регион"}
    _ok(client.post(f"/api/deals/{did}/logistics/batches", headers=H(LOG), json={"items": items, "position_ids": [pos["id"]]}), 201)
    _ok(client.put(f"/api/deals/{did}/logistics/positions/{pos['id']}", headers=H(LOG), json={"hs_code": "8429519000", "util_fee_rub": 600000}))
    assert _ok(client.post(f"/api/deals/{did}/logistics/complete", headers=H(LOG)))["status"] == "calc_ready"
    _ok(client.post(f"/api/deals/{did}/price", headers=H(CD), json={"variant_id": var["id"], "mode": "margin", "value": 1.0}))
    schemes = _ok(client.get("/api/refs/payment-schemes", headers=H(CD)))
    calc = _ok(client.get(f"/api/deals/{did}/calc", headers=H(CD)))
    assert calc["terms"][var["id"]][0]["delivery_term"]["unit"] == "weeks" and "аккредитива" in calc["terms"][var["id"]][0]["delivery_term"]["text"]
    _ok(client.post(f"/api/deals/{did}/payment-terms", headers=H(CD), json={"client_payments": schemes[1]["lines"], "factory_payments": {pos["id"]: calc["payment_terms"]["default_factory"]}}))
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


GD, ADM = "gd@example.com", "admin@example.com"


def _png(path):
    from PIL import Image
    Image.new("RGB", (640, 480), (200, 30, 30)).save(path, "PNG")
    return path


def test_tech_params_offers_images_local_options(client, tmp_path):
    """Задачи 4, 6, 9, 10: импорт таблицы клиента с предпросмотром, опции РФ, запрос нескольким заводам, ответ завода,
    сравнение и отбор КД (дублирование заявки), изображения и выбор для КП, бренд подачи."""
    from openpyxl import Workbook
    clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
    factories = _ok(client.get("/api/refs/factories", headers=H(MGR)))
    deal = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "Разрез Восточный"}), 201)
    did = deal["id"]
    pos = _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"factory_id": factories[0]["id"], "model": "ZL50", "machine_type": "Погрузчик",
                                                                              "qty": 1, "unit_price": 900000, "currency": "CNY"}), 201)
    pid = pos["id"]
    # вторая позиция той же модели: справка по цене с историей (даты в JSON) — не падает
    extra = _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"factory_id": factories[0]["id"], "model": "ZL50", "machine_type": "Погрузчик",
                                                                                "qty": 1, "unit_price": 910000, "currency": "CNY"}), 201)
    assert extra["price_reference"]["last_price"] == 900000 and extra["price_reference"]["history"]
    _ok(client.delete(f"/api/deals/{did}/positions/{extra['id']}", headers=H(MGR)), 204)
    # таблица из общего шаблона (фиксированные параметры RFQ 1–11)
    tp = _ok(client.get(f"/api/deals/{did}/positions/{pid}/tech-params", headers=H(MGR)))
    assert len(tp["rows"]) > 20 and all(not r["value_ru"] for r in tp["rows"] if r["code"] not in ("1.1", "1.2"))
    assert {r["code"]: r["value_ru"] for r in tp["rows"] if r["code"] in ("1.1", "1.2")} == {"1.1": "Погрузчик", "1.2": "ZL50"}   # тип и модель — из позиции
    # файл клиента: №, параметр, требование → предпросмотр и применение
    wb = Workbook(); ws = wb.active
    ws.append(["Требования к погрузчику"]); ws.append([])
    ws.append(["№", "Параметр", "Ед.", "Требование"])
    first = next(r for r in tp["rows"] if not r["value_ru"])
    ws.append([first["no"], first["name_ru"], first["unit"], "не менее 5 т"])
    ws.append(["", "Кондиционер кабины", "", "обязательно"])
    f = tmp_path / "client.xlsx"; wb.save(f)
    with open(f, "rb") as fh:
        prev = _ok(client.post(f"/api/deals/{did}/positions/{pid}/tech-params/import/preview", headers=H(MGR), files={"file": ("client.xlsx", fh)}))
    mp = prev["sheets"][0]["mapping"]
    assert mp["header_row"] == 2 and mp["columns"]["name_ru"] == 1 and mp["columns"]["customer_requirement_ru"] == 3
    import json
    with open(f, "rb") as fh:
        res = _ok(client.post(f"/api/deals/{did}/positions/{pid}/tech-params/import/apply", headers=H(MGR), files={"file": ("client.xlsx", fh)},
                              data={"mapping": json.dumps({"sheet": prev["sheets"][0]["name"], **mp}), "target": "requirements"}))
    assert res["matched"] == 1 and res["added"] == 1
    # задача 4: сопоставление запомнено для клиента — повторный файл того же формата
    with open(f, "rb") as fh:
        again = _ok(client.post(f"/api/deals/{did}/positions/{pid}/tech-params/import/preview", headers=H(MGR), files={"file": ("client.xlsx", fh)}))
    assert again["sheets"][0]["mapping"]["source"] == "saved" and again["sheets"][0]["mapping"]["columns"]["customer_requirement_ru"] == 3
    # RFQ на основе таблицы: требования заказчика вписаны по кодам
    assert client.get(f"/api/deals/{did}/positions/{pid}/rfq/file", headers=H(MGR)).status_code == 200
    rows = res["rows"]
    ac = next(r for r in rows if r["name_ru"] == "Кондиционер кабины")
    assert next(r for r in rows if r["no"] == first["no"])["customer_requirement_ru"] == "не менее 5 т"
    # задача 6: кондиционер реализуется в России → опция, задачи менеджеру/сервису/КД, не идёт заводу
    _ok(client.post(f"/api/deals/{did}/positions/{pid}/local-options", headers=H(MGR), json={"name": "Кондиционер кабины", "cost": 250000, "currency": "RUB",
                                                                                                "supplier_lead_days": 20, "install_stage": "before_handover", "install_days": 3, "tech_param_id": ac["id"]}), 201)
    tasks_svc = _ok(client.get("/api/tasks/my", headers=H(SVC)))
    assert any(t["deal_id"] == did and "установки" in t["title"] for t in tasks_svc)
    assert any(t["deal_id"] == did and "Утвердить стоимость" in t["title"] for t in _ok(client.get("/api/tasks/my", headers=H(CD))))
    tp = _ok(client.get(f"/api/deals/{did}/positions/{pid}/tech-params", headers=H(MGR)))
    assert next(r for r in tp["rows"] if r["id"] == ac["id"])["local_ru"] is True
    # задача 10: изображение клиента, отправка заводу; выбор для КП — только КД
    png = _png(tmp_path / "scheme.png")
    with open(png, "rb") as fh:
        img = _ok(client.post(f"/api/deals/{did}/positions/{pid}/images", headers=H(MGR), files={"file": ("scheme.png", fh, "image/png")},
                              data={"kind": "scheme", "source": "client", "caption": "схема клиента"}), 201)
    assert client.patch(f"/api/deals/{did}/positions/{pid}/images/{img['id']}", headers=H(MGR), json={"in_kp": True}).status_code == 403
    _ok(client.patch(f"/api/deals/{did}/positions/{pid}/images/{img['id']}", headers=H(CD), json={"in_kp": True, "kp_order": 1}))
    assert client.post(f"/api/deals/{did}/positions/{pid}/images", headers=H(FD), files={"file": ("x.png", b"123", "image/png")}).status_code == 403
    # задача 9: запрос двум заводам (файл RU+ZH, строка опции РФ не включена), ответ второго завода, сравнение, отбор КД
    # файлы клиента без подтверждения проверки — 422 (задача 10)
    r = client.post(f"/api/deals/{did}/positions/{pid}/tech-params/request", headers=H(MGR), json={"factory_ids": [factories[0]["id"]], "image_ids": [img["id"]]})
    assert r.status_code == 422 and "client_files" in r.json()["detail"]
    req = _ok(client.post(f"/api/deals/{did}/positions/{pid}/tech-params/request", headers=H(MGR), json={"factory_ids": [factories[0]["id"], factories[1]["id"]],
                                                                                                        "image_ids": [img["id"]], "via_wecom": True, "via_mail": False,
                                                                                                        "client_files_checked": True}))
    assert req["sent"][0]["wecom"].startswith("не отправлено")
    assert len(req["sent"]) == 2 and all(x["offer_id"] for x in req["sent"])
    offers = _ok(client.get(f"/api/deals/{did}/offers", headers=H(MGR)))
    assert len(offers) == 2 and all(o["status"] == "requested" for o in offers)
    second = next(o for o in offers if o["factory_id"] == factories[1]["id"])
    wb = Workbook(); ws = wb.active
    ws.append(["№", "参数", "数值（中文）", "备注"])
    ws.append([first["no"], first["name_ru"], "5.5", "标准配置"])
    f2 = tmp_path / "answer.xlsx"; wb.save(f2)
    with open(f2, "rb") as fh:
        prev2 = _ok(client.post(f"/api/deals/{did}/positions/{pid}/tech-params/import/preview", headers=H(MGR), files={"file": ("answer.xlsx", fh)}))
    mp2 = prev2["sheets"][0]["mapping"]
    assert mp2["columns"].get("value_zh") == 2
    with open(f2, "rb") as fh:
        ans = _ok(client.post(f"/api/deals/{did}/positions/{pid}/tech-params/import/apply", headers=H(MGR), files={"file": ("answer.xlsx", fh)},
                              data={"mapping": json.dumps({"sheet": prev2["sheets"][0]["name"], **mp2}), "target": "factory_answer", "offer_id": second["id"]}))
    assert ans["matched"] == 1 and ans["offer_id"] == second["id"]
    _ok(client.post(f"/api/deals/{did}/offers", headers=H(MGR), json={"factory_id": factories[1]["id"], "model": "LW500", "unit_price": 850000, "currency": "CNY",
                                                                     "production_term": {"value": 10, "unit": "weeks"}, "source_position_id": pid}), 201)
    # задача 10: фото из ответа второго завода — к кандидату
    with open(png, "rb") as fh:
        oi = _ok(client.post(f"/api/deals/{did}/offers/{second['id']}/images", headers=H(MGR), files={"file": ("lw500.png", fh, "image/png")}), 201)
    assert oi["images"] == 1
    cmp_ = _ok(client.get(f"/api/deals/{did}/offers/compare?position_id={pid}", headers=H(MGR)))
    assert len(cmp_["offers"]) == 2 and all(r["name_ru"] != "Кондиционер кабины" for r in cmp_["rows"])
    row0 = next(r for r in cmp_["rows"] if r["no"] == first["no"])
    assert [c["status"] for c in row0["cells"]].count("empty") == 1 and any(c["value"] == "5.5" for c in row0["cells"])
    _ok(client.post(f"/api/deals/{did}/offers/submit-compare", headers=H(MGR)))
    assert any(t["deal_id"] == did and "Отобрать заводы" in t["title"] for t in _ok(client.get("/api/tasks/my", headers=H(CD))))
    assert client.post(f"/api/deals/{did}/offers/select", headers=H(MGR), json={"offer_ids": [o["id"] for o in offers]}).status_code == 403
    sel = _ok(client.post(f"/api/deals/{did}/offers/select", headers=H(CD), json={"offer_ids": [o["id"] for o in offers], "copy_calcs": True}))
    assert len(sel["created_deals"]) == 1 and sel["created_deals"][0]["number"] == deal["number"] + "-2"
    nxt = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "x"}), 201)   # дубль не сдвигает нумерацию
    assert int(nxt["number"].split("-")[2]) == int(deal["number"].split("-")[2]) + 1
    # пропуск в нумерации (старые данные) не даёт повторного номера
    nxt2 = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "y"}), 201)
    assert nxt2["number"] != nxt["number"] and int(nxt2["number"].split("-")[2]) == int(nxt["number"].split("-")[2]) + 1
    related = _ok(client.get(f"/api/deals/{did}/related", headers=H(MGR)))
    assert len(related) == 2
    new = _ok(client.get(f"/api/deals/{sel['created_deals'][0]['id']}", headers=H(MGR)))
    assert new["parent_deal_id"] == did and new["positions"][0]["model"] == "LW500" and new["positions"][0]["factory_id"] == factories[1]["id"]
    assert new["positions"][0]["local_options"] and len(new["positions"][0]["images"]) >= 2 and new["offer"]   # своё + фото завода
    assert any(i["source"] == "factory" and i["has_factory_logo"] for i in new["positions"][0]["images"])
    lst = _ok(client.get("/api/deals", headers=H(MGR)))                                    # задача 9: группировка в списке
    dup = next(x for x in lst if x["id"] == new["id"])
    assert dup["group"] == did and dup["parent_number"] == deal["number"] and dup["related_count"] >= 1
    brd = _ok(client.get("/api/board", headers=H(CD)))
    assert any(x["id"] == new["id"] and x["group"] == did for col in brd.values() for x in col["deals"])
    assert any(r["value_ru"] == "5.5" for r in new["positions"][0]["tech_param_rows"])
    # бренд компании: без наименования в каталоге — 422; задать можно через каталог
    assert client.patch(f"/api/deals/{did}/kp-settings", headers=H(CD), json={"kp_brand": "company"}).status_code == 422
    # после занесения в каталог с наименованием бренда позиция привязана к исполнению → подача под брендом компании доступна
    prop = _ok(client.post(f"/api/deals/{did}/catalog/propose", headers=H(MGR), json={"position_ids": [pid], "names": {pid: "ZL50 базовое"}}))
    _ok(client.post(f"/api/catalog/executions/{prop['created'][0]['id']}/decision", headers=H(GD), json={"approve": True, "brand_name": "BH-50"}))
    assert _ok(client.patch(f"/api/deals/{did}/kp-settings", headers=H(CD), json={"kp_brand": "company"}))["kp_brand"] == "company"
    # задача 7: справка цен при вводе позиции — менеджер видит цены завода, цены клиентам — нет
    ref_m = _ok(client.get(f"/api/catalog/price-reference?factory_id={factories[0]['id']}&model=ZL50", headers=H(MGR)))
    assert ref_m["factory_prices"] and ref_m["client_prices"] is None
    assert _ok(client.get(f"/api/catalog/price-reference?factory_id={factories[0]['id']}&model=ZL50", headers=H(GD)))["client_prices"] is not None
    # объединение дублей исполнений: ссылки позиции переходят, дубль в архив
    rows_now = _ok(client.get(f"/api/deals/{did}/positions/{pid}/tech-params", headers=H(MGR)))["rows"]
    rows_now[0]["value_ru"] = "6"                                                          # другой набор параметров → новое исполнение
    _ok(client.put(f"/api/deals/{did}/positions/{pid}/tech-params", headers=H(MGR), json=rows_now))
    prop2 = _ok(client.post(f"/api/deals/{did}/catalog/propose", headers=H(MGR), json={"position_ids": [pid], "names": {pid: "ZL50 дубль"}}))
    dupe = prop2["created"][0]["id"]
    _ok(client.post(f"/api/catalog/executions/{dupe}/decision", headers=H(CD), json={"approve": True}))
    assert client.post(f"/api/catalog/executions/{dupe}/merge", headers=H(MGR), json={"into_id": prop["created"][0]["id"]}).status_code == 403
    mg = _ok(client.post(f"/api/catalog/executions/{dupe}/merge", headers=H(CD), json={"into_id": prop["created"][0]["id"]}))
    assert mg["into"] == prop["created"][0]["id"]
    assert any(e["id"] == dupe for e in _ok(client.get("/api/catalog/archive", headers=H(CD)))["executions"])
    # задача 5: альтернативы — выключение при двух предложениях требует выбрать, какое оставить
    _ok(client.patch(f"/api/deals/{did}", headers=H(MGR), json={"has_alternatives": True}))
    _ok(client.post(f"/api/deals/{did}/variants", headers=H(MGR), json={"name": "Предложение Б", "position_ids": [pid]}), 201)
    dd = _ok(client.get(f"/api/deals/{did}", headers=H(MGR)))
    offers_ab = [v for v in dd["variants"] if not v.get("is_default")]
    assert len(offers_ab) == 2
    r = client.patch(f"/api/deals/{did}", headers=H(MGR), json={"has_alternatives": False})
    assert r.status_code == 409 and len(r.json()["detail"]["choose_variant"]) == 2
    keep = offers_ab[1]["id"]
    dd = _ok(client.patch(f"/api/deals/{did}", headers=H(MGR), json={"has_alternatives": False, "keep_variant_id": keep}))
    assert [v["id"] for v in dd["variants"]] == [keep] and dd["offer"]["id"] == keep
    cmp2 = _ok(client.get(f"/api/deals/{did}/offers/compare?position_id={pid}", headers=H(MGR)))
    row0 = next(r for r in cmp2["rows"] if r["no"] == first["no"])                      # «5.5» против «не менее 5 т» — соответствует
    assert [c["status"] for c in row0["cells"]].count("match") == 1
    assert client.patch(f"/api/deals/{did}/kp-settings", headers=H(MGR), json={"kp_images_mode": "both"}).status_code == 403
    assert _ok(client.patch(f"/api/deals/{did}/kp-settings", headers=H(CD), json={"kp_images_mode": "both"}))["kp_images_mode"] == "both"


def test_access_levels_admin(client):
    """Задача 11: матрица прав в интерфейсе — правило по роли, исключение для сотрудника, защита ГД, предпросмотр."""
    assert client.get("/api/admin/access", headers=H(CD)).status_code == 403
    m = _ok(client.get("/api/admin/access", headers=H(GD)))
    assert any(g["code"] == "client_price_history" for g in m["groups"]) and any(r["role"] == "gd" for r in m["roles"])
    me = _ok(client.get("/api/auth/me", headers=H(MGR)))
    assert me["perms"]["client_price_history"] == 0 and me["perms"]["tech_params"] == 2
    _ok(client.put("/api/admin/access/manager/client_price_history", headers=H(ADM), json={"level": 1}))
    assert _ok(client.get("/api/auth/me", headers=H(MGR)))["perms"]["client_price_history"] == 1
    assert client.put("/api/admin/access/gd/access_admin", headers=H(ADM), json={"level": 0}).status_code == 422
    users = m["users"]
    mgr = next(u for u in users if "manager" in u["roles"])
    ov = _ok(client.post("/api/admin/access/overrides", headers=H(GD), json={"user_id": mgr["id"], "group": "client_price_history", "level": 0, "comment": "стажёр"}), 201)
    assert _ok(client.get("/api/auth/me", headers=H(MGR)))["perms"]["client_price_history"] == 0
    pv = _ok(client.get(f"/api/admin/access/preview?user_id={mgr['id']}", headers=H(GD)))
    assert "kp_history" in pv["denied_fields"]
    _ok(client.delete(f"/api/admin/access/overrides/{ov['id']}", headers=H(GD)), 204)
    _ok(client.put("/api/admin/access/manager/client_price_history", headers=H(ADM), json={"level": 0}))
    assert _ok(client.get("/api/auth/me", headers=H(MGR)))["perms"]["client_price_history"] == 0


def test_catalog_add_clients_by_inn_factories_settings(client, tmp_path):
    """Каталог: добавление вручную/из RFQ/списком (менеджер → на подтверждение, КД — сразу); клиент по ИНН; справочник заводов;
    описание настроек; разделы и подсветка несовпадения в таблице теххарактеристик."""
    import json
    from openpyxl import Workbook
    factories = _ok(client.get("/api/refs/factories", headers=H(MGR)))
    # --- каталог: вручную
    body = {"factory_id": factories[0]["id"], "machine_type": "Самосвал", "model": "KT-30", "execution_name": "KT-30 базовое",
            "tech_params": [{"code": "1.5", "name_ru": "Длина", "unit": "мм", "value_ru": "8 200"}], "unit_price": 1_200_000, "currency": "CNY"}
    assert client.post("/api/catalog/equipment", headers=H(LOG), json=body).status_code == 403
    m = _ok(client.post("/api/catalog/equipment", headers=H(MGR), json=body), 201)
    assert m["status"] == "pending" and m["created"] and "подтверждение" in m["message"]
    assert any(p["id"] == m["id"] for p in _ok(client.get("/api/catalog/pending", headers=H(CD))))
    _ok(client.post(f"/api/catalog/executions/{m['id']}/decision", headers=H(CD), json={"approve": True}))
    c = _ok(client.post("/api/catalog/equipment", headers=H(CD), json={**body, "model": "KT-40", "brand_name": "BH-40"}), 201)
    assert c["status"] == "approved" and c["brand_name"] == "BH-40"
    again = _ok(client.post("/api/catalog/equipment", headers=H(CD), json={**body, "model": "KT-40"}), 201)
    assert again["created"] is False and again["id"] == c["id"]                         # тот же набор параметров — без дубля
    tree = _ok(client.get("/api/catalog/tree", headers=H(MGR)))
    assert any(mm["model"] == "KT-30" for f in tree for t in f["types"] for mm in t["models"])
    # --- каталог: файл RFQ одной машины → данные для формы
    rfq = fill_rfq_template(str(tmp_path / "rfq.xlsx"), FILLED, ZIP, option_names=OPTION_NAMES)
    with open(rfq, "rb") as fh:
        pv = _ok(client.post("/api/catalog/equipment/import/preview", headers=H(MGR), files={"file": ("rfq.xlsx", fh)}))
    assert pv["kind"] == "rfq" and pv["item"]["model"] == "XDR80T" and pv["filled"] > 20
    assert all(r["section"] for r in pv["item"]["tech_params"])
    # --- каталог: список моделей
    wb = Workbook(); ws = wb.active
    ws.append(["Перечень моделей"]); ws.append(["Завод", "Тип машины", "Модель", "Цена", "Валюта"])
    ws.append([factories[1]["name"], "Погрузчик", "LW-600", 700000, "CNY"]); ws.append([factories[1]["name"], "Погрузчик", "LW-800", 900000, "CNY"])
    f = tmp_path / "list.xlsx"; wb.save(f)
    with open(f, "rb") as fh:
        pl = _ok(client.post("/api/catalog/equipment/import/preview", headers=H(MGR), files={"file": ("list.xlsx", fh)}))
    assert pl["kind"] == "list"
    mp = pl["sheets"][0]["mapping"]
    assert mp["header_row"] == 1 and mp["columns"]["model"] == 2 and mp["columns"]["factory"] == 0
    with open(f, "rb") as fh:
        ap = _ok(client.post("/api/catalog/equipment/import/apply", headers=H(MGR), files={"file": ("list.xlsx", fh)},
                             data={"mapping": json.dumps({"sheet": pl["sheets"][0]["name"], **mp})}))
    assert len(ap["added"]) == 2 and all(a["status"] == "pending" for a in ap["added"])
    # --- клиент по ИНН: неверный ИНН 422; источник без ключа — 503 (понятная ошибка); создание с реквизитами
    assert client.get("/api/refs/clients/lookup?inn=7707083890", headers=H(MGR)).status_code == 422
    assert client.get("/api/refs/clients/lookup?inn=7707083893", headers=H(MGR)).status_code in (503, 502)
    assert client.post("/api/refs/clients", headers=H(MGR), json={"name": "Тест", "inn": "7707083890"}).status_code == 422
    cl = _ok(client.post("/api/refs/clients", headers=H(MGR), json={"name": "ПАО СБЕРБАНК", "inn": "7707083893", "kpp": "773601001", "ogrn": "1027700132195",
                                                                     "director": "ПРЕЗИДЕНТ, Греф Герман Оскарович", "legal_status": "ACTIVE",
                                                                     "registry": {"source": "dadata"}}), 201)
    assert cl["kpp"] == "773601001" and cl["registry_checked_at"]
    # --- справочник заводов: контакты по направлениям, WeCom «Заявки» первым
    fz = _ok(client.post("/api/refs/factories", headers=H(MGR), json={
        "name": "Test Mining Machinery Co., Ltd.", "name_zh": "测试矿山机械有限公司", "province": "Shandong", "uscc": "91370700MA3C000000",
        "contacts": [{"name": "Li Wei", "roles": ["finance"], "email": "fin@test.cn", "wecom_id": "wm_fin"},
                     {"name": "Zhang San", "roles": ["rfq", "sales"], "email": "sales@test.cn", "wechat": "zhangsan_wx", "wecom_id": "wm_rfq", "primary": True}]}), 201)
    assert fz["wecom_external_ids"][0] == "wm_rfq"
    assert client.post("/api/refs/factories", headers=H(FD), json={"name": "X"}).status_code == 403
    card = _ok(client.get(f"/api/refs/factories/{fz['id']}", headers=H(MGR)))
    assert card["contacts"][1]["wechat"] == "zhangsan_wx" and "models" in card
    _ok(client.put(f"/api/refs/factories/{fz['id']}", headers=H(MGR), json={**{k: card[k] for k in ("name", "name_zh", "contacts")}, "bank_details": {"swift": "BKCHCNBJ"}}))
    up = _ok(client.put(f"/api/refs/factories/{fz['id']}", headers=H(LOG), json={**{k: card[k] for k in ("name", "name_zh", "contacts")}, "website": "https://test.cn"}))
    assert up["website"] == "https://test.cn" and "bank_details" not in up              # логист не видит реквизиты…
    assert _ok(client.get(f"/api/refs/factories/{fz['id']}", headers=H(MGR)))["bank_details"] == {"swift": "BKCHCNBJ"}   # …и не стирает их
    assert all("bank_details" not in f_ for f_ in _ok(client.get("/api/refs/factories", headers=H(LOG))))
    assert "rfq" in _ok(client.get("/api/refs/factories/contact-roles", headers=H(MGR)))
    # --- настройки: у каждого ключа есть понятное название и группа
    meta = _ok(client.get("/api/admin/settings/meta", headers=H(FD)))
    assert meta["items"]["margin_threshold_pct"]["title"] == "Порог маржи" and not [k for k, v in meta["items"].items() if v["group"] == "Прочее"]
    # --- таблица теххарактеристик: разделы и подсветка несовпадения с требованием
    clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
    deal = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "разделы"}), 201)
    with open(rfq, "rb") as fh:
        imp = _ok(client.post(f"/api/deals/{deal['id']}/rfq/import", headers=H(MGR), files={"file": ("rfq.xlsx", fh)}), 201)
    tp = _ok(client.get(f"/api/deals/{deal['id']}/positions/{imp['position']['id']}/tech-params", headers=H(MGR)))
    assert all(r["section"] and r["section"][0].isdigit() for r in tp["rows"])
    assert {r["req_status"] for r in tp["rows"]} <= {"match", "mismatch", "none"} and "mismatched" in tp
