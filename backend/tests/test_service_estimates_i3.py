"""D-198 (I-3): вид оплаты, смета с версиями и согласованием, документы-снимки, права (инженер цен не видит)."""
import io
import uuid

from tests.test_api_flow import client, _ok, H, SVC, FD  # noqa: F401
from tests.test_service_core import ENG, _assign_with_permits, _ev, _order, _refs  # noqa: F401


def _cd_part(c, price):
    t = uuid.uuid4().hex[:6].upper()
    return _ok(c.post("/api/parts/catalog", headers=H("cd@example.com"), json={
        "article": f"SE-{t}", "name_ru": "Ремкомплект", "purchase_price": 1, "purchase_currency": "RUB", "sale_price": price, "sale_currency": "RUB"}), 201)


def test_estimate_versions_send_decide_and_docs(client):  # noqa: F811
    _, wo = _order(client, "DIAG-HYD")
    wo = _assign_with_permits(client, wo)
    wo = _ok(_ev(client, wo, "start"))["order"]
    refs = _ok(client.get("/api/service/refs", headers=H(SVC)))
    wt = next(x for x in refs["work_types"] if x["code"] == "RPR-HYD")
    wo = _ok(_ev(client, wo, "work_add", payload={"work_type_id": wt["id"]}))["order"]
    wo = _ok(_ev(client, wo, "work_update", payload={"work_id": wo["works"][0]["id"], "actual_hours": 6}))["order"]
    wo = _ok(_ev(client, wo, "diagnosis", payload={"text": "Износ уплотнений"}))["order"]
    # инженер смету не видит
    assert client.get(f"/api/service/orders/{wo['id']}/estimates", headers=H(ENG)).status_code == 403
    est = _ok(client.post(f"/api/service/orders/{wo['id']}/estimates", headers=H(SVC), json={}), 201)
    assert est["version"] == 1 and est["status"] == "draft" and est["vat_pct"] == 22
    assert est["lines"][0]["kind"] == "work" and est["lines"][0]["qty"] == 6 and est["lines"][0]["price"] is None   # ставки ещё нет
    assert client.post(f"/api/service/orders/{wo['id']}/estimates", headers=H(SVC), json={}).status_code == 422     # черновик уже есть
    part = _cd_part(client, 15000)
    lines = [{"kind": "work", "work_id": est["lines"][0]["work_id"], "title": est["lines"][0]["title"], "qty": 6, "unit": "н·ч", "price": 3500},
             {"kind": "part", "part_id": part["id"], "qty": 2, "price": None}]
    e = _ok(client.put(f"/api/service/estimates/{est['id']}", headers=H(SVC), json={"currency": "RUB", "vat_pct": 22, "hour_rate": 3500, "lines": lines}))
    assert e["lines"][1]["title"] == "Ремкомплект" and e["lines"][1]["catalog_price"] == 15000 and e["totals"]["missing_prices"] == 1
    r = client.post(f"/api/service/estimates/{est['id']}/send", headers=H(SVC), json={"channel": "email"})
    assert r.status_code == 422 and "цена" in r.json()["detail"]
    lines[1]["price"] = 15000
    lines[1]["discount_pct"] = 10
    e = _ok(client.put(f"/api/service/estimates/{est['id']}", headers=H(SVC), json={"currency": "RUB", "vat_pct": 22, "hour_rate": 3500, "lines": lines}))
    assert e["totals"]["net"] == 6 * 3500 + 2 * 15000 * 0.9 and e["totals"]["vat"] == round(e["totals"]["net"] * 0.22, 2)
    e = _ok(client.post(f"/api/service/estimates/{est['id']}/send", headers=H(SVC), json={"channel": "email"}))
    assert e["status"] == "sent" and e["total"] == e["totals"]["total"]
    assert client.put(f"/api/service/estimates/{est['id']}", headers=H(SVC), json={"lines": lines}).status_code == 422   # отправленную не правят
    assert client.post(f"/api/service/estimates/{est['id']}/decide", headers=H(SVC), json={"decision": "approved"}).status_code == 422
    e = _ok(client.post(f"/api/service/estimates/{est['id']}/decide", headers=H(SVC),
                        json={"decision": "approved", "client_name": "Главный механик Петров", "basis": "письмо от 27.11"}))
    assert e["status"] == "approved" and e["decided_by_name"]
    assert client.post(f"/api/service/orders/{wo['id']}/estimates", headers=H(SVC), json={}).status_code == 422       # без причины
    e2 = _ok(client.post(f"/api/service/orders/{wo['id']}/estimates", headers=H(SVC), json={"reason": "доп. работы"}), 201)
    assert e2["version"] == 2 and len(e2["lines"]) == 2 and e2["hour_rate"] == 3500
    all_e = _ok(client.get(f"/api/service/orders/{wo['id']}/estimates", headers=H(FD)))["items"]
    assert [x["status"] for x in all_e] == ["draft", "superseded"]
    # документы: смета появилась при отправке, дефектная ведомость — по запросу; акт — только когда наряд готов к закрытию
    docs = _ok(client.get(f"/api/service/orders/{wo['id']}/documents", headers=H(SVC)))["items"]
    assert [d["kind"] for d in docs] == ["estimate"] and docs[0]["number"].startswith("СМ-") and docs[0]["legal_checked"] is False
    dv = _ok(client.post(f"/api/service/orders/{wo['id']}/documents", headers=H(SVC), json={"kind": "defect_list"}), 201)
    assert dv["number"].startswith("ДВ-")
    assert client.post(f"/api/service/orders/{wo['id']}/documents", headers=H(SVC), json={"kind": "act"}).status_code == 422
    f = client.get(dv["url"], headers=H(ENG))
    assert f.status_code == 200
    from docx import Document
    text = "\n".join(p.text for p in Document(io.BytesIO(f.content)).paragraphs)
    assert "не проверен юристом" in text and "Износ уплотнений" in text and "15 000" not in text   # в ведомости нет цен
    eng_docs = _ok(client.get(f"/api/service/orders/{wo['id']}/documents", headers=H(ENG)))["items"]
    assert {d["kind"] for d in eng_docs} == {"defect_list"}                                       # смету инженер не видит
    assert client.get(docs[0]["url"], headers=H(ENG)).status_code == 404
    est_text = "\n".join(c.text for t in Document(io.BytesIO(client.get(docs[0]["url"], headers=H(SVC)).content)).tables for r in t.rows for c in r.cells)
    assert "27 000.00" in est_text                                                                # 2 × 15 000 − 10 %


