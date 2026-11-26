"""D-188: единый справочник контактов (этап 2) — один человек у нескольких клиентов, доп. адреса, история,
адресаты КП только из справочника без заранее отмеченных, «Кому» и «Копия» раздельно, неизменяемый снимок отправки."""
import uuid

from tests.test_api_flow import client, _ok, H, MGR, CD  # noqa: F401
from tests.test_negotiation import _approved

GD = "evgeniy@dobrov.pro"


def _inn() -> str:
    import random
    d = [random.randint(0, 9) for _ in range(9)]
    d[0] = d[0] or 7
    return "".join(map(str, d)) + str(sum(c * x for c, x in zip([2, 4, 10, 3, 5, 9, 4, 6, 8], d)) % 11 % 10)


def _two(c):
    cls = _ok(c.get("/api/refs/clients", headers=H(MGR)))
    while len(cls) < 2:
        _ok(c.post("/api/refs/clients", headers=H(MGR), json={"name": f"ООО «Контакты-{uuid.uuid4().hex[:4]}»", "inn": _inn()}), 201)
        cls = _ok(c.get("/api/refs/clients", headers=H(MGR)))
    return cls[0], cls[1]


def _contact(c, cid, **kw):
    t = uuid.uuid4().hex[:6]
    return _ok(c.post("/api/clients/contacts", headers=H(MGR), json={"client_id": cid, "full_name": f"Смирнов {t}", "work_email": f"sm.{t}@x.example", **kw}), 201)


def test_one_person_two_clients_roles_and_periods(client):  # noqa: F811
    a, b = _two(client)
    p = _contact(client, a["id"], roles=["commercial"], position="Коммерческий директор")
    # тот же человек у другого клиента — не второй контакт, а привязка
    r = client.post("/api/clients/contacts", headers=H(MGR), json={"client_id": b["id"], "full_name": p["full_name"], "work_email": p["work_email"]})
    assert r.status_code == 409 and r.json()["detail"]["contact_id"] == p["id"]
    lb = _ok(client.post(f"/api/clients/contacts/{p['id']}/links", headers=H(MGR), json={"client_id": b["id"], "roles": ["poa"], "valid_from": "2026-01-01", "valid_to": "2027-12-31"}), 201)
    assert lb["roles"] == ["poa"] and lb["client_id"] == b["id"]
    assert client.post(f"/api/clients/contacts/{p['id']}/links", headers=H(MGR), json={"client_id": b["id"]}).status_code == 409
    ia = _ok(client.get(f"/api/clients/contacts?client_id={a['id']}", headers=H(MGR)))["items"]
    ib = _ok(client.get(f"/api/clients/contacts?client_id={b['id']}", headers=H(MGR)))["items"]
    xa, xb = next(x for x in ia if x["id"] == p["id"]), next(x for x in ib if x["id"] == p["id"])
    assert xa["roles"] == ["commercial"] and xb["roles"] == ["poa"] and xb["valid_to"] == "2027-12-31"   # роли и сроки не смешиваются
    assert b["name"] in xa["other_clients"] and a["name"] in xb["other_clients"]
    assert [x["id"] for x in _ok(client.get(f"/api/clients/contacts?client_id={b['id']}&role=legal_any", headers=H(MGR)))["items"]] == [p["id"]]
    # правка человека видна у обоих клиентов; связь второго клиента не меняется
    _ok(client.put(f"/api/clients/contacts/{p['id']}", headers=H(MGR), json={"client_id": a["id"], "full_name": p["full_name"], "work_email": p["work_email"],
                                                                         "position": "Генеральный директор", "roles": ["commercial", "decision"]}))
    xb = next(x for x in _ok(client.get(f"/api/clients/contacts?client_id={b['id']}", headers=H(MGR)))["items"] if x["id"] == p["id"])
    assert xb["position"] == "Генеральный директор" and xb["roles"] == ["poa"]
    # история: было / стало — только у тех, кто правит
    h = _ok(client.get(f"/api/clients/contacts/{p['id']}/history", headers=H(MGR)))
    assert any(r["field"] == "position" and r["old"] == "Коммерческий директор" and r["new"] == "Генеральный директор" for r in h)
    assert client.get(f"/api/clients/contacts/{p['id']}/history", headers=H("logist@example.com")).status_code == 403


