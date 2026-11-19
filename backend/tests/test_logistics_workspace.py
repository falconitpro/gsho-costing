"""Рабочее место логиста (D-073): адреса, поля от ЗГД, запрос логистическим компаниям (Excel/PDF/почта), ответы, задачи в списке."""
import io
import uuid

from openpyxl import load_workbook

from tests.test_api_flow import client, _ok, H, MGR, LOG, CD, GD  # noqa: F401


def _deal(client):
    clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
    factories = _ok(client.get("/api/refs/factories", headers=H(MGR)))
    did = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "Рудник Северный"}), 201)["id"]
    pos = _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"factory_id": factories[0]["id"], "model": "WS-1",
              "machine_type": "ПДМ", "qty": 2, "unit_price": 1000000, "currency": "CNY"}), 201)
    _ok(client.post(f"/api/deals/{did}/send-to-calc", headers=H(MGR)))
    return did, pos, clients[0]["id"]


def test_workspace_shape_and_roles(client):
    did, pos, _ = _deal(client)
    ws = _ok(client.get(f"/api/deals/{did}/logistics/workspace", headers=H(LOG)))
    assert ws["can_edit"] and not ws["can_choose_fields"]
    assert ws["fields"][0]["position_id"] == pos["id"] and ws["fields"][0]["rows"]
    assert ws["addresses"]["pickup"] and ws["addresses"]["delivery_address"]          # подставлено из сделки/карточек
    assert ws["mail_enabled"] is False
    assert _ok(client.get(f"/api/deals/{did}/logistics/workspace", headers=H(GD)))["can_choose_fields"]
    assert not _ok(client.get(f"/api/deals/{did}/logistics/workspace", headers=H(MGR)))["can_edit"]


def test_fields_chosen_by_gd_only(client):
    did, _, _ = _deal(client)
    opts = _ok(client.get(f"/api/refs/logistics-fields?deal_id={did}", headers=H(LOG)))
    assert opts["options"]
    pick = [{"code": o["code"], "name": o["name"]} for o in opts["options"][:2]]
    assert client.put("/api/refs/logistics-fields", headers=H(LOG), json=pick).status_code == 403
    _ok(client.put("/api/refs/logistics-fields", headers=H(GD), json=pick))
    ws = _ok(client.get(f"/api/deals/{did}/logistics/workspace", headers=H(LOG)))
    assert [r["name"] for r in ws["fields"][0]["rows"]] == [p["name"] for p in pick]
    # настройка внутренняя — на общем экране настроек её нет и через него не меняется
    assert client.put("/api/admin/settings/logistics_tech_fields", headers=H(GD), json={"value": []}).status_code in (403, 404)


def test_delivery_address_proposed_and_approved_by_gd(client):
    did, _, cid = _deal(client)
    tag = uuid.uuid4().hex[:6]
    ws = _ok(client.get(f"/api/deals/{did}/logistics/workspace", headers=H(LOG)))
    fid = ws["addresses"]["pickup"][0]["factory_id"]
    r = _ok(client.put(f"/api/deals/{did}/logistics/addresses", headers=H(LOG),
                       json={"pickup": {fid: "Сюйчжоу, пр. Тестовый 1"}, "delivery_address": f"Норильск, ул. Новая {tag}", "propose_to_client": True}))
    assert r["proposed"]
    ws = _ok(client.get(f"/api/deals/{did}/logistics/workspace", headers=H(LOG)))
    assert ws["addresses"]["delivery_address"] == f"Норильск, ул. Новая {tag}"
    assert ws["addresses"]["pickup"][0]["address"] == "Сюйчжоу, пр. Тестовый 1" and ws["addresses"]["pickup"][0]["edited"]
    card = _ok(client.get(f"/api/refs/clients/{cid}", headers=H(GD)))
    a = next(x for x in card["delivery_addresses"] if x["address"] == f"Норильск, ул. Новая {tag}")
    assert a["status"] == "pending"
    assert client.post(f"/api/refs/clients/{cid}/delivery-addresses/{a['id']}/approve", headers=H(LOG)).status_code == 403
    out = _ok(client.post(f"/api/refs/clients/{cid}/delivery-addresses/{a['id']}/approve", headers=H(GD)))
    assert next(x for x in out["delivery_addresses"] if x["id"] == a["id"])["status"] == "approved"
    # повторное предложение того же адреса не плодит дубль
    _ok(client.put(f"/api/deals/{did}/logistics/addresses", headers=H(LOG), json={"delivery_address": f"норильск, ул. новая {tag} ", "propose_to_client": True}))
    card = _ok(client.get(f"/api/refs/clients/{cid}", headers=H(GD)))
    assert sum(1 for x in card["delivery_addresses"] if x["address"].strip().lower() == f"норильск, ул. новая {tag}") == 1
    # менеджер адреса не меняет
    assert client.put(f"/api/deals/{did}/logistics/addresses", headers=H(MGR), json={"delivery_address": "x"}).status_code == 403


