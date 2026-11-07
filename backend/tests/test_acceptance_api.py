"""Приёмка на заводе, ЗИП позиции и документы модели на живой PostgreSQL."""
from __future__ import annotations

import os
import tempfile
import uuid

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, SRV, LOG = "manager@example.com", "cd@example.com", "service@example.com", "logist@example.com"


def _ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:800])
    return r.json() if r.content else None


def test_acceptance_flow():
    seed()
    t = uuid.uuid4().hex[:5].upper()
    with TestClient(app) as c:
        cl = _ok(c.get("/api/refs/clients", headers=H(MGR)))[0]
        fac = next(f for f in _ok(c.get("/api/refs/factories", headers=H(MGR))) if f["approval_status"] == "approved")
        d = _ok(c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "рудник"}), 201)
        p = _ok(c.post(f"/api/deals/{d['id']}/positions", headers=H(MGR), json={"factory_id": fac["id"], "model": f"ACC-{t}", "machine_type": "Погрузчик",
                                                                              "qty": 2, "unit_price": 100000, "currency": "CNY"}), 201)
        # ЗИП позиции
        assert c.put(f"/api/deals/{d['id']}/positions/{p['id']}/zip", headers=H(LOG), json={"items": []}).status_code == 403
        z = _ok(c.put(f"/api/deals/{d['id']}/positions/{p['id']}/zip", headers=H(MGR), json={"items": [
            {"assembly": "Двигатель", "article": "F-1", "name": "Фильтр масляный", "qty": 2, "unit": "шт", "price": 15, "currency": "usd", "kind": "supply", "lead_days": 30},
            {"assembly": "Гидравлика", "article": "H-1", "name": "Фильтр гидравлический", "qty": 1, "interval_hours": 500, "kind": "to"}, {"name": ""}]}))
        assert len(z["items"]) == 2 and z["items"][0]["currency"] == "USD"
        # документы модели — из сделки, хранятся у модели
        doc = _ok(c.post(f"/api/deals/{d['id']}/positions/{p['id']}/documents", headers=H(MGR), files={"file": ("manual.pdf", b"%PDF-1.4 test")},
                         data={"kind": "manual", "language": "ru", "version": "v1"}), 201)
        assert doc["kind_title"].startswith("Руководство") and doc["version"] == "v1"
        docs = _ok(c.get(f"/api/deals/{d['id']}/positions/{p['id']}/documents", headers=H(CD)))
        assert docs["model_id"] and [x["id"] for x in docs["documents"]] == [doc["id"]]
        assert _ok(c.get(f"/api/catalog/models/{docs['model_id']}/documents", headers=H(CD)))[0]["file_name"] == "manual.pdf"
        assert c.get(doc["url"], headers=H(LOG)).content == b"%PDF-1.4 test"
        # приёмка: карточки на каждую машину
        assert c.post(f"/api/deals/{d['id']}/acceptance/init", headers=H(LOG)).status_code == 403
        a = _ok(c.post(f"/api/deals/{d['id']}/acceptance/init", headers=H(MGR)))
        assert a["created"] == 2 and len(a["units"]) == 2
        u1, u2 = a["units"]
        items = {(x["group"], x["item"]) for x in u1["checklist"]}
        assert ("Комплектность: ЗИП в комплекте поставки", "F-1 Фильтр масляный") in items and not any("H-1" in i for _, i in items)
        assert any(g == "Документация" and "каталоге" in i for g, i in items)
        assert _ok(c.post(f"/api/deals/{d['id']}/acceptance/init", headers=H(MGR)))["created"] == 0
        r = c.post(f"/api/deals/{d['id']}/acceptance/{u1['id']}/decision", headers=H(MGR), json={"decision": "accepted"})
        assert r.status_code == 422 and "заводской номер" in r.text
        ok_cl = [{**x, "result": "ok"} for x in u1["checklist"]]
        ok_ts = [{**x, "result": "ok"} for x in u1["tests"]]
        _ok(c.put(f"/api/deals/{d['id']}/acceptance/{u1['id']}", headers=H(MGR), json={"serial": f"SN-{t}-1", "engine_no": "E1", "checklist": ok_cl, "tests": ok_ts,
                                                                                      "inspector": "Иванов", "factory_rep": "Ли"}))
        _ok(c.post(f"/api/deals/{d['id']}/acceptance/{u1['id']}/files", headers=H(MGR), files={"file": ("test.mp4", b"video")}, data={"kind": "video"}), 201)
        x = _ok(c.post(f"/api/deals/{d['id']}/acceptance/{u1['id']}/decision", headers=H(MGR), json={"decision": "accepted"}))
        assert x["decision"] == "accepted" and x["machine_id"] and x["files"][0]["kind"] == "video"
        park = _ok(c.get(f"/api/parts/fleet/machines?client_id={cl['id']}", headers=H(MGR)))
        assert any(m["serial"] == f"SN-{t}-1" and m["deal_id"] == d["id"] for m in park)
        assert c.put(f"/api/deals/{d['id']}/acceptance/{u1['id']}", headers=H(MGR), json={"serial": "X"}).status_code == 409   # после приёмки — только КД / ЗГД
        assert _ok(c.put(f"/api/deals/{d['id']}/acceptance/{u1['id']}", headers=H(CD), json={"serial": f"SN-{t}-1A"}))["serial"] == f"SN-{t}-1A"
        assert any(m["serial"] == f"SN-{t}-1A" for m in _ok(c.get(f"/api/parts/fleet/machines?client_id={cl['id']}", headers=H(MGR))))
        # вторая машина: замечание не устранено — «с замечаниями»
        cl2 = [{**x, "result": "ok"} for x in u2["checklist"]]
        cl2[0]["result"] = "fail"
        _ok(c.put(f"/api/deals/{d['id']}/acceptance/{u2['id']}", headers=H(MGR), json={"serial": f"SN-{t}-2", "checklist": cl2, "tests": ok_ts,
                                                                                      "remarks": [{"text": "царапина на кузове", "responsible": "завод", "due": "2026-12-01"}]}))
        assert c.post(f"/api/deals/{d['id']}/acceptance/{u2['id']}/decision", headers=H(MGR), json={"decision": "accepted"}).status_code == 422
        from app.api.acceptance import position_accepted
        db = SessionLocal()
        assert position_accepted(db, p["id"]) is False
        db.close()
        y = _ok(c.post(f"/api/deals/{d['id']}/acceptance/{u2['id']}/decision", headers=H(MGR), json={"decision": "accepted_remarks", "comment": "устранить до отгрузки"}))
        assert y["open_remarks"] == 1
        db = SessionLocal()
        assert position_accepted(db, p["id"]) is True
        db.close()
        _ok(c.put(f"/api/deals/{d['id']}/acceptance/{u2['id']}/act", headers=H(MGR), json={"number": "A-1", "on": "2026-11-07", "signed_factory_on": "2026-11-07"}))
        ad = c.get(f"/api/deals/{d['id']}/acceptance/{u2['id']}/act.docx", headers=H(CD))
        assert ad.status_code == 200 and ad.content[:2] == b"PK"
        _ok(c.post(f"/api/deals/{d['id']}/acceptance/{u2['id']}/act/file", headers=H(MGR), files={"file": ("act.pdf", b"%PDF scan")}), 201)
        assert c.get(f"/api/deals/{d['id']}/acceptance/{u2['id']}/act/file", headers=H(MGR)).content == b"%PDF scan"
        # повторная приёмка: онлайн, затем на заводе; история и пометка «не принято ранее»
        assert c.post(f"/api/deals/{d['id']}/acceptance/{u2['id']}/rounds", headers=H(MGR), json={"mode": "space"}).status_code == 422
        r2 = _ok(c.post(f"/api/deals/{d['id']}/acceptance/{u2['id']}/rounds", headers=H(MGR), json={"mode": "online"}))
        assert r2["round_no"] == 2 and r2["mode"] == "online" and r2["is_final"] is False and r2["decision"] == ""
        assert r2["history"][0]["round_no"] == 1 and r2["history"][0]["decision"] == "accepted_remarks" and r2["history"][0]["act_number"] == "A-1"
        assert r2["history"][0]["fails"] == [cl2[0]["item"]] and r2["prev_fail_count"] == 1
        first = next(x for x in r2["checklist"] if x["id"] == cl2[0]["id"])
        assert first["prev_fail"] == [1] and first["result"] == "" and all(x["result"] == "" for x in r2["checklist"])
        assert len(r2["remarks"]) == 1 and r2["remarks"][0]["from_round"] == 1                  # неустранённое замечание перенесено
        db = SessionLocal()
        assert position_accepted(db, p["id"]) is False                                         # идёт новая приёмка
        db.close()
        ok2 = [{**x, "result": "ok"} for x in r2["checklist"]]
        rem = [{**r2["remarks"][0], "fixed_on": "2026-11-20", "fixed_note": "окрашено"}]
        _ok(c.put(f"/api/deals/{d['id']}/acceptance/{u2['id']}", headers=H(MGR), json={"checklist": ok2, "tests": [{**x, "result": "ok"} for x in r2["tests"]], "remarks": rem}))
        z = _ok(c.post(f"/api/deals/{d['id']}/acceptance/{u2['id']}/decision", headers=H(MGR), json={"decision": "accepted"}))
        assert z["decision"] == "accepted"
        db = SessionLocal()
        assert position_accepted(db, p["id"]) is False                                         # онлайн — не окончательная
        db.close()
        assert c.put(f"/api/deals/{d['id']}/acceptance/{u2['id']}", headers=H(MGR), json={"is_final": True}).status_code == 409
        r3 = _ok(c.post(f"/api/deals/{d['id']}/acceptance/{u2['id']}/rounds", headers=H(MGR), json={"mode": "factory"}))
        assert r3["round_no"] == 3 and r3["is_final"] is True and [h["round_no"] for h in r3["history"]] == [2, 1]
        assert next(x for x in r3["checklist"] if x["id"] == cl2[0]["id"])["prev_fail"] == [1]
        _ok(c.put(f"/api/deals/{d['id']}/acceptance/{u2['id']}", headers=H(MGR), json={"checklist": [{**x, "result": "ok"} for x in r3["checklist"]],
                                                                                      "tests": [{**x, "result": "ok"} for x in r3["tests"]]}))
        _ok(c.post(f"/api/deals/{d['id']}/acceptance/{u2['id']}/decision", headers=H(MGR), json={"decision": "accepted"}))
        db = SessionLocal()
        assert position_accepted(db, p["id"]) is True
        db.close()
        # документы машины при приёмке → карточка машины в парке; руководство — общим документом модели
        _ok(c.post(f"/api/deals/{d['id']}/acceptance/{u1['id']}/files", headers=H(MGR), files={"file": ("passport.pdf", b"%PDF passport")},
                   data={"kind": "passport", "title": "Паспорт машины"}), 201)
        _ok(c.post(f"/api/deals/{d['id']}/acceptance/{u1['id']}/files", headers=H(MGR), files={"file": ("ru-manual.pdf", b"%PDF manual")},
                   data={"kind": "manual", "to_model": "true"}), 201)
        ph = _ok(c.post(f"/api/deals/{d['id']}/acceptance/{u1['id']}/files", headers=H(MGR), files={"file": ("leak.jpg", b"jpg")},
                        data={"kind": "photo", "item_id": u1["checklist"][0]["id"]}), 201)
        assert ph["files"][-1]["item_id"] == u1["checklist"][0]["id"]
        md = _ok(c.get(f"/api/parts/fleet/machines/{x['machine_id']}/documents", headers=H(MGR)))
        assert {"passport", "manual"} <= {z["kind"] for z in md["documents"]} and any(z["name"] == "leak.jpg" for z in md["media"])
        assert any(z["name"] == "ru-manual.pdf" for z in md["model_documents"])
        pas = next(z for z in md["documents"] if z["kind"] == "passport")
        assert c.get(pas["url"], headers=H(MGR)).content == b"%PDF passport"