def test_extra_emails_validation(client):  # noqa: F811
    a, _ = _two(client)
    t = uuid.uuid4().hex[:6]
    p = _contact(client, a["id"], extra_emails=[{"email": f"Second.{t}@x.example", "label": "для КП"}, {"email": f"second.{t}@x.example"}, {"email": ""}])
    assert p["extra_emails"] == [{"email": f"second.{t}@x.example", "label": "для КП"}] and p["emails"][1] == f"second.{t}@x.example"
    assert client.post("/api/clients/contacts", headers=H(MGR), json={"client_id": a["id"], "full_name": "X", "extra_emails": [{"email": "плохо"}]}).status_code == 422
    # доп. адрес совпадает с адресом другого контакта у того же клиента — дубль
    assert client.post("/api/clients/contacts", headers=H(MGR), json={"client_id": a["id"], "full_name": "Y", "work_email": f"second.{t}@x.example"}).status_code == 409


def test_kp_recipients_explicit_choice_cc_and_snapshot(client):  # noqa: F811
    from app.adapters.mail import ConsoleMail
    from app.db import SessionLocal
    from app.models import ClientContact, KpDispatch, KpDispatchRecipient
    did, _, _ = _approved(client)
    deal = _ok(client.get(f"/api/deals/{did}", headers=H(CD)))
    a = _contact(client, deal["client_id"], roles=["purchasing"], position="Закупки", extra_emails=[{"email": f"tender.{uuid.uuid4().hex[:5]}@x.example"}])
    b = _contact(client, deal["client_id"], roles=["decision"])
    rc = _ok(client.get(f"/api/deals/{did}/kp/recipients", headers=H(MGR)))
    mine = {c["contact_id"]: c for c in rc["candidates"] if c["contact_id"] in (a["id"], b["id"])}
    assert set(mine) == {a["id"], b["id"]} and mine[a["id"]]["emails"] == a["emails"]
    assert not any("selected" in c for c in rc["candidates"])                         # ничего не отмечено заранее
    # проверка без отправки
    pv = _ok(client.post(f"/api/deals/{did}/kp/recipients/preview", headers=H(MGR), json={"to": [a["emails"][1]], "cc": [b["work_email"]]}))
    assert [(i["kind"], i["email"], i["contact_id"]) for i in pv["items"]] == [("to", a["emails"][1], a["id"]), ("cc", b["work_email"], b["id"])]
    for bad, code in (({"to": ["stranger@x.example"]}, 422), ({"to": [], "cc": [b["work_email"]]}, 422), ({"to": [b["work_email"]], "cc": [b["work_email"]]}, 422)):
        assert client.post(f"/api/deals/{did}/kp/recipients/preview", headers=H(MGR), json=bad).status_code == code, bad
    r = client.post(f"/api/deals/{did}/kp/recipients/preview", headers=H(MGR), json={"to": ["stranger@x.example"]})
    assert r.json()["detail"]["unknown"] == ["stranger@x.example"]
    # ручная отметка — снимок; повтор с тем же ключом — без второго снимка
    key = uuid.uuid4().hex
    body = {"recipients": [a["emails"][1]], "cc": [b["work_email"]], "sent_via": "outlook", "dispatch_key": key}
    _ok(client.post(f"/api/deals/{did}/kp-sent", headers=H(MGR), json=body))
    assert _ok(client.post(f"/api/deals/{did}/kp-sent", headers=H(MGR), json=body)).get("repeated") is True
    with SessionLocal() as db:
        ds = db.query(KpDispatch).filter(KpDispatch.deal_id == did).all()
        assert len(ds) == 1 and ds[0].method == "manual"
        rs = db.query(KpDispatchRecipient).filter(KpDispatchRecipient.dispatch_id == ds[0].id).order_by(KpDispatchRecipient.position).all()
        assert [(r.kind, r.email, r.display_name, r.job_title) for r in rs] == [("to", a["emails"][1], a["full_name"], "Закупки"), ("cc", b["work_email"], b["full_name"], "")]
        # правка контакта после отправки снимок не меняет
        c = db.get(ClientContact, a["id"])
        c.full_name, c.position = "Переименован", "Другая должность"
        db.commit()
        r0 = db.query(KpDispatchRecipient).filter(KpDispatchRecipient.dispatch_id == ds[0].id, KpDispatchRecipient.kind == "to").one()
        assert (r0.display_name, r0.job_title) == (a["full_name"], "Закупки")
    hist = _ok(client.get(f"/api/deals/{did}/kp/recipients", headers=H(MGR)))["dispatches"]
    assert hist and hist[0]["recipients"][1] == {"kind": "cc", "name": b["full_name"], "position": "", "email": b["work_email"]}
    assert ConsoleMail is not None


