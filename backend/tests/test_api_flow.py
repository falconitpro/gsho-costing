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
LAW = "lawyer@example.com"


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
    assert pos["rfq_import_id"]                                                         # источник позиции — RFQ, а не «вручную»
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
    # задача 4: таблица технических характеристик из RFQ, запрос заводу (файл), обязательные параметры
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
    assert lg["logistics"]["duty_pct"] == 5.0 and lg["logistics"]["vat_pct"] == 22.0   # ставки из справочника
    # код ТН ВЭД вводит логист — он сохраняется и в карточке оборудования каталога (подсказка в следующих сделках)
    from app.db import SessionLocal as _S
    from app.models import EquipmentModel as _EM
    with _S() as _db:
        _m = _db.query(_EM).filter(_EM.model == "XDR80T").first()
        assert _m is not None and _m.default_hs_code and _m.hs_info.get("code") == _m.default_hs_code
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
                                      "local_completion", "external_commission", "manager_commission", "cd_commission"}
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
    # КП: пересобрать, выгрузить Word, получатели, отправить из системы (почта console) — отметка «КП отправлено»
    _ok(client.post(f"/api/deals/{did}/kp/build", headers=H(MGR)))
    kf = client.get(f"/api/deals/{did}/kp/file?format=docx", headers=H(MGR))
    assert kf.status_code == 200 and kf.content[:2] == b"PK"
    # обычная ссылка (без заголовка dev-пользователя) — по сессии, выданной при работе с API: скачивание КП и Excel работает
    assert client.get(f"/api/deals/{did}/kp/file?format=docx").status_code == 200
    rc = _ok(client.get(f"/api/deals/{did}/kp/recipients", headers=H(MGR)))
    assert rc["recipients"][0]["email"] == "mech@rudnik.example"
    # изменение КП (КД): скидка 5 % и доп. услуга — новая редакция с файлом, ценой, маржой; история КП
    rv0 = _ok(client.get(f"/api/deals/{did}/kp/revisions", headers=H(CD)))
    vid0 = rv0["variants"][0]["id"]
    ch = {vid0: {"adjustments": [{"kind": "discount", "mode": "pct", "value": 5}], "extras": [{"name": "Обучение операторов", "price_rub": 300000}],
                 "configuration": "базовая + система пожаротушения"}}
    assert client.post(f"/api/deals/{did}/kp/revisions", headers=H(MGR), json={"changes": ch, "description": "скидка"}).status_code == 403
    pv = _ok(client.post(f"/api/deals/{did}/kp/revisions/preview", headers=H(CD), json={"changes": ch}))["variants"][vid0]
    assert pv["total"] == round((pv["base"] + 300000) * 0.95, 2) and pv["margin_pct"] is not None
    nr = _ok(client.post(f"/api/deals/{did}/kp/revisions", headers=H(CD), json={"changes": ch, "description": "Скидка 5 % по запросу клиента, обучение"}), 201)
    assert nr["no"] == 1 and nr["price_rub"] == pv["total"]
    hist = _ok(client.get(f"/api/deals/{did}/kp/revisions", headers=H(CD)))["rows"]
    assert [r["no"] for r in hist] == [0, 1] and hist[1]["price_before_rub"] == hist[0]["price_rub"]
    assert client.get(f"/api/deals/{did}/kp/revisions/{nr['id']}/file?format=docx", headers=H(MGR)).content[:2] == b"PK"
    # почта не подключена — отправка из системы недоступна, КП отправляют вручную и отмечают
    assert rc["mail_configured"] is False
    assert client.post(f"/api/deals/{did}/kp/send", headers=H(MGR), json={"to": ["mech@rudnik.example"], "format": "docx"}).status_code == 409
    assert _ok(client.post(f"/api/deals/{did}/kp-sent", headers=H(MGR), json={"recipients": ["mech@rudnik.example"]}))["status"] == "kp_sent"
    assert _ok(client.get(f"/api/deals/{did}/kp/revisions", headers=H(CD)))["rows"][1]["sent_at"]   # в историю цен ушла редакция
    card_cd = _ok(client.get(f"/api/refs/clients/{clients[0]['id']}", headers=H(CD)))
    assert card_cd["kp_history"] and card_cd["kp_history"][0]["deal_number"] == deal["number"] and card_cd["kp_history"][0]["versions"]
    card_mgr = _ok(client.get(f"/api/refs/clients/{clients[0]['id']}", headers=H(MGR)))
    assert "kp_history" not in card_mgr and "last_kp" not in card_mgr                # историю КП видят только КД и ЗГД
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
    # корректировка модели КД с историей; менеджер не может
    mod_id = next(m["id"] for f in tree for t in f["types"] for m in t["models"] if any(e["name"] == "XDR80T базовое" for e in m["executions"]))
    assert client.patch(f"/api/catalog/models/{mod_id}", headers=H(MGR), json={"trademark": "X"}).status_code == 403
    _ok(client.patch(f"/api/catalog/models/{mod_id}", headers=H(CD), json={"machine_type": "Шахтный самосвал", "trademark": "XCMG", "comment": "марка завода"}))
    mh = _ok(client.get(f"/api/catalog/models/{mod_id}/history", headers=H(CD)))
    assert any(h["action"] == "изменение" and any(c["field"] == "торговая марка" and c["after"] == "XCMG" for c in h["changes"]) and h["comment"] == "марка завода" for h in mh)
    card_m = _ok(client.get(f"/api/catalog/models/{mod_id}/card", headers=H(CD)))
    assert card_m["trademark"] == "XCMG" and card_m["manually_checked"]
    # заявка клиенту по машине из каталога: позиция с характеристиками исполнения и последней ценой завода
    ex_id = next(e["id"] for f in tree for t in f["types"] for m in t["models"] for e in m["executions"] if e["name"] == "XDR80T базовое")
    assert client.post(f"/api/catalog/executions/{ex_id}/create-deal", headers=H(LOG), json={"client_id": clients[0]["id"]}).status_code == 403
    nd = _ok(client.post(f"/api/catalog/executions/{ex_id}/create-deal", headers=H(MGR), json={"client_id": clients[0]["id"], "qty": 3, "site": "Карьер Южный"}), 201)
    assert nd["price_known"] and nd["status"] == "draft" and len(nd["positions"]) == 1
    np_ = nd["positions"][0]
    assert np_["execution_id"] == ex_id and np_["qty"] == 3 and np_["unit_price"] > 0 and np_["trademark"] == "XCMG"
    assert np_["logistics_params"] and _ok(client.get(f"/api/deals/{nd['id']}/positions/{np_['id']}/tech-params", headers=H(MGR)))["rows"]
    # позиция из каталога в существующей заявке без цены — цена из истории завода
    pc = _ok(client.post(f"/api/deals/{nd['id']}/positions", headers=H(MGR), json={"factory_id": np_["factory_id"], "model": np_["model"], "qty": 1, "unit_price": 0,
                                                                                    "execution_id": ex_id}), 201)
    assert pc["unit_price"] > 0 and isinstance(pc["from_catalog"], dict)
    assert client.post(f"/api/deals/{did}/outcome", headers=H(LOG), json={"outcome": "won", "reason": "x"}).status_code == 403
    assert client.post(f"/api/deals/{did}/outcome", headers=H(CD), json={"outcome": "won", "reason": " "}).status_code == 422
    out = _ok(client.post(f"/api/deals/{did}/outcome", headers=H(CD), json={"outcome": "won", "reason": "лучшая цена"}))   # КД отмечает результат
    assert out["status"] == "contract"
    dw = _ok(client.get(f"/api/deals/{did}", headers=H(MGR)))
    assert dw["status_detail"] == "Заключение контракта · подготовка проекта контракта"
    assert any(t["deal_id"] == did and "подготовка проекта контракта" in t["title"] for t in _ok(client.get("/api/tasks/my", headers=H(MGR))))
    # подэтапы заключения контракта: по порядку, с правами и задачей следующему
    cs = _ok(client.get(f"/api/deals/{did}/execution/stages", headers=H(MGR)))["contract_steps"]
    assert cs[0]["code"] == "draft" and cs[0]["state"] == "current" and all(c["state"] == "pending" for c in cs[1:])
    _ok(client.post(f"/api/deals/{did}/execution/contract-step", headers=H(MGR), json={"code": "draft", "comment": "проект по КП"}))
    assert client.post(f"/api/deals/{did}/execution/contract-step", headers=H(MGR), json={"code": "legal_review"}).status_code == 403   # только юрист, КД, ЗГД
    _ok(client.post(f"/api/deals/{did}/execution/contract-step", headers=H(LAW), json={"code": "legal_review", "comment": "замечаний нет"}))
    _ok(client.post(f"/api/deals/{did}/execution/contract-step", headers=H(MGR), json={"code": "client_review"}))
    assert client.post(f"/api/deals/{did}/execution/contract-step", headers=H(MGR), json={"code": "internal_approval"}).status_code == 403
    r_ia = _ok(client.post(f"/api/deals/{did}/execution/contract-step", headers=H(CD), json={"code": "internal_approval", "comment": "условия = КП"}))
    assert r_ia["status_detail"].endswith("контракт подписан клиентом")
    assert client.post(f"/api/deals/{did}/execution/contract-step", headers=H(MGR), json={"code": "client_signed"}).status_code == 422   # нужен номер
    assert client.post(f"/api/deals/{did}/execution/contract-step", headers=H(MGR), json={"code": "nope"}).status_code == 404
    assert next(x for x in _ok(client.get("/api/deals", headers=H(MGR))) if x["id"] == did)["status_detail"].startswith("Заключение контракта")
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
    # контроль сроков: базовый план зафиксирован, уточнённая дата готовности сдвигает прогноз поставки, отклонение в днях
    rd = next(s for s in r["stages"] if s["code"] == "readiness")
    dl = next(s for s in r["stages"] if s["code"] == "delivery")
    assert rd["baseline_on"] and dl["baseline_on"] and dl["deviation_days"] is not None
    from datetime import date as _d, timedelta as _td
    later = (_d.fromisoformat(rd["planned_on"]) + _td(days=20)).isoformat()
    rf = _ok(client.post(f"/api/deals/{did}/execution/stages/{rd['id']}/refine", headers=H(LOG), json={"refined_on": later, "note": "завод перенёс готовность"}))
    rd2 = next(s for s in rf["stages"] if s["id"] == rd["id"])
    dl2 = next(s for s in rf["stages"] if s["code"] == "delivery")
    assert rd2["refined_on"] == later and rd2["baseline_on"] == rd["baseline_on"]
    assert rd2["deviation_days"] == (_d.fromisoformat(later) - _d.fromisoformat(rd["baseline_on"])).days
    assert dl2["planned_on"] > dl["planned_on"] and dl2["deviation_days"] > dl["deviation_days"]
    # --- план/факт (стадия исполнения)
    _ok(client.post(f"/api/deals/{did}/fact", headers=H(LOG), json={"kind": "cost_item", "cost_item_code": "intl_freight", "amount": 2000000, "on": "2027-03-01"}), 201)
    assert client.post(f"/api/deals/{did}/fact", headers=H(LOG), json={"kind": "cost_item", "cost_item_code": "service", "amount": 1, "on": "2027-03-01"}).status_code == 403
    pf = _ok(client.get(f"/api/deals/{did}/plan-fact", headers=H(FD)))
    row = next(r for r in pf["rows"] if r["code"] == "intl_freight")
    assert row["fact"] == 2000000 and row["deviation_rub"] is not None
    assert any(e["kind"] == "payment_in" for e in pf["entries"])                     # платёж ФД попал в план/факт
    # экран план/факта: что может вносить роль; валюта без курса — 422; ошибочную запись удаляет автор
    fl = _ok(client.get(f"/api/deals/{did}/fact/form", headers=H(LOG)))
    assert fl["open"] and "intl_freight" in {i["code"] for i in fl["items"]} and "service" not in {i["code"] for i in fl["items"]} and not fl["payments"]
    assert not fl["can_report"] and all(e["entered_by"] for e in fl["entries"])
    assert _ok(client.get(f"/api/deals/{did}/fact/form", headers=H(FD)))["payments"] and _ok(client.get(f"/api/deals/{did}/fact/form", headers=H(FD)))["can_close"]
    assert client.post(f"/api/deals/{did}/fact", headers=H(LOG), json={"kind": "cost_item", "cost_item_code": "insurance", "amount": 100, "currency": "CNY", "on": "2027-03-01"}).status_code == 422
    wrong = _ok(client.post(f"/api/deals/{did}/fact", headers=H(LOG), json={"kind": "cost_item", "cost_item_code": "insurance", "amount": 100, "currency": "CNY", "fx_rate": 12.0, "on": "2027-03-01"}), 201)
    pf2 = _ok(client.get(f"/api/deals/{did}/plan-fact", headers=H(FD)))
    assert next(r for r in pf2["rows"] if r["code"] == "insurance")["fact"] == 1200   # валюта × курс
    assert client.delete(f"/api/deals/{did}/fact/{wrong['entry']['id']}", headers=H(MGR)).status_code == 403
    _ok(client.delete(f"/api/deals/{did}/fact/{wrong['entry']['id']}", headers=H(LOG)))
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
    # ФД отказывает → доработка у КД; КД настаивает на цене → виза ЗГД → утверждено
    assert client.post(f"/api/deals/{did}/insist", headers=H(CD), json={"comment": "x"}).status_code == 409   # отказа ФД ещё нет
    assert _ok(client.post(f"/api/deals/{did}/visa", headers=H(FD), json={"approved": False, "comment": "маржа ниже порога"}))["status"] == "rework"
    calc_rw = _ok(client.get(f"/api/deals/{did}/calc", headers=H(CD)))
    assert calc_rw["fd_declined"]["comment"] == "маржа ниже порога"
    assert client.post(f"/api/deals/{did}/insist", headers=H(MGR), json={"comment": "x"}).status_code == 403
    assert client.post(f"/api/deals/{did}/insist", headers=H(CD), json={"comment": ""}).status_code == 422
    assert _ok(client.post(f"/api/deals/{did}/insist", headers=H(CD), json={"comment": "стратегический клиент, вход на рудник"}))["status"] == "gd_visa"
    assert any(t["deal_id"] == did and t["block"] == "visa" for t in _ok(client.get("/api/tasks/my", headers=H("gd@example.com"))))
    assert _ok(client.post(f"/api/deals/{did}/visa", headers=H("gd@example.com"), json={"approved": True, "comment": "согласовано"}))["status"] == "approved"
    # возврат на доработку создаёт версию и задачу логисту
    rw = _ok(client.post(f"/api/deals/{did}/rework", headers=H(CD), json={"block": "logistics", "reason": "уточнить фрахт"}))
    assert rw["status"] == "rework" and rw["version_no"] >= 2
    assert any(t["deal_id"] == did and t["block"] == "logistics" for t in _ok(client.get("/api/tasks/my", headers=H(LOG))))
    # ФД тоже возвращает на доработку — в т.ч. ЗГД; дату отчёта меняют КД / ЗГД
    assert _ok(client.post(f"/api/deals/{did}/rework", headers=H(FD), json={"block": "gd", "reason": "проверить условия"}))["status"] == "rework"
    assert client.put(f"/api/deals/{did}/report-date", headers=H(FD), json={"report_date": "2026-11-01"}).status_code == 403
    assert _ok(client.put(f"/api/deals/{did}/report-date", headers=H(CD), json={"report_date": "2026-11-01"}))["report_date"] == "2026-11-01"
    assert _ok(client.get(f"/api/deals/{did}/calc", headers=H(CD)))["input"]["calc_date"] == "2026-11-01"
    _ok(client.put(f"/api/deals/{did}/report-date", headers=H(CD), json={"report_date": None}))
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
    # задача 9: запрос двум заводам (файл RU+CN, строка опции РФ не включена), ответ второго завода, сравнение, отбор КД
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
    """Задача 11: матрица прав в интерфейсе — правило по роли, исключение для сотрудника, защита ЗГД, предпросмотр."""
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
    описание настроек; разделы и подсветка несовпадения в таблице технических характеристик."""
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
    # --- торговые марки: сопоставление «завод → марка», обозначение = марка + модель
    mk = _ok(client.put(f"/api/refs/factories/{fz['id']}", headers=H(MGR), json={**{k: card[k] for k in ("name", "name_zh", "contacts")},
                                                                                  "trademarks": [" SHILI ", "shili", "", "SL"]}))
    assert mk["trademarks"] == ["SHILI", "SL"]
    cl0 = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    dtm = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": cl0["id"], "site": "марки"}), 201)
    ptm = _ok(client.post(f"/api/deals/{dtm['id']}/positions", headers=H(MGR), json={"factory_id": fz["id"], "model": "FL-0.5(C)",
                                                                                     "machine_type": "Погрузчик", "unit_price": 100000}), 201)
    assert ptm["trademark"] == "SHILI"                                        # подставлена по заводу
    ptm2 = _ok(client.patch(f"/api/deals/{dtm['id']}/positions/{ptm['id']}", headers=H(MGR), json={"trademark": "XGMA"}))
    assert ptm2["trademark"] == "XGMA"
    assert "XGMA" in _ok(client.get(f"/api/refs/factories/{fz['id']}", headers=H(MGR)))["trademarks"]   # новая марка запомнена за заводом
    # --- настройки: у каждого ключа есть понятное название и группа
    meta = _ok(client.get("/api/admin/settings/meta", headers=H(FD)))
    assert meta["items"]["margin_threshold_pct"]["title"] == "Порог маржи" and not [k for k, v in meta["items"].items() if v["group"] == "Прочее"]
    # --- таблица технических характеристик: разделы и подсветка несовпадения с требованием
    clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
    deal = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "разделы"}), 201)
    with open(rfq, "rb") as fh:
        imp = _ok(client.post(f"/api/deals/{deal['id']}/rfq/import", headers=H(MGR), files={"file": ("rfq.xlsx", fh)}), 201)
    tp = _ok(client.get(f"/api/deals/{deal['id']}/positions/{imp['position']['id']}/tech-params", headers=H(MGR)))
    assert all(r["section"] and r["section"][0].isdigit() for r in tp["rows"])
    assert {r["req_status"] for r in tp["rows"]} <= {"match", "mismatch", "none"} and "mismatched" in tp
    # --- тип машины, изменённый в таблице, — в позицию (перечень позиций у всех ролей); страна — только названием
    pid = imp["position"]["id"]
    rows = tp["rows"]
    for r in rows:
        if r["code"] == "1.1":
            r["value_ru"] = "Погрузчик шахтный"
        if r["code"] == "1.3":
            r["value_ru"] = "China, Shandong province, Jinan"
    sv = _ok(client.put(f"/api/deals/{deal['id']}/positions/{pid}/tech-params", headers=H(MGR), json=rows))
    assert sv["position"]["machine_type"] == "Погрузчик шахтный"
    assert next(r for r in sv["rows"] if r["code"] == "1.3")["value_ru"] == "Китай"
    got = _ok(client.get(f"/api/deals/{deal['id']}", headers=H(CD)))
    assert got["positions"][0]["machine_type"] == "Погрузчик шахтный"
    _ok(client.patch(f"/api/deals/{deal['id']}/positions/{pid}", headers=H(MGR), json={"machine_type": "ПДМ"}))
    tp2 = _ok(client.get(f"/api/deals/{deal['id']}/positions/{pid}/tech-params", headers=H(MGR)))
    assert next(r for r in tp2["rows"] if r["code"] == "1.1")["value_ru"] == "ПДМ"     # и обратно: карточка позиции → таблица
    assert "Китай" in _ok(client.get("/api/refs/countries", headers=H(MGR)))
    # --- ручной ввод формой-таблицей: значения, требования заказчика и комментарии — в те же строки таблицы
    _ok(client.patch(f"/api/deals/{deal['id']}/positions/{pid}", headers=H(MGR), json={"form_rows": [
        {"code": "1.5", "value": "8 300", "requirement": "не более 8 500", "comment": "по чертежу"}]}))
    r15 = next(r for r in _ok(client.get(f"/api/deals/{deal['id']}/positions/{pid}/tech-params", headers=H(MGR)))["rows"] if r["code"] == "1.5")
    assert (r15["value_ru"], r15["customer_requirement_ru"], r15["comment_ru"]) == ("8 300", "не более 8 500", "по чертежу")
    assert r15["req_status"] == "match"
    # --- логист видит тот же тип машины, что в таблице (поле 1.1 с меткой L — в данных логиста)
    rows3 = _ok(client.get(f"/api/deals/{deal['id']}/positions/{pid}/tech-params", headers=H(MGR)))["rows"]
    for r in rows3:
        if r["code"] == "1.1":
            r["value_ru"] = "Самосвал шахтный"
    _ok(client.put(f"/api/deals/{deal['id']}/positions/{pid}/tech-params", headers=H(MGR), json=rows3))
    pl = next(p_ for p_ in _ok(client.get(f"/api/deals/{deal['id']}", headers=H(MGR)))["positions"] if p_["id"] == pid)
    assert pl["machine_type"] == "Самосвал шахтный"
    if "1.1" in (pl.get("logistics_params") or {}):
        assert pl["logistics_params"]["1.1"]["value"] == "Самосвал шахтный"
    snap = ((pl.get("tech_params") or {}).get("_rfq") or {}).get("fields") or {}
    if "1.1" in snap:
        assert snap["1.1"]["value"] == "Самосвал шахтный"
    # --- настройки: КД меняет внешнюю комиссию, комиссию КД — только ЗГД; экспертные точки — в едином виде
    _ok(client.put("/api/admin/settings/external_commission_pct", headers=H(CD), json={"value": 2.5, "comment": "тест"}))
    assert client.put("/api/admin/settings/cd_commission_pct", headers=H(CD), json={"value": 1.0}).status_code == 403
    mt = _ok(client.get("/api/admin/settings/meta", headers=H(CD)))
    assert mt["items"]["external_commission_pct"]["editable"] and not mt["items"]["cd_commission_pct"]["editable"]
    assert mt["items"]["region_norms"]["type"] == "dict_table" and mt["items"]["fx_expert_points"]["type"] == "expert_points"
    _ok(client.put("/api/admin/settings/fx_expert_points", headers=H(FD), json={"value": {"cny": {"2027-03-31": 12.5}}}))
    assert _ok(client.get("/api/admin/settings", headers=H(FD)))["fx_expert_points"] == {"CNY": [{"on": "2027-03-31", "rate": 12.5}]}
    s_ = _ok(client.get("/api/admin/settings", headers=H(FD)))
    assert s_["commissioning_days"] == 5 and s_["unplanned_reserve_pct"] == 3.0 and s_["sla_days"]["logistics"] == 1
    # --- удалять строки таблицы технических характеристик могут только КД, ЗГД и администратор
    rows4 = _ok(client.get(f"/api/deals/{deal['id']}/positions/{pid}/tech-params", headers=H(MGR)))["rows"]
    assert client.put(f"/api/deals/{deal['id']}/positions/{pid}/tech-params", headers=H(MGR), json=rows4[1:]).status_code == 403
    assert len(_ok(client.put(f"/api/deals/{deal['id']}/positions/{pid}/tech-params", headers=H(CD), json=rows4[1:]))["rows"]) == len(rows4) - 1
    # --- карточка машины в каталоге: характеристики для всех; цены и аналитическая справка — КД и ЗГД
    tree_ = _ok(client.get("/api/catalog/tree", headers=H(MGR)))
    mid = tree_[0]["types"][0]["models"][0]["id"]
    cm = _ok(client.get(f"/api/catalog/models/{mid}/card", headers=H(MGR)))
    assert cm["executions"] and not cm["with_prices"] and "factory_prices" not in cm
    cc = _ok(client.get(f"/api/catalog/models/{mid}/card", headers=H(CD)))
    assert cc["with_prices"] and "factory" in cc["analytics"] and "client" in cc["analytics"] and isinstance(cc["factory_prices"], list)


def test_team_contacts_and_task_notifications(client):
    """Карточка контактов исполнителя; каналы выбирают КД / ЗГД / администратор; новая задача — уведомление по каналам."""
    t = _ok(client.get("/api/team", headers=H(MGR)))
    log_u = next(u for u in t["users"] if u["email"] == LOG)
    assert "wechat" in t["channels"] and log_u["contacts"]["mobile"] == ""
    _ok(client.put(f"/api/team/{log_u['id']}/contacts", headers=H(LOG), json={"contacts": {"mobile": "+7 900 111-22-33", "wechat": "oksana_k", "telegram": "@oksana"}}))
    assert client.put(f"/api/team/{log_u['id']}/contacts", headers=H(MGR), json={"contacts": {"mobile": "1"}}).status_code == 403
    assert client.put(f"/api/team/{log_u['id']}/channels", headers=H(LOG), json={"channels": ["email"]}).status_code == 403
    c = _ok(client.put(f"/api/team/{log_u['id']}/channels", headers=H(CD), json={"channels": ["email", "wechat", "telegram"]}))
    assert c["notify_channels"] == ["app", "email", "wechat", "telegram"] and c["contacts"]["wechat"] == "oksana_k"
    # новая задача логисту (заявка на расчёт) — уведомления по всем его каналам
    clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
    factories = _ok(client.get("/api/refs/factories", headers=H(MGR)))
    d = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "уведомления"}), 201)
    _ok(client.post(f"/api/deals/{d['id']}/positions", headers=H(MGR), json={"factory_id": factories[0]["id"], "model": "N-1", "machine_type": "ПДМ",
                                                                            "unit_price": 100000}), 201)
    _ok(client.post(f"/api/deals/{d['id']}/send-to-calc", headers=H(MGR)))
    log = _ok(client.get(f"/api/team/{log_u['id']}/notifications", headers=H(LOG)))["log"]
    task_msgs = [r for r in log if d["number"] in r["text"]]
    assert {r["channel"] for r in task_msgs} >= {"app", "email", "wechat", "telegram"}
    assert next(r for r in task_msgs if r["channel"] == "app")["status"] == "sent"


def test_admin_screens_and_overdue(client, tmp_path):
    """Администрирование: сотрудники и роли, ТН ВЭД, курсы, шаблоны, журналы; автоматическая проверка просрочек."""
    users = _ok(client.get("/api/admin/users", headers=H(ADM)))
    assert client.get("/api/admin/users", headers=H(MGR)).status_code == 403
    obs = next(u for u in users if u["email"] == "observer@example.com")
    _ok(client.patch(f"/api/admin/users/{obs['id']}", headers=H(ADM), json={"roles": ["observer", "purchasing"]}))
    assert client.patch(f"/api/admin/users/{obs['id']}", headers=H(ADM), json={"roles": ["nobody"]}).status_code == 422
    adm = next(u for u in users if u["email"] == ADM)
    assert client.patch(f"/api/admin/users/{adm['id']}", headers=H(ADM), json={"roles": ["observer"]}).status_code == 409
    assert client.patch(f"/api/admin/users/{adm['id']}", headers=H(ADM), json={"is_active": False}).status_code == 409
    nu = _ok(client.post("/api/admin/users", headers=H(ADM), json={"email": "new.logist@example.com", "display_name": "Новый Логист", "roles": ["logistician"]}), 201)
    assert client.post("/api/admin/users", headers=H(ADM), json={"email": "new.logist@example.com", "display_name": "Дубль", "roles": ["logistician"]}).status_code == 409
    _ok(client.patch(f"/api/admin/users/{nu['id']}", headers=H(ADM), json={"is_active": False}))
    # ТН ВЭД: ручной код, загрузка CSV (с ошибочной строкой), выгрузка
    _ok(client.post("/api/admin/hs", headers=H(ADM), json={"code": "8429 52 1000", "description": "Экскаваторы гусеничные", "duty_pct": 5, "vat_pct": 22, "util_fee": True}), 201)
    hs = _ok(client.get("/api/admin/hs?q=842952", headers=H(ADM)))
    assert hs["total"] == 1 and hs["rows"][0]["code"] == "8429521000"
    csv_ = tmp_path / "hs.csv"
    csv_.write_text("code;description;duty_pct;vat_pct;util_fee;licence;certification\n8429521000;Экскаваторы гусеничные;7,5;22;да;нет;ТР ТС 010/2011\n;пустой код;0;22;;;\n", encoding="utf-8")
    with open(csv_, "rb") as f:
        up = _ok(client.post("/api/admin/hs/upload", headers=H(ADM), files={"file": ("hs.csv", f)}))
    assert up["imported"] == 1 and len(up["errors"]) == 1
    hs = _ok(client.get("/api/admin/hs?q=8429521000", headers=H(ADM)))
    assert hs["total"] == 1 and hs["rows"][0]["duty_pct"] == 7.5            # прежняя ставка закрыта, новая действует
    ex = client.get("/api/admin/hs/export", headers=H(ADM))
    assert ex.status_code == 200 and "8429521000" in ex.content.decode("utf-8-sig")
    # курсы: сводка и ручной курс
    summ = _ok(client.get("/api/admin/fx/summary", headers=H(FD)))
    assert any(s["currency"] == "CNY" for s in summ)
    _ok(client.post("/api/admin/fx/manual", headers=H(ADM), json={"on": "2026-11-03", "currency": "CNY", "rate": 11.5, "comment": "ЦБ недоступен"}), 201)
    # шаблоны: неверный файл RFQ отклоняется, верный становится действующим, можно вернуть встроенный
    bad = tmp_path / "bad.xlsx"
    from openpyxl import Workbook
    Workbook().save(bad)
    with open(bad, "rb") as f:
        assert client.post("/api/admin/templates/rfq", headers=H(ADM), files={"file": ("bad.xlsx", f)}).status_code == 422
    from app.services.rfq_template import DEFAULT_TEMPLATE
    with open(DEFAULT_TEMPLATE, "rb") as f:
        t = _ok(client.post("/api/admin/templates/rfq", headers=H(ADM), files={"file": ("rfq v3.xlsx", f)}), 201)
    tl = _ok(client.get("/api/admin/templates", headers=H(ADM)))
    assert any(x["id"] == t["id"] and x["is_active"] and x["file_exists"] for x in tl["templates"]) and "rfq" in tl["kinds"]
    assert client.get("/api/deals/rfq/template/file", headers=H(MGR)).status_code == 200
    _ok(client.post("/api/admin/templates/rfq/reset", headers=H(ADM)))
    assert not any(x["is_active"] for x in _ok(client.get("/api/admin/templates", headers=H(ADM)))["templates"] if x["kind"] == "rfq")
    _ok(client.post(f"/api/admin/templates/{t['id']}/activate", headers=H(ADM)))
    _ok(client.post("/api/admin/templates/rfq/reset", headers=H(ADM)))
    # журналы
    au = _ok(client.get("/api/admin/audit?entity=hs_codes", headers=H(ADM)))
    assert au and all(a["entity"] == "hs_codes" for a in au) and au[0]["user_name"]
    assert "hs_codes" in _ok(client.get("/api/admin/audit/entities", headers=H(ADM)))
    assert _ok(client.get("/api/admin/audit?q=Экскаваторы", headers=H(ADM)))
    assert isinstance(_ok(client.get("/api/admin/integrations/log", headers=H(ADM))), list)
    assert "mape_by_item" in _ok(client.get("/api/reports/estimate-accuracy", headers=H(FD)))
    # просрочки: просроченная задача → уведомление исполнителю и КД, один раз
    from datetime import date, timedelta
    from app.db import SessionLocal
    from app.models import Deal, NotificationLog, Task, User
    from app.services.deal_service import check_overdue_tasks
    with SessionLocal() as db:
        lg = db.query(User).filter(User.email == LOG).one()
        t = Task(deal_id=db.query(Deal).first().id, role="logistician", assignee_id=lg.id, title="Тест просрочки", block="test", due=date.today() - timedelta(days=3))
        db.add(t)
        db.commit()
        assert check_overdue_tasks(db) >= 1
        db.commit()
        assert db.query(NotificationLog).filter(NotificationLog.event == "task_overdue", NotificationLog.user_id == lg.id).count() >= 1
        assert check_overdue_tasks(db) == 0          # повторно не уведомляет
        t.done_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        db.commit()
    assert "notified" in _ok(client.post("/api/tasks/check-overdue", headers=H(ADM)))
    with SessionLocal() as db:   # просрочку получил и ЗГД
        gd = db.query(User).filter(User.email == GD).one()
        assert db.query(NotificationLog).filter(NotificationLog.event == "task_overdue", NotificationLog.user_id == gd.id).count() >= 1
    # история просрочек по сотрудникам — только ЗГД
    assert client.get("/api/reports/overdue", headers=H(CD)).status_code == 403
    assert client.get("/api/reports/overdue", headers=H(ADM)).status_code == 403
    rep = _ok(client.get("/api/reports/overdue", headers=H(GD)))
    lg_row = next(s_ for s_ in rep["summary"] if s_["name"].startswith("Каталкина"))
    assert lg_row["overdue"] >= 1 and lg_row["days_max"] >= 3
    assert any(r["title"] == "Тест просрочки" and r["days_late"] == 3 and not r["open"] for r in rep["rows"])
    only = _ok(client.get(f"/api/reports/overdue?user_id={lg_row['user_id']}", headers=H(GD)))
    assert {r["user_id"] for r in only["rows"]} <= {lg_row["user_id"]}


def test_outcome_lost_and_reopen(client):
    """Проигранную сделку КД возвращает в работу; менеджер не может."""
    from app.db import SessionLocal
    from app.models import Deal
    from app.core.workflow import DealStatus
    with SessionLocal() as db:
        d = db.query(Deal).filter(Deal.status == DealStatus.APPROVED).first()
        if d is None:
            pytest.skip("нет утверждённой сделки")
        did = d.id
    assert _ok(client.post(f"/api/deals/{did}/outcome", headers=H(GD), json={"outcome": "lost", "reason": "цена выше конкурентов"}))["status"] == "lost"
    assert client.post(f"/api/deals/{did}/reopen", headers=H(MGR), json={"reason": "x"}).status_code == 403
    assert client.post(f"/api/deals/{did}/reopen", headers=H(CD), json={"reason": ""}).status_code == 422
    assert _ok(client.post(f"/api/deals/{did}/reopen", headers=H(CD), json={"reason": "клиент вернулся к переговорам"}))["status"] == "kp_sent"
    assert client.post(f"/api/deals/{did}/reopen", headers=H(CD), json={"reason": "ещё раз"}).status_code == 409


def test_factory_delete_and_contacts(client):
    """Удаление завода — только ЗГД (используемый — в архив); контакты правят менеджер, КД и ЗГД."""
    f = _ok(client.post("/api/refs/factories", headers=H(MGR), json={"name": "Тестовый завод для удаления", "contacts": []}), 201)
    c = [{"name": "Ли Вэй", "email": "li@example.cn", "roles": ["rfq"], "primary": True}]
    assert client.put(f"/api/refs/factories/{f['id']}/contacts", headers=H(LOG), json={"contacts": c}).status_code == 403
    assert _ok(client.put(f"/api/refs/factories/{f['id']}/contacts", headers=H(MGR), json={"contacts": c}))["contacts"][0]["email"] == "li@example.cn"
    card = _ok(client.get(f"/api/refs/factories/{f['id']}", headers=H(LOG)))
    _ok(client.put(f"/api/refs/factories/{f['id']}", headers=H(LOG), json={**{k: v for k, v in card.items() if v is not None}, "notes": "логист", "contacts": []}))
    assert _ok(client.get(f"/api/refs/factories/{f['id']}", headers=H(LOG)))["contacts"][0]["email"] == "li@example.cn"   # логист контакты не меняет
    assert client.delete(f"/api/refs/factories/{f['id']}", headers=H(CD)).status_code == 403
    r = _ok(client.delete(f"/api/refs/factories/{f['id']}", headers=H(GD)))
    assert r["deleted"] and not any(x["id"] == f["id"] for x in _ok(client.get("/api/refs/factories", headers=H(MGR))))
    used = _ok(client.get("/api/refs/factories", headers=H(MGR)))[0]
    r2 = _ok(client.delete(f"/api/refs/factories/{used['id']}", headers=H(GD)))
    assert r2["archived"] and not r2["deleted"]
    assert any(x["id"] == used["id"] for x in _ok(client.get("/api/refs/factories?include_archived=true", headers=H(MGR))))
    card_u = _ok(client.get(f"/api/refs/factories/{used['id']}", headers=H(GD)))
    _ok(client.put(f"/api/refs/factories/{used['id']}", headers=H(GD), json={**{k: v for k, v in card_u.items() if v is not None}, "is_active": True}))


def test_documents_lawyer_and_offer_from_tech_table(client):
    """Файлы клиента в сделке, задача и проверка юриста, закрытый доступ к юридическим файлам; ответ завода из таблицы теххарактеристик."""
    from app.db import SessionLocal
    from app.models import Deal, FactoryOffer, Position, User
    from app.services.offers_service import offer_from_position
    with SessionLocal() as db:
        mgr = db.query(User).filter(User.email == MGR).one()
        d = db.query(Deal).filter(Deal.manager_id == mgr.id).first()
        if d is None:
            pytest.skip("нет сделки менеджера")
        did = d.id
    tender = ("Тендерная документация.pdf", b"%PDF-1.4 test", "application/pdf")
    up = _ok(client.post(f"/api/deals/{did}/documents", headers=H(MGR), files={"file": tender}, data={"category": "tender", "comment": "от клиента"}), 201)
    assert any(x["category"] == "tender" for x in up["documents"])
    docs = _ok(client.get(f"/api/deals/{did}/documents", headers=H(LAW)))
    assert any(x["file_name"] == "Тендерная документация.pdf" for x in docs["documents"]) and docs["review"]["status"] == "in_review"
    assert any(t["deal_id"] == did and t["block"] == "legal" for t in _ok(client.get("/api/tasks/my", headers=H(LAW))))
    # протокол разногласий — юрист; менеджер юридические документы не загружает
    dis = ("Протокол разногласий.docx", b"PK test", "application/octet-stream")
    assert client.post(f"/api/deals/{did}/documents", headers=H(MGR), files={"file": dis}, data={"category": "disagreements"}).status_code == 403
    _ok(client.post(f"/api/deals/{did}/documents", headers=H(LAW), files={"file": dis}, data={"category": "disagreements"}), 201)
    assert client.post(f"/api/deals/{did}/legal-review", headers=H(MGR), json={"status": "ok"}).status_code == 403
    assert client.post(f"/api/deals/{did}/legal-review", headers=H(LAW), json={"status": "remarks", "comment": ""}).status_code == 422
    _ok(client.post(f"/api/deals/{did}/legal-review", headers=H(LAW), json={"status": "remarks", "comment": "п. 5.2 — неустойка"}))
    assert _ok(client.get(f"/api/deals/{did}", headers=H(MGR)))["legal_review"]["status"] == "remarks"
    assert not any(t["deal_id"] == did and t["block"] == "legal" and not t.get("done_at") for t in _ok(client.get("/api/tasks/my", headers=H(LAW))))
    # файлы юридической папки закрыты для ролей без доступа
    url = next(x["url"] for x in _ok(client.get(f"/api/deals/{did}/documents", headers=H(MGR)))["documents"] if x["category"] == "tender")
    assert client.get(url, headers=H(LOG)).status_code in (403, 404)
    assert client.get(f"/api/deals/{did}/documents", headers=H(LOG)).status_code in (403, 404)   # логисту сделка не видна или документы закрыты
    # ответ завода из таблицы теххарактеристик
    with SessionLocal() as db:
        pos = db.query(Position).filter(Position.factory_id.isnot(None)).first()
        if pos is None:
            return
        deal = db.get(Deal, pos.deal_id)
        if pos.tech_param_rows:
            pos.tech_param_rows[0].value_ru = pos.tech_param_rows[0].value_ru or "тест-значение"
            pos.tech_param_rows[0].local_ru = False
        pos.unit_price = pos.unit_price or 1000.0
        o = offer_from_position(db, deal, pos, None, "из таблицы технических характеристик")
        assert o is not None and o.factory_id == pos.factory_id
        assert db.query(FactoryOffer).filter(FactoryOffer.deal_id == deal.id, FactoryOffer.source_position_id == pos.id,
                                             FactoryOffer.factory_id == pos.factory_id).count() == 1
        db.rollback()


def test_gd_adds_employee_and_access(client):
    """ЗГД на вкладке «Сотрудники» добавляет сотрудника, назначает роли и индивидуальный доступ; менеджер — нет."""
    assert client.post("/api/admin/users", headers=H(MGR), json={"email": "x@example.com", "display_name": "X", "roles": ["manager"]}).status_code == 403
    assert client.post("/api/admin/users", headers=H(GD), json={"email": "new@example.com", "display_name": "Новый", "roles": []}).status_code == 422
    u = _ok(client.post("/api/admin/users", headers=H(GD), json={"email": "New.Manager@example.com", "display_name": "Новиков Пётр", "roles": ["manager"],
                                                                 "contacts": {"position": "менеджер по продажам"}}), 201)
    assert u["email"] == "new.manager@example.com"
    team = _ok(client.get("/api/team?include_inactive=true", headers=H(GD)))
    assert team["can_manage"] and any(r["code"] == "lawyer" for r in team["roles"])
    card = next(x for x in team["users"] if x["id"] == u["id"])
    assert card["contacts"]["position"] == "менеджер по продажам"
    assert not _ok(client.get("/api/team", headers=H(MGR)))["can_manage"]
    _ok(client.post("/api/admin/access/overrides", headers=H(GD), json={"user_id": u["id"], "group": "legal_docs", "level": 1, "comment": "тест"}), 201)
    perms = {p["group"]: p["level"] for p in _ok(client.get(f"/api/admin/access/preview?user_id={u['id']}", headers=H(GD)))["perms"]}
    assert perms["legal_docs"] == 1
    _ok(client.patch(f"/api/admin/users/{u['id']}", headers=H(GD), json={"is_active": False}))
    assert not any(x["id"] == u["id"] for x in _ok(client.get("/api/team", headers=H(GD)))["users"])
    assert any(x["id"] == u["id"] and not x["is_active"] for x in _ok(client.get("/api/team?include_inactive=true", headers=H(GD)))["users"])


def test_delete_draft_and_archive_others(client):
    """КД и ЗГД: черновик удаляется, сделка в другом статусе переносится в архив и возвращается; менеджер не может."""
    from app.db import SessionLocal
    from app.models import Deal
    from app.core.workflow import DealStatus
    with SessionLocal() as db:
        draft = db.query(Deal).filter(Deal.status == DealStatus.DRAFT, Deal.deleted_at.is_(None)).first()
        other = db.query(Deal).filter(Deal.status != DealStatus.DRAFT, Deal.status.notin_([DealStatus.CONTRACT, DealStatus.EXECUTION, DealStatus.CLOSED]),
                                      Deal.deleted_at.is_(None), Deal.archived_at.is_(None)).first()
        if draft is None or other is None:
            pytest.skip("нет подходящих сделок")
        did, oid = draft.id, other.id
    assert client.post(f"/api/deals/{did}/delete", headers=H(MGR), json={"reason": "x"}).status_code == 403
    assert client.post(f"/api/deals/{did}/delete", headers=H(CD), json={"reason": ""}).status_code == 422
    r = _ok(client.post(f"/api/deals/{did}/delete", headers=H(CD), json={"reason": "ошибочно заведена"}))
    assert r["deleted"] and not r["archived"]
    assert client.get(f"/api/deals/{did}", headers=H(GD)).status_code == 404
    assert not any(d["id"] == did for d in _ok(client.get("/api/deals", headers=H(GD))))
    r = _ok(client.post(f"/api/deals/{oid}/delete", headers=H(GD), json={"reason": "закупка перенесена"}))
    assert r["archived"] and not r["deleted"]
    assert not any(d["id"] == oid for d in _ok(client.get("/api/deals", headers=H(GD))))
    assert any(d["id"] == oid for d in _ok(client.get("/api/deals?archived=true", headers=H(GD))))
    assert _ok(client.get(f"/api/deals/{oid}", headers=H(GD)))["archived_at"]
    assert client.post(f"/api/deals/{oid}/unarchive", headers=H(MGR)).status_code == 403
    _ok(client.post(f"/api/deals/{oid}/unarchive", headers=H(CD)))
    assert any(d["id"] == oid for d in _ok(client.get("/api/deals", headers=H(GD))))


def test_cd_creates_deal(client):
    """КД заводит сделку на себя (и ведёт её) или назначает ответственного менеджера; менеджер назначать не может."""
    cl = _ok(client.get("/api/refs/clients", headers=H(CD)))[0]
    mgr_id = _ok(client.get("/api/auth/me", headers=H(MGR)))["id"]
    own = _ok(client.post("/api/deals", headers=H(CD), json={"client_id": cl["id"]}), 201)
    assert own["manager_id"] == _ok(client.get("/api/auth/me", headers=H(CD)))["id"]
    _ok(client.patch(f"/api/deals/{own['id']}", headers=H(CD), json={"contact": "Иванов"}))   # КД ведёт свою сделку
    d = _ok(client.post("/api/deals", headers=H(CD), json={"client_id": cl["id"], "manager_id": mgr_id}), 201)
    assert d["manager_id"] == mgr_id and any(x["id"] == d["id"] for x in _ok(client.get("/api/deals", headers=H(MGR))))
    assert client.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "manager_id": own["manager_id"]}).status_code == 403
    assert client.post("/api/deals", headers=H(LOG), json={"client_id": cl["id"]}).status_code == 403