def test_billing_event_and_act(client):  # noqa: F811
    _, wo = _order(client, "CONS-REM")
    wo = _assign_with_permits(client, wo)
    r = _ok(_ev(client, wo, "billing", who=SVC, payload={"billing": "warranty"}))
    assert r["event"]["result"] == "applied" and r["order"]["billing"] == "warranty" and r["event"]["reason"]   # срок гарантии не указан
    assert _ev(client, r["order"], "billing", payload={"billing": "client"}).status_code == 403                    # инженер не меняет оплату
    assert _ok(_ev(client, r["order"], "billing", who=SVC, payload={"billing": "free"}))["event"]["result"] == "rejected"
    wo = _ok(_ev(client, r["order"], "start"))["order"]
    wo = _ok(_ev(client, wo, "checklist", payload={"item_id": wo["checklist"][0]["id"], "done": True}))["order"]
    wo = _ok(_ev(client, wo, "close", payload={"signed_by_name": "Иванов И. И."}))["order"]
    act = _ok(client.post(f"/api/service/orders/{wo['id']}/documents", headers=H(SVC), json={"kind": "act"}), 201)
    from docx import Document
    text = "\n".join(p.text for p in Document(io.BytesIO(client.get(act["url"], headers=H(SVC)).content)).paragraphs)
    assert act["number"].startswith("АВР-") and "Иванов И. И." in text
    assert client.post(f"/api/service/orders/{wo['id']}/documents", headers=H(ENG), json={"kind": "act"}).status_code == 403
