"""Торги (D-074): шаги снижения цены и условий, пересчёт, редакция КП на шаге, отправка, конкуренты, аналитика, доступ КД / ЗГД."""
from tests.test_api_flow import client, _ok, H, MGR, LOG, CD, FD, GD  # noqa: F401


def _approved(client, margin=20.0, currency=None):
    clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
    factories = _ok(client.get("/api/refs/factories", headers=H(MGR)))
    did = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "Торги"}), 201)["id"]
    pos = _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"factory_id": factories[0]["id"], "model": "NG-7",
              "machine_type": "ПДМ", "qty": 1, "unit_price": 1500000, "currency": "CNY"}), 201)
    var = _ok(client.get(f"/api/deals/{did}", headers=H(MGR)))["offer"]
    _ok(client.post(f"/api/deals/{did}/send-to-calc", headers=H(MGR)))
    items = {"door_to_door": {"amount": 200000, "currency": "CNY"}, "broker_rub": 200000, "comments": {"door_to_door": "x", "broker_rub": "x"}}
    _ok(client.post(f"/api/deals/{did}/logistics/batches", headers=H(LOG), json={"items": items, "position_ids": [pos["id"]]}), 201)
    _ok(client.put(f"/api/deals/{did}/logistics/positions/{pos['id']}", headers=H(LOG), json={"hs_code": "8429519000", "util_applies": False}))
    _ok(client.post(f"/api/deals/{did}/logistics/complete", headers=H(LOG)))
    _ok(client.post(f"/api/deals/{did}/price", headers=H(CD), json={"variant_id": var["id"], "mode": "margin", "value": margin, **({"currency": currency} if currency else {})}))
    schemes = _ok(client.get("/api/refs/payment-schemes", headers=H(CD)))
    calc = _ok(client.get(f"/api/deals/{did}/calc", headers=H(CD)))
    _ok(client.post(f"/api/deals/{did}/payment-terms", headers=H(CD), json={"client_payments": schemes[0]["lines"],
                                                                            "factory_payments": {pos["id"]: calc["payment_terms"]["default_factory"]}}))
    assert _ok(client.post(f"/api/deals/{did}/submit", headers=H(CD)))["status"] == "approved"
    return did, var["id"], schemes


def test_access_only_cd_gd(client):
    did, _, _ = _approved(client)
    for who in (MGR, LOG, FD):
        assert client.get(f"/api/deals/{did}/negotiation", headers=H(who)).status_code == 403
    ov = _ok(client.get(f"/api/deals/{did}/negotiation", headers=H(CD)))
    assert ov["ready"] and ov["can_edit"] and ov["steps"] == []
    d = ov["decision"][ov["variants"][0]["id"]]
    assert d["min_price_threshold"]["net_rub"] < d["price"]["net_rub"] and d["room_pct"] > 0
    assert _ok(client.get(f"/api/deals/{did}/negotiation", headers=H(GD)))["ready"]


