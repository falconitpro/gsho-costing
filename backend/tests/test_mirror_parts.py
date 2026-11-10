"""Зеркальные запросы цен на запчасти через компанию группы (D-156…D-159): заявка → запрос в компанию группы с конечным
поставщиком → её запрос поставщику (с её каналов) → ответ → предложение компании группы по маршруту и наценкам →
сравнение, выбор, изменение после выбора, права."""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import date

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Factory, FxRate, LegalEntity, MarkupPolicy, NotificationLog, SupplierRfq  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, FD, LOG = "manager@example.com", "cd@example.com", "fd@example.com", "logist@example.com"


def _ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:800])
    return r.json() if r.content else None


def _setup(t: str):
    with SessionLocal() as db:
        cn = db.scalar(select(LegalEntity).where(LegalEntity.code == "cn"))
        cn.channels = {"mail_profile": "alliance", "mail_from": "rfq@alliance.example", "wecom_profile": "alliance"}
        ai = Factory(name=f"Альянс-Импекс {t}", country="CN", supplier_status="group_company", legal_entity_id=cn.id, default_currency="CNY")
        x = Factory(name=f"Завод запчастей {t}", country="CN", default_currency="CNY", contacts=[{"name": "Li", "email": f"x{t}@x.cn", "roles": ["rfq"]}])
        db.add_all([ai, x])
        if not db.scalar(select(FxRate).where(FxRate.currency == "CNY")):
            db.add(FxRate(currency="CNY", rate=12.5, on=date.today(), source="test"))
        for p in db.scalars(select(MarkupPolicy).where(MarkupPolicy.status == "approved")).all():
            p.status = "superseded"
        last = db.scalars(select(MarkupPolicy).order_by(MarkupPolicy.version_no.desc())).first()
        rules = {**(last.rules if last else {})}
        rules["default"] = {**(rules.get("default") or {}), cn.id: 10.0}
        db.add(MarkupPolicy(version_no=(last.version_no + 1) if last else 1, status="approved", reason="тест запчастей", valid_from=None, rules=rules))
        db.commit()
        return cn.id, ai.id, x.id