def test_request_files_and_sending(client):
    did, _, _ = _deal(client)
    r = client.get(f"/api/deals/{did}/logistics/request/file?fmt=xlsx&answer_days=4", headers=H(LOG))
    assert r.status_code == 200 and "spreadsheet" in r.headers["content-type"]
    wb = load_workbook(io.BytesIO(r.content))
    text = " ".join(str(c.value) for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.value)
    assert "Запрос" in text and "WS-1" in text and "Стоимость" in text
    r = client.get(f"/api/deals/{did}/logistics/request/file?fmt=pdf", headers=H(LOG))
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    # почта не подключена: отправка из системы — 409, отметка «вручную» — записывается
    body = {"emails": ["calc@carrier.example"], "answer_days": 3}
    assert client.post(f"/api/deals/{did}/logistics/request/send", headers=H(LOG), json=body).status_code == 409
    _ok(client.post(f"/api/deals/{did}/logistics/request/send", headers=H(LOG), json={**body, "manual": True}))
    ws = _ok(client.get(f"/api/deals/{did}/logistics/workspace", headers=H(LOG)))
    assert len(ws["requests"]) == 1 and ws["requests"][0]["sent_to"] == ["calc@carrier.example"] and ws["requests"][0]["no"].endswith("-Л1")
    assert client.post(f"/api/deals/{did}/logistics/request/send", headers=H(LOG), json={"emails": ["не-почта"], "manual": True}).status_code == 422


def test_carriers_directory(client):
    _ok(client.put("/api/refs/logistics-carriers", headers=H(LOG), json=[{"name": "Транс-Восток", "email": "calc@tv.example"}]))
    assert _ok(client.get("/api/refs/logistics-carriers", headers=H(MGR)))[0]["name"] == "Транс-Восток"
    assert client.put("/api/refs/logistics-carriers", headers=H(LOG), json=[{"name": "X", "email": "bad"}]).status_code == 422
    assert client.put("/api/refs/logistics-carriers", headers=H(MGR), json=[]).status_code == 403


def test_quotes_upload_and_delete(client):
    did, _, _ = _deal(client)
    files = {"file": ("ответ.pdf", b"%PDF-1.4 test", "application/pdf")}
    q = _ok(client.post(f"/api/deals/{did}/logistics/quotes", headers=H(LOG), files=files,
                        data={"carrier": "Транс-Восток", "amount": "350000", "currency": "CNY", "transit_days": "21", "scope": "door_to_door"}), 201)
    assert q["amount"] == 350000 and q["storage_path"]
    ws = _ok(client.get(f"/api/deals/{did}/logistics/workspace", headers=H(CD)))
    assert ws["quotes"][0]["carrier"] == "Транс-Восток"
    assert client.delete(f"/api/deals/{did}/logistics/quotes/{q['id']}", headers=H(MGR)).status_code == 403
    assert client.delete(f"/api/deals/{did}/logistics/quotes/{q['id']}", headers=H(LOG)).status_code == 204
    assert _ok(client.get(f"/api/deals/{did}/logistics/workspace", headers=H(LOG)))["quotes"] == []


def test_my_tasks_in_deals_list(client):
    did, _, _ = _deal(client)
    rows = _ok(client.get("/api/deals", headers=H(LOG)))
    rows = rows["items"] if isinstance(rows, dict) else rows
    row = next(r for r in rows if r["id"] == did)
    assert "logistics_info" not in row
    assert row["my_tasks"] and all({"title", "due", "overdue"} <= set(t) for t in row["my_tasks"])