def test_price_steps_kp_and_analytics(client):
    did, vid, _ = _approved(client)
    first = _ok(client.get(f"/api/deals/{did}/negotiation", headers=H(CD)))["variants"][0]["first_rub"]
    # шаг 1: −3 % — предпросмотр, затем фиксация с КП
    pv = _ok(client.post(f"/api/deals/{did}/negotiation/preview", headers=H(CD), json={"prices": {vid: {"mode": "pct", "value": 3}}}))
    assert abs(pv["variants"][vid]["price"]["net_rub"] - first * 0.97) < 1
    s1 = _ok(client.post(f"/api/deals/{did}/negotiation/steps", headers=H(CD),
                         json={"prices": {vid: {"mode": "pct", "value": 3}}, "reason": "client", "comment": "просьба клиента"}), 201)
    assert s1["no"] == 1 and s1["kp_revision_no"] == 1 and s1["margin_pct"] < 20
    # шаг 2: −100 000 RUB от цены с НДС
    s2 = _ok(client.post(f"/api/deals/{did}/negotiation/steps", headers=H(GD),
                         json={"prices": {vid: {"mode": "amount", "basis": "gross", "currency": "RUB", "value": 122000}}, "reason": "competitor"}), 201)
    assert abs((s2["price_before_rub"] - s2["price_rub"]) - 100000) < 1
    # КП шага — редакция с итогом шага
    revs = _ok(client.get(f"/api/deals/{did}/kp/revisions", headers=H(CD)))["rows"]
    assert len(revs) == 3 and abs(revs[-1]["price_rub"] - s2["price_rub"]) < 1
    r = client.get(f"/api/deals/{did}/kp/file?format=docx", headers=H(CD))
    assert r.status_code == 200 and r.content[:2] == b"PK"
    ov = _ok(client.get(f"/api/deals/{did}/negotiation", headers=H(CD)))
    a = ov["analytics"]
    assert a["steps"] == 2 and a["price_steps"] == 2 and abs(a["first_rub"] - first) < 1
    assert abs(a["bargain_coef"] - (first - s2["price_rub"]) / first) < 0.0001 and a["margin_lost_pp"] > 0
    assert ov["variants"][0]["price_rub"] == s2["price_rub"]


def test_below_threshold_needs_confirmation_and_comment(client):
    did, vid, _ = _approved(client, margin=6.0)
    body = {"prices": {vid: {"mode": "pct", "value": 10}}, "reason": "tender"}
    r = client.post(f"/api/deals/{did}/negotiation/steps", headers=H(CD), json=body)
    assert r.status_code == 422 and r.json()["detail"]["confirm"] == "below_threshold"
    assert client.post(f"/api/deals/{did}/negotiation/steps", headers=H(CD), json={**body, "confirm_below_threshold": True}).status_code == 422
    _ok(client.post(f"/api/deals/{did}/negotiation/steps", headers=H(CD), json={**body, "confirm_below_threshold": True, "comment": "вход на объект"}), 201)
    assert client.post(f"/api/deals/{did}/negotiation/steps", headers=H(CD), json={"reason": "client"}).status_code == 422


def test_terms_step_changes_payments_in_kp(client):
    did, vid, schemes = _approved(client)
    alt = schemes[-1]["lines"]
    s = _ok(client.post(f"/api/deals/{did}/negotiation/steps", headers=H(CD),
                        json={"terms": {vid: {"client_payments": alt, "delivery_term": "120 дней с предоплаты"}}, "reason": "terms"}), 201)
    assert s["kind"] == "terms" and abs(s["price_rub"] - s["price_before_rub"]) < 1
    ov = _ok(client.get(f"/api/deals/{did}/negotiation", headers=H(CD)))
    assert ov["variants"][0]["delivery_term"] == "120 дней с предоплаты" and ov["variants"][0]["client_payments"] == alt
    bad = [{"no": 1, "share_pct": 50, "event": "contract"}]
    assert client.post(f"/api/deals/{did}/negotiation/preview", headers=H(CD), json={"terms": {vid: {"client_payments": bad}}}).status_code == 422


def test_send_step_kp_marks_sent(client):
    did, vid, _ = _approved(client)
    s = _ok(client.post(f"/api/deals/{did}/negotiation/steps", headers=H(CD), json={"prices": {vid: {"mode": "pct", "value": 1}}, "reason": "client"}), 201)
    body = {"to": ["buyer@client.example"]}
    assert client.post(f"/api/deals/{did}/negotiation/steps/{s['id']}/send", headers=H(CD), json=body).status_code == 409   # почта не подключена
    out = _ok(client.post(f"/api/deals/{did}/negotiation/steps/{s['id']}/send", headers=H(CD), json={**body, "manual": True}))
    assert out["sent_at"] and out["sent_to"] == ["buyer@client.example"]
    assert _ok(client.get(f"/api/deals/{did}", headers=H(CD)))["status"] == "kp_sent"
    _ok(client.put(f"/api/deals/{did}/negotiation/steps/{s['id']}/answer", headers=H(CD), json={"client_answer": "просит ещё 2 %"}))
    assert _ok(client.get(f"/api/deals/{did}/negotiation", headers=H(CD)))["steps"][0]["client_answer"] == "просит ещё 2 %"