def test_mirror_parts_flow():
    seed()
    t = uuid.uuid4().hex[:6].upper()
    cn_id, ai_id, x_id = _setup(t)
    with TestClient(app) as c:
        cl = _ok(c.get("/api/refs/clients", headers=H(MGR)))[0]
        _ok(c.post("/api/parts/catalog", headers=H(CD), json={"article": f"MP-{t}", "name_ru": "Фильтр масляный", "names": {"cn": "机油滤清器"}}), 201)
        r = _ok(c.post("/api/parts/requests", headers=H(MGR), json={"client_id": cl["id"], "lines_text": f"MP-{t}\t\t3\nMP2-{t}\tПалец\t2"}), 201)
        rid, ids = r["id"], [l["id"] for l in r["lines"]]
        url = f"/api/parts/requests/{rid}/rfq"

        assert c.post(url, headers=H(MGR), json={"line_ids": ids, "supplier_ids": [ai_id]}).status_code == 422          # нет конечного поставщика
        assert c.post(url, headers=H(MGR), json={"line_ids": ids, "supplier_ids": [ai_id], "via_group": {ai_id: [ai_id]}}).status_code == 422
        res = _ok(c.post(url, headers=H(MGR), json={"line_ids": ids, "supplier_ids": [ai_id], "via_group": {ai_id: [x_id]}}), 201)
        assert len(res["rfqs"]) == 1                                         # менеджер видит только предложение компании группы
        up = res["rfqs"][0]
        assert up["supplier_id"] == ai_id and up["mirror"]["role"] == "upper" and up["mirror"]["end_supplier"] == f"Завод запчастей {t}"
        assert up["mirror"]["route"] == "Поставщик КНР → компания в Китае → РФ" and up["mirror"]["state"] == "waiting"
        low_id = up["mirror"]["lower_id"]
        assert c.get(f"/api/parts/rfq/{low_id}", headers=H(MGR)).status_code == 403
        low = _ok(c.get(f"/api/parts/rfq/{low_id}", headers=H(CD)))
        assert low["supplier_id"] == x_id and low["language"] == "zh" and low["mirror"]["role"] == "lower"
        assert low["lines"][0]["name_foreign"] == "机油滤清器"
        assert {x["id"] for x in _ok(c.get(f"/api/parts/rfq?request_id={rid}", headers=H(MGR)))} == {up["id"]}
        assert {x["id"] for x in _ok(c.get(f"/api/parts/rfq?request_id={rid}", headers=H(CD)))} == {up["id"], low_id}
        assert {x["id"] for x in _ok(c.get(f"/api/parts/rfq?request_id={rid}", headers=H(FD)))} == {low_id}   # ФД — только цепочка

        # отправка из предложения: уходит запрос поставщику, от имени компании группы
        assert c.post(f"/api/parts/rfq/{low_id}/send", headers=H(CD), json={}).status_code == 409
        sent = _ok(c.post(f"/api/parts/rfq/{up['id']}/send", headers=H(MGR), json={}))
        assert sent["send_results"][0]["via"] == "mail" and sent["status"] == "draft"          # консольная почта стенда — не ушло
        with SessionLocal() as db:
            lo = db.get(SupplierRfq, low_id)
            assert lo.send_log[-1]["from"] and lo.send_log[0]["to"] == [f"x{t}@x.cn"]
        _ok(c.post(f"/api/parts/rfq/{up['id']}/mark-sent", headers=H(MGR)))
        with SessionLocal() as db:
            assert db.get(SupplierRfq, low_id).status == "sent"

        # ответ: в предложение компании группы — нельзя; менеджеру — нет права; КД вносит ответ поставщика
        assert c.put(f"/api/parts/rfq/{up['id']}/answers", headers=H(CD), json={"lines": []}).status_code == 409
        l1, l2 = low["lines"]
        ans = {"lines": [{"id": l1["id"], "availability": "available", "price": 100, "currency": "CNY", "lead_days": 10, "moq": 1},
                         {"id": l2["id"], "availability": "none"}]}
        assert c.put(f"/api/parts/rfq/{low_id}/answers", headers=H(MGR), json=ans).status_code == 403
        _ok(c.put(f"/api/parts/rfq/{low_id}/answers", headers=H(CD), json=ans))

        u_m = _ok(c.get(f"/api/parts/rfq/{up['id']}", headers=H(MGR)))
        u1, u2 = u_m["lines"]
        assert u_m["status"] == "answered" and u_m["mirror"]["state"] == "ok"
        assert u1["availability"] == "available" and u1["lead_days"] == 10 and u1["price"] > 0 and "chain" not in u1
        assert u2["availability"] == "none" and u2["price"] is None
        u_cd = _ok(c.get(f"/api/parts/rfq/{up['id']}", headers=H(CD)))["lines"][0]
        steps = u_cd["chain"]["steps"]
        assert u_cd["chain"]["supplier_price"] == 100 and steps[0]["price"] == 100 and steps[-1]["price"] == u1["price"]
        assert u_cd["currency"] == steps[-1]["currency"] and u_cd["chain"]["group_margin_rub"] > 0

        # сравнение: менеджер — только предложение компании группы; КД — и ответ поставщика (не выбирается)
        qm = _ok(c.get(f"/api/parts/requests/{rid}/quotes", headers=H(MGR)))[ids[0]]
        assert [q["supplier_id"] for q in qm] == [ai_id] and "chain" not in qm[0]
        qc = _ok(c.get(f"/api/parts/requests/{rid}/quotes", headers=H(CD)))[ids[0]]
        assert {(q["supplier_id"], q["selectable"]) for q in qc} == {(ai_id, True), (x_id, False)}
        lower_q = next(q for q in qc if q["supplier_id"] == x_id)
        assert c.post(f"/api/parts/requests/{rid}/lines/{ids[0]}/choose", headers=H(MGR), json={"rfq_line_id": lower_q["id"]}).status_code == 422

        # выбор: в заявке поставщик X, маршрут через компанию группы, цена X; менеджер видит компанию группы и её цену
        ch = _ok(c.post(f"/api/parts/requests/{rid}/lines/{ids[0]}/choose", headers=H(MGR), json={"rfq_line_id": u1["id"]}))
        L = next(l for l in ch["lines"] if l["id"] == ids[0])
        assert (L["supplier_id"], L["quote_price"], L["quote_currency"]) == (ai_id, u1["price"], u1["currency"])
        assert L["via_group"]["end_supplier"] == f"Завод запчастей {t}" and "cost" not in L
        Lc = next(l for l in _ok(c.get(f"/api/parts/requests/{rid}", headers=H(CD)))["lines"] if l["id"] == ids[0])
        assert (Lc["supplier_id"], Lc["quote_price"], Lc["quote_currency"]) == (x_id, 100, "CNY")
        with SessionLocal() as db:
            fx = {r.currency: r.rate for r in db.scalars(select(FxRate).order_by(FxRate.on)).all()}
        rate = 1.0 if u1["currency"] == "RUB" else fx[u1["currency"]]
        assert abs(Lc["cost"] - u1["price"] * rate) < 0.05                # себестоимость = цена компании группы в рублях
        # менеджер «выбирает» в строке ту же компанию группы — связь не теряется
        _ok(c.patch(f"/api/parts/requests/{rid}/lines/{ids[0]}", headers=H(MGR), json={"supplier_id": ai_id}))
        Lc = next(l for l in _ok(c.get(f"/api/parts/requests/{rid}", headers=H(CD)))["lines"] if l["id"] == ids[0])
        assert (Lc["supplier_id"], Lc["rfq_line_id"], Lc["quote_price"]) == (x_id, u1["id"], 100)

        # поставщик изменил ответ после выбора: цена в заявке не меняется, отметка и уведомление КД
        ans["lines"][0]["price"] = 120
        _ok(c.put(f"/api/parts/rfq/{low_id}/answers", headers=H(CD), json=ans))
        u_cd = _ok(c.get(f"/api/parts/rfq/{up['id']}", headers=H(CD)))
        assert u_cd["mirror"]["state"] == "changed_after_select" and u_cd["lines"][0]["price"] == u1["price"]
        assert u_cd["lines"][0]["chain"]["pending"]["price"] > u1["price"]
        assert next(l for l in _ok(c.get(f"/api/parts/requests/{rid}", headers=H(CD)))["lines"] if l["id"] == ids[0])["quote_price"] == 100
        with SessionLocal() as db:
            assert db.scalar(select(NotificationLog).where(NotificationLog.event == "mirror_changed", NotificationLog.text.like(f"{up['number']}%")))
        # повторный выбор принимает новую цену
        ch = _ok(c.post(f"/api/parts/requests/{rid}/lines/{ids[0]}/choose", headers=H(CD), json={"rfq_line_id": u1["id"]}))
        assert next(l for l in ch["lines"] if l["id"] == ids[0])["quote_price"] == 120
        u_cd = _ok(c.get(f"/api/parts/rfq/{up['id']}", headers=H(CD)))
        assert u_cd["mirror"]["state"] == "ok" and "pending" not in u_cd["lines"][0]["chain"]

        # заказ: внешний — поставщику X в его валюте, внутригрупповые — по ступеням; последняя = цена компании группы
        pos = _ok(c.post("/api/parts/po", headers=H(CD), json={"supplier_id": x_id, "request_line_ids": [ids[0]]}), 201)
        assert len(pos) == 1 and pos[0]["supplier_id"] == x_id and pos[0]["total"] == 3 * 120 and pos[0]["intragroup"]
        last = pos[0]["intragroup"][-1]["lines"][0]
        assert abs(last["price"] - u_cd["lines"][0]["price"]) < 0.01

        # без наценок группы предложение не считается
        with SessionLocal() as db:
            for p in db.scalars(select(MarkupPolicy).where(MarkupPolicy.status.in_(("approved", "superseded")))).all():
                p.status = "draft"
            db.commit()
        try:
            ans["lines"][0]["price"] = 130
            _ok(c.put(f"/api/parts/rfq/{low_id}/answers", headers=H(CD), json=ans))
            assert _ok(c.get(f"/api/parts/rfq/{up['id']}", headers=H(CD)))["mirror"]["state"] in ("no_policy", "changed_after_select")
        finally:
            with SessionLocal() as db:
                p = db.scalars(select(MarkupPolicy).where(MarkupPolicy.reason == "тест запчастей").order_by(MarkupPolicy.version_no.desc())).first()
                p.status = "approved"
                db.commit()

        # закрытие пары
        _ok(c.post(f"/api/parts/rfq/{up['id']}/close", headers=H(MGR)))
        with SessionLocal() as db:
            assert db.get(SupplierRfq, low_id).status == "closed"