def test_system_send_keeps_cc_separate(client, monkeypatch):  # noqa: F811
    from app.adapters.mail import ConsoleMail, OutgoingMail
    from app.api import pricing
    did, _, _ = _approved(client)
    deal = _ok(client.get(f"/api/deals/{did}", headers=H(CD)))
    a, b = _contact(client, deal["client_id"]), _contact(client, deal["client_id"])
    monkeypatch.setattr(pricing, "_mail_provider", lambda: "smtp")
    sent: list[OutgoingMail] = []
    monkeypatch.setattr(ConsoleMail, "send", lambda self, msg: (sent.append(msg), "msg-1")[1])
    key = uuid.uuid4().hex
    r = client.post(f"/api/deals/{did}/kp/send", headers=H(MGR), json={"to": [a["work_email"]], "cc": [b["work_email"]], "format": "docx", "dispatch_key": key})
    assert r.status_code == 200, r.text
    kp = [m for m in sent if m.subject.startswith("Коммерческое предложение")]
    assert len(kp) == 1 and kp[0].to == [a["work_email"]] and kp[0].cc == [b["work_email"]]   # копия — копией, а не «Кому»
    assert _ok(client.post(f"/api/deals/{did}/kp/send", headers=H(MGR), json={"to": [a["work_email"]], "format": "docx", "dispatch_key": key}))["repeated"] is True
    assert len([m for m in sent if m.subject.startswith("Коммерческое предложение")]) == 1


def test_no_auto_draft_on_approval(client, monkeypatch):  # noqa: F811
    from app.adapters.mail import ConsoleMail
    drafts = []
    monkeypatch.setattr(ConsoleMail, "create_draft", lambda self, msg: (drafts.append(msg), "d")[1])
    _approved(client)
    assert drafts == []


def test_mobile_phone(client):  # noqa: F811
    """D-190: мобильный (рабочий) телефон — проверка формата, дубль по любому из телефонов, стирается при обезличивании."""
    a, _ = _two(client)
    import random
    n = f"{random.randint(0, 9999999):07d}"
    p = _contact(client, a["id"], mobile_phone=f"+7 (913) {n[:3]}-{n[3:5]}-{n[5:]}")
    assert p["mobile_phone"].endswith(n[5:])
    assert client.post("/api/clients/contacts", headers=H(MGR), json={"client_id": a["id"], "full_name": "M", "mobile_phone": "звонить"}).status_code == 422
    # тот же номер как рабочий у другого контакта клиента — дубль
    assert client.post("/api/clients/contacts", headers=H(MGR), json={"client_id": a["id"], "full_name": "Дубль", "work_phone": f"8 913 {n}"}).status_code == 409
    _ok(client.post(f"/api/clients/contacts/{p['id']}/delete", headers=H(MGR), json={"reason": "запрос субъекта"}))
    from app.db import SessionLocal
    from app.models import ClientContact
    with SessionLocal() as db:
        assert db.get(ClientContact, p["id"]).mobile_phone == ""
