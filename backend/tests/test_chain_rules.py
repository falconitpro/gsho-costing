"""Правила внутригрупповых цепочек (D-164…D-167): менеджер выбирает конечного поставщика, цепочку подставляют правила ФД/ЗГД;
ФД/ЗГД правит цепочку в сделке и утверждает её до КП."""
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

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Deal, Factory, FactoryOffer, FxRate, LegalEntity, MarkupPolicy, Position  # noqa: E402
from app.services import chain_rules  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, FD, GD = "manager@example.com", "cd@example.com", "fd@example.com", "evgeniy@dobrov.pro"
T = uuid.uuid4().hex[:6].upper()


def _setup():
    with SessionLocal() as db:
        cn = db.scalar(select(LegalEntity).where(LegalEntity.code == "cn"))
        ai = Factory(name=f"Альянс-Импекс П{T}", country="CN", supplier_status="group_company", legal_entity_id=cn.id, default_currency="CNY")
        x = Factory(name=f"Китайский завод {T}", country="CN", default_currency="CNY", contacts=[{"name": "Li", "email": f"r{T}@x.cn", "roles": ["rfq"]}])
        de = Factory(name=f"Немецкий завод {T}", country="DE", default_currency="EUR", contacts=[{"name": "Hans", "email": f"r{T}@x.de", "roles": ["rfq"]}])
        db.add_all([ai, x, de])
        if not db.scalar(select(FxRate).where(FxRate.currency == "CNY")):
            db.add(FxRate(currency="CNY", rate=12.5, on=date.today(), source="test"))
        for p in db.scalars(select(MarkupPolicy).where(MarkupPolicy.status == "approved")).all():
            p.status = "superseded"
        last = db.scalars(select(MarkupPolicy).order_by(MarkupPolicy.version_no.desc())).first()
        db.add(MarkupPolicy(version_no=(last.version_no + 1) if last else 1, status="approved", reason="тест правил цепочек", valid_from=date.today(),
                            rules={**(last.rules if last else {}), "gsho_default": {cn.id: 10.0}, "gsho_by_machine_type": [], "gsho_by_supplier": []}))
        db.commit()
        return ai.id, x.id, de.id