def test_competitor_terms_in_negotiation(client):
    did, vid, _ = _approved(client)
    _ok(client.post(f"/api/deals/{did}/competitors", headers=H(CD), json={"competitor": "Конкурент-А", "amount": 30000000, "currency": "RUB",
                                                                         "equipment": "XY-7", "delivery_term": "90 дней", "payment_terms": "30/70",
                                                                         "variant_id": vid}), 201)
    ov = _ok(client.get(f"/api/deals/{did}/negotiation", headers=H(CD)))
    c = ov["competitors"][0]
    assert c["competitor"] == "Конкурент-А" and c["delivery_term"] == "90 дней" and c["payment_terms"] == "30/70"
    assert ov["analytics"]["best_competitor_rub"] == 30000000


def test_kp_edit_after_step_keeps_negotiated_price(client):
    did, vid, _ = _approved(client)
    s = _ok(client.post(f"/api/deals/{did}/negotiation/steps", headers=H(CD), json={"prices": {vid: {"mode": "pct", "value": 5}}, "reason": "client"}), 201)
    r = _ok(client.post(f"/api/deals/{did}/kp/revisions", headers=H(CD), json={"changes": {vid: {"extras": [{"name": "Обучение", "price_rub": 100000}]}},
                                                                              "description": "добавлено обучение"}), 201)
    assert abs(r["price_rub"] - (s["price_rub"] + 100000)) < 1
    # шаг со скидкой строкой в КП: итог — цена шага
    s2 = _ok(client.post(f"/api/deals/{did}/negotiation/steps", headers=H(CD),
                         json={"prices": {vid: {"mode": "pct", "value": 1}}, "reason": "client", "show_discount": True}), 201)
    assert abs(s2["price_rub"] - r["price_rub"] * 0.99) < 1


def test_client_review_status_and_reminder(client):
    from datetime import date, timedelta
    did, vid, _ = _approved(client)
    s = _ok(client.post(f"/api/deals/{did}/negotiation/steps", headers=H(CD), json={"prices": {vid: {"mode": "pct", "value": 1}}, "reason": "client"}), 201)
    on = (date.today() + timedelta(days=3)).isoformat()
    assert client.put(f"/api/deals/{did}/negotiation/steps/{s['id']}/review", headers=H(MGR), json={"remind_on": on}).status_code == 403
    assert client.put(f"/api/deals/{did}/negotiation/steps/{s['id']}/review", headers=H(CD), json={"remind_on": "2020-01-01"}).status_code == 422
    r = _ok(client.put(f"/api/deals/{did}/negotiation/steps/{s['id']}/review", headers=H(CD), json={"remind_on": on, "assignee": "manager", "note": "ждём ответа закупки"}))
    assert r["review"]["status"] == "client_review" and r["review"]["remind_on"] == on
    tasks = _ok(client.get("/api/tasks/my", headers=H(MGR)))
    t = next(x for x in tasks if x["deal_id"] == did and x["block"] == "negotiation_review")
    assert t["due"] == on and "Уточнить у клиента" in t["title"]
    # следующий шаг закрывает «на рассмотрении» и напоминание
    _ok(client.post(f"/api/deals/{did}/negotiation/steps", headers=H(CD), json={"prices": {vid: {"mode": "pct", "value": 1}}, "reason": "client"}), 201)
    st = _ok(client.get(f"/api/deals/{did}/negotiation", headers=H(CD)))["steps"]
    assert st[0]["review"]["status"] == "answered"
    assert not any(x["deal_id"] == did and x["block"] == "negotiation_review" for x in _ok(client.get("/api/tasks/my", headers=H(MGR))))
    # ручное закрытие с ответом клиента
    _ok(client.put(f"/api/deals/{did}/negotiation/steps/{st[1]['id']}/review", headers=H(CD), json={"remind_on": on, "assignee": "me"}))
    out = _ok(client.post(f"/api/deals/{did}/negotiation/steps/{st[1]['id']}/review/close", headers=H(CD), json={"client_answer": "согласны"}))
    assert out["review"]["status"] == "answered" and out["client_answer"] == "согласны"
    assert client.post(f"/api/deals/{did}/negotiation/steps/{st[1]['id']}/review/close", headers=H(CD), json={}).status_code == 409