def test_services_selection_in_request(client):
    did, _, _ = _deal(client)
    ws = _ok(client.get(f"/api/deals/{did}/logistics/workspace", headers=H(LOG)))
    assert "customs" not in ws["services"] and "export" not in ws["services"] and "intl_freight" in ws["services"]
    _ok(client.put(f"/api/deals/{did}/logistics/services", headers=H(LOG), json={"services": ["intl_freight", "rf_delivery"]}))
    r = client.get(f"/api/deals/{did}/logistics/request/file?fmt=xlsx", headers=H(LOG))
    wb = load_workbook(io.BytesIO(r.content))
    text = " ".join(str(c.value) for ws_ in wb.worksheets for row in ws_.iter_rows() for c in row if c.value)
    assert "Международная перевозка" in text and "Таможенное оформление" not in text and "Страхование" not in text
    assert client.put(f"/api/deals/{did}/logistics/services", headers=H(LOG), json={"services": []}).status_code == 422
    assert client.put(f"/api/deals/{did}/logistics/services", headers=H(MGR), json={"services": ["rf_delivery"]}).status_code == 403


def test_compare_several_carriers_and_choose(client):
    did, _, _ = _deal(client)
    _ok(client.put(f"/api/deals/{did}/logistics/services", headers=H(LOG), json={"services": ["door_to_door", "intl_freight", "rf_delivery"]}))
    add = lambda c, scope, amount, cur="RUB", days="": _ok(client.post(f"/api/deals/{did}/logistics/quotes", headers=H(LOG),  # noqa: E731
                                                                    data={"carrier": c, "scope": scope, "amount": str(amount), "currency": cur, "transit_days": str(days) if days else ""}), 201)
    add("Альфа", "door_to_door", 900000, days=30)
    add("Бета", "intl_freight", 400000, days=20)
    add("Бета", "rf_delivery", 300000, days=7)
    add("Гамма", "intl_freight", 100000)                       # неполное: нет доставки по РФ
    cmp = _ok(client.get(f"/api/deals/{did}/logistics/workspace", headers=H(LOG)))["compare"]
    rows = {r["carrier"]: r for r in cmp["rows"]}
    assert rows["Бета"]["total_rub"] == 700000 and rows["Бета"]["complete"] and rows["Бета"]["transit_days"] == 27
    assert not rows["Гамма"]["complete"] and rows["Гамма"]["missing"]
    assert cmp["best"] == "Бета"
    assert client.post(f"/api/deals/{did}/logistics/quotes/choose", headers=H(LOG), json={"carrier": "Альфа"}).status_code == 422   # не лучшее — нужна причина
    out = _ok(client.post(f"/api/deals/{did}/logistics/quotes/choose", headers=H(LOG), json={"carrier": "Альфа", "comment": "надёжнее, один подрядчик"}))
    assert out["chosen"] == "Альфа"
    assert client.post(f"/api/deals/{did}/logistics/quotes/choose", headers=H(LOG), json={"carrier": "Неизвестная"}).status_code == 422


def test_choice_needs_gd_approval(client):
    did, _, _ = _deal(client)
    _ok(client.post(f"/api/deals/{did}/logistics/quotes", headers=H(LOG), data={"carrier": "Дельта", "scope": "door_to_door", "amount": "500000", "currency": "RUB"}), 201)
    assert client.post(f"/api/deals/{did}/logistics/quotes/choose", headers=H(LOG), json={"carrier": "Дельта", "need_gd": True}).status_code == 422
    out = _ok(client.post(f"/api/deals/{did}/logistics/quotes/choose", headers=H(LOG), json={"carrier": "Дельта", "need_gd": True, "comment": "предоплата 100 %"}))
    assert out["chosen_status"] == "pending_gd"
    assert client.post(f"/api/deals/{did}/logistics/quotes/choose/decision", headers=H(LOG), json={"approve": True}).status_code == 403
    assert client.post(f"/api/deals/{did}/logistics/quotes/choose/decision", headers=H(GD), json={"approve": False}).status_code == 422
    out = _ok(client.post(f"/api/deals/{did}/logistics/quotes/choose/decision", headers=H(GD), json={"approve": True, "comment": "ок"}))
    assert out["chosen_status"] == "approved" and out["gd_comment"] == "ок"
    assert client.post(f"/api/deals/{did}/logistics/quotes/choose/decision", headers=H(GD), json={"approve": True}).status_code == 409


def test_calc_survives_text_in_numeric_tech_field():
    """Регресс 19.11.2026: «указать» в поле массы ронял расчёт (500 на «Расчёт и утверждение»)."""
    from app.services.deal_calc import _num
    assert _num("указать") == 0.0 and _num("указать", 5.0) == 5.0
    assert _num("12 500 кг") == 12500.0 and _num("12,5") == 12.5 and _num(None, 1.0) == 1.0 and _num(7) == 7.0 and _num("") == 0.0