def test_rules_route_requests_and_visa_before_kp():
    seed()
    ai_id, x_id, de_id = _setup()
    with TestClient(app) as c:
        route_id = next(r["id"] for r in c.get("/api/parts/routes", headers=H(CD)).json() if r["name"] == "Поставщик КНР → компания в Китае → РФ")
        # правила ведут ФД и ЗГД; КД видит; группа как «поставщик» и неизвестный маршрут — ошибка
        assert c.put("/api/parts/intragroup-rules", headers=H(CD), json={"rules": []}).status_code == 403
        assert c.put("/api/parts/intragroup-rules", headers=H(FD), json={"rules": [{"kind": "gsho", "route_id": "нет"}]}).status_code == 422
        assert c.put("/api/parts/intragroup-rules", headers=H(FD), json={"rules": [{"kind": "x"}]}).status_code == 422
        rules = [{"kind": "gsho", "country": "CN", "route_id": route_id, "note": "Китай — через Альянс-Импекс"},
                 {"kind": "*", "country": "DE", "route_id": None}]
        got = c.put("/api/parts/intragroup-rules", headers=H(GD), json={"rules": rules}).json()
        assert [r["route_name"] for r in got["rules"]] == ["Поставщик КНР → компания в Китае → РФ", ""] and got["can_edit"]
        assert c.get("/api/parts/intragroup-rules", headers=H(CD)).json()["can_edit"] is False
        try:
            with SessionLocal() as db:
                x, de = db.get(Factory, x_id), db.get(Factory, de_id)
                assert chain_rules.match(db, x, "gsho")["country"] == "CN" and chain_rules.match(db, x, "parts") is None
                assert chain_rules.match(db, de, "parts")["kind"] == "*"
                assert chain_rules.resolve(db, x, "gsho")[1].id == ai_id

            cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
            did = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": f"правила-{T}"}).json()["id"]
            with SessionLocal() as db:
                p1 = Position(deal_id=did, factory_id=x_id, model="CN-1", machine_type="Самосвал", qty=1, unit_price=1, currency="CNY")
                p2 = Position(deal_id=did, factory_id=de_id, model="DE-1", machine_type="Самосвал", qty=1, unit_price=1, currency="EUR")
                db.add_all([p1, p2])
                db.commit()
                p1_id, p2_id = p1.id, p2.id
            # менеджер выбирает конечного поставщика: Китай — через Альянс-Импекс по правилу, Германия — напрямую
            s1 = c.post(f"/api/deals/{did}/positions/{p1_id}/tech-params/request", headers=H(MGR), json={"factory_ids": [x_id]})
            assert s1.status_code == 200, s1.text
            s1 = s1.json()["sent"][0]
            assert s1["factory_id"] == ai_id and s1["lower_offer_id"] and "→" in s1["rule"]
            s2 = c.post(f"/api/deals/{did}/positions/{p2_id}/tech-params/request", headers=H(MGR), json={"factory_ids": [de_id]}).json()["sent"][0]
            assert s2["factory_id"] == de_id and "lower_offer_id" not in s2
            assert c.post(f"/api/deals/{did}/positions/{p1_id}/tech-params/request", headers=H(MGR), json={"factory_ids": [ai_id], "via_group": {ai_id: [x_id]}}).status_code == 422

            # ФД правит цепочку до отбора: Германия — через Альянс-Импекс, затем обратно напрямую
            assert c.post(f"/api/deals/{did}/offers/{s2['offer_id']}/chain", headers=H(CD), json={"route_id": route_id}).status_code == 403
            v = c.post(f"/api/deals/{did}/offers/{s2['offer_id']}/chain", headers=H(FD), json={"route_id": route_id}).json()
            assert v["via_group"] is True
            with SessionLocal() as db:
                lo = db.get(FactoryOffer, s2["offer_id"])
                assert lo.mirror_of_id == v["offer_id"] and db.get(FactoryOffer, v["offer_id"]).end_supplier_id == de_id
            back = c.post(f"/api/deals/{did}/offers/{v['offer_id']}/chain", headers=H(FD), json={"route_id": None}).json()
            assert back == {"offer_id": s2["offer_id"], "via_group": False}
            with SessionLocal() as db:
                assert db.get(FactoryOffer, v["offer_id"]) is None and db.get(FactoryOffer, s2["offer_id"]).mirror_of_id is None

            # ответ X, отбор предложения Альянс-Импекс; до визы — КП нельзя
            assert c.post(f"/api/deals/{did}/offers", headers=H(CD), json={"factory_id": x_id, "offer_id": s1["lower_offer_id"], "unit_price": 50000, "currency": "CNY"}).status_code == 201
            assert c.post(f"/api/deals/{did}/offers/select", headers=H(CD), json={"offer_ids": [s1["offer_id"]], "copy_calcs": False}).status_code == 200
            assert c.post(f"/api/deals/{did}/offers/{s1['offer_id']}/chain", headers=H(FD), json={"route_id": None}).status_code == 409   # после отбора — нельзя
            ch = c.get(f"/api/deals/{did}/chain", headers=H(MGR)).json()
            assert ch["needed"] and ch["rows"][0]["group_company"] and ch["can_edit"] is False and "routes" not in ch
            from app.services.mirror_offers import require_chain_visa
            with SessionLocal() as db:
                with pytest.raises(HTTPException) as e:
                    require_chain_visa(db, db.get(Deal, did))
                assert e.value.status_code == 409
            assert c.post(f"/api/deals/{did}/kp-sent", headers=H(MGR), json={"recipients": ["a@b.c"], "sent_via": "manual"}).status_code == 409
            assert c.post(f"/api/deals/{did}/chain/approve", headers=H(CD)).status_code == 403
            ok = c.post(f"/api/deals/{did}/chain/approve", headers=H(FD)).json()
            assert ok["approved"] and not ok["needed"]
            with SessionLocal() as db:
                require_chain_visa(db, db.get(Deal, did))
            # цепочка изменилась после визы (трансфертная цена) — нужна новая виза
            with SessionLocal() as db:
                db.get(FactoryOffer, s1["offer_id"]).transfer_price = 52000
                db.commit()
            st = c.get(f"/api/deals/{did}/chain", headers=H(FD)).json()
            assert st["stale"] is True and st["needed"] is True
        finally:
            c.put("/api/parts/intragroup-rules", headers=H(GD), json={"rules": []})