def test_client_rates_only_for_negotiation(client):
    did, vid, _ = _approved(client, currency="CNY")
    ov = _ok(client.get(f"/api/deals/{did}/negotiation", headers=H(CD)))
    v = ov["variants"][0]
    assert v["currency"] == "CNY" and ov["client_rates"] == {}
    calc_before = _ok(client.get(f"/api/deals/{did}/calc", headers=H(CD)))["input"]["fx"]["rates"]
    assert client.put(f"/api/deals/{did}/negotiation/client-rates", headers=H(MGR), json={"rates": {"CNY": 11}}).status_code == 403
    assert client.put(f"/api/deals/{did}/negotiation/client-rates", headers=H(CD), json={"rates": {"CNY": -1}}).status_code == 422
    _ok(client.put(f"/api/deals/{did}/negotiation/client-rates", headers=H(CD), json={"rates": {"CNY": 11.0}, "comment": "курс заказчика"}))
    ov = _ok(client.get(f"/api/deals/{did}/negotiation", headers=H(CD)))
    assert ov["client_rates"] == {"CNY": 11.0} and ov["client_rates_history"][-1]["comment"] == "курс заказчика"
    # снижение на 110 000 ₽ без НДС по курсу заказчика = 10 000 CNY → в расчёте — 10 000 × курс цены КП
    pv = _ok(client.post(f"/api/deals/{did}/negotiation/preview", headers=H(CD),
                         json={"prices": {vid: {"mode": "amount", "basis": "net", "currency": "RUB", "value": 110000}}}))["variants"][vid]
    assert abs((pv["before"]["net"] - pv["price"]["net"]) - 10000) < 0.5
    assert abs(-pv["change_rub"] - 10000 * v["kp_rate"]) < 1
    # расчёт сделки — по курсам ЦБ, курсы заказчика его не меняют
    assert _ok(client.get(f"/api/deals/{did}/calc", headers=H(CD)))["input"]["fx"]["rates"] == calc_before
    _ok(client.put(f"/api/deals/{did}/negotiation/client-rates", headers=H(CD), json={"rates": {}}))
    assert _ok(client.get(f"/api/deals/{did}/negotiation", headers=H(CD)))["client_rates"] == {}


def test_price_per_unit_for_qty_more_than_one(client):
    """D-085: при количестве больше 1 цена фиксируется за 1 шт., итог — × количество; в торгах снижение на сумму — тоже за 1 шт."""
    clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
    factories = _ok(client.get("/api/refs/factories", headers=H(MGR)))
    did = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "Штуки"}), 201)["id"]
    _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"factory_id": factories[0]["id"], "model": "Q-3",
        "machine_type": "ПДМ", "qty": 3, "unit_price": 1000000, "currency": "RUB"}), 201)
    var = _ok(client.get(f"/api/deals/{did}", headers=H(MGR)))["offer"]
    _ok(client.put(f"/api/deals/{did}/deal-currency", headers=H(CD), json={"currency": "RUB"}))
    r = _ok(client.post(f"/api/deals/{did}/price", headers=H(CD), json={"variant_id": var["id"], "mode": "amount", "value": 2_000_000, "currency": "RUB"}))
    assert r["variant"]["price_rub"] == 6_000_000 and r["variant"]["price_input_units"] == 3 and r["variant"]["price_input_amount"] == 2_000_000
    r = _ok(client.post(f"/api/deals/{did}/price", headers=H(CD), json={"variant_id": var["id"], "mode": "amount", "value": 6_000_000, "currency": "RUB", "per_unit": False}))
    assert r["variant"]["price_rub"] == 6_000_000


def test_negotiation_amount_per_unit(client):
    from app.services.negotiation import new_price
    st = {"price_rub": 3_000_000, "currency": "RUB", "kp_rate": 1.0, "units": 3}
    assert new_price(st, {"mode": "amount", "basis": "net", "currency": "RUB", "value": 100_000}, 22, {}) == 2_700_000
    assert new_price(st, {"mode": "target", "basis": "net", "currency": "RUB", "value": 900_000}, 22, {}) == 2_700_000
    assert new_price(st, {"mode": "pct", "value": 10}, 22, {}) == 2_700_000


def _priced(client):
    """Сделка с назначенной ценой, до утверждения (расчёт готов)."""
    clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
    factories = _ok(client.get("/api/refs/factories", headers=H(MGR)))
    did = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "Завод"}), 201)["id"]
    pos = _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"factory_id": factories[0]["id"], "model": "FP-1",
              "machine_type": "ПДМ", "qty": 2, "unit_price": 1000000, "currency": "CNY"}), 201)
    var = _ok(client.get(f"/api/deals/{did}", headers=H(MGR)))["offer"]
    _ok(client.post(f"/api/deals/{did}/send-to-calc", headers=H(MGR)))
    _ok(client.post(f"/api/deals/{did}/logistics/batches", headers=H(LOG), json={"items": {"door_to_door": {"amount": 100000, "currency": "CNY"}, "broker_rub": 100000,
        "comments": {"door_to_door": "x", "broker_rub": "x"}}, "position_ids": [pos["id"]]}), 201)
    _ok(client.put(f"/api/deals/{did}/logistics/positions/{pos['id']}", headers=H(LOG), json={"hs_code": "8429519000", "util_applies": False}))
    _ok(client.post(f"/api/deals/{did}/logistics/complete", headers=H(LOG)))
    _ok(client.post(f"/api/deals/{did}/price", headers=H(CD), json={"variant_id": var["id"], "mode": "margin", "value": 15}))
    return did, var["id"], pos


def test_factory_price_what_if_solve_request_apply(client):
    did, vid, pos = _priced(client)
    assert client.post(f"/api/deals/{did}/factory-price/what-if", headers=H(MGR), json={"variant_id": vid, "changes": {}}).status_code == 403
    w = _ok(client.post(f"/api/deals/{did}/factory-price/what-if", headers=H(CD), json={"variant_id": vid, "changes": {pos["id"]: {"mode": "discount_pct", "value": 5}}}))
    assert w["new_prices"][pos["id"]] == 950000
    assert w["after"]["margin_pct"] > w["before"]["margin_pct"] and w["after"]["cost_total_rub"] < w["before"]["cost_total_rub"]
    assert w["after"]["price_rub"] == w["before"]["price_rub"]                       # цена продажи не меняется
    s = _ok(client.post(f"/api/deals/{did}/factory-price/solve", headers=H(CD), json={"variant_id": vid, "position_id": pos["id"], "target_margin_pct": 20}))
    assert s["unit_price"] < 1000000 and abs(s["after"]["margin_pct"] - 20) < 0.05 and s["discount_pct"] > 0
    r = _ok(client.post(f"/api/deals/{did}/factory-price/request", headers=H(CD), json={"position_id": pos["id"], "mode": "price", "value": s["unit_price"], "comment": "к пятнице"}), 201)
    tasks = _ok(client.get("/api/tasks/my", headers=H(MGR)))
    assert any(t["id"] == r["task_id"] and "Запросить у производителя" in t["title"] for t in tasks)
    assert client.post(f"/api/deals/{did}/factory-price/apply", headers=H(CD), json={"position_id": pos["id"], "unit_price": 900000}).status_code == 422
    out = _ok(client.post(f"/api/deals/{did}/factory-price/apply", headers=H(CD), json={"position_id": pos["id"], "unit_price": 900000, "comment": "письмо завода 19.11"}))
    assert out["unit_price"] == 900000 and out["before"] == 1000000
    assert next(p for p in _ok(client.get(f"/api/deals/{did}", headers=H(CD)))["positions"] if p["id"] == pos["id"])["unit_price"] == 900000
