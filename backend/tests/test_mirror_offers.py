"""Зеркальные запросы ГШО через компанию группы (D-150…D-155): запрос → запросы компании группы поставщикам → ответ →
предложение компании группы по маршруту и наценкам, сравнение, отбор, изменения после отбора, права."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

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
from app.models import (Deal, Factory, FactoryOffer, FactoryPriceHistory, FxRate, LegalEntity, MarkupPolicy, NotificationLog, Position,  # noqa: E402
                        SupplyRoute, User)
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, FD, GD, LAW, LOG = "manager@example.com", "cd@example.com", "fd@example.com", "evgeniy@dobrov.pro", "lawyer@example.com", "logist@example.com"
RUN = datetime.now(timezone.utc).strftime("%H%M%S%f")


def _setup():
    with SessionLocal() as db:
        cn = db.scalar(select(LegalEntity).where(LegalEntity.code == "cn"))
        cn.channels = {"mail_profile": "alliance", "mail_from": "rfq@alliance.example", "wecom_profile": "alliance"}
        ai = Factory(name=f"Альянс-Импекс {RUN}", country="CN", supplier_status="group_company", legal_entity_id=cn.id, default_currency="CNY")
        x = Factory(name=f"Завод X {RUN}", country="CN", default_currency="CNY", contacts=[{"name": "Li", "email": f"x{RUN}@x.cn", "roles": ["rfq"]}])
        y = Factory(name=f"Завод Y {RUN}", country="CN", default_currency="CNY", contacts=[{"name": "Wang", "email": f"y{RUN}@y.cn"}])
        db.add_all([ai, x, y])
        if not db.scalar(select(FxRate).where(FxRate.currency == "CNY")):
            from datetime import date
            db.add(FxRate(currency="CNY", rate=12.5, on=date.today(), source="test"))
        db.commit()
        return cn.id, ai.id, x.id, y.id


def _deal(c, machine_type="Самосвал"):
    cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
    d = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": f"зеркало-{RUN}"}).json()
    with SessionLocal() as db:
        fac = db.scalars(select(Factory).where(Factory.supplier_status != "group_company")).first()
        pos = Position(deal_id=d["id"], factory_id=fac.id, model="ZK-90", machine_type=machine_type, qty=2, unit_price=1, currency="CNY")
        db.add(pos)
        db.commit()
        return d["id"], pos.id


def _approve_policy(cn_id: str, gsho_default: float, by_type=None):
    with SessionLocal() as db:
        for p in db.scalars(select(MarkupPolicy).where(MarkupPolicy.status == "approved")).all():
            p.status = "superseded"
        p = db.scalars(select(MarkupPolicy).order_by(MarkupPolicy.version_no.desc())).first()
        db.add(MarkupPolicy(version_no=(p.version_no + 1) if p else 1, status="approved", reason="тест ГШО", valid_from=None,
                            rules={**(p.rules if p else {}), "gsho_default": {cn_id: gsho_default}, "gsho_by_machine_type": by_type or [], "gsho_by_supplier": []}))
        db.commit()


def _cny() -> float:
    with SessionLocal() as db:
        return db.scalars(select(FxRate).where(FxRate.currency == "CNY").order_by(FxRate.on.desc())).first().rate


def _offers(c, did, who):
    return c.get(f"/api/deals/{did}/offers", headers=H(who)).json()


def test_mirror_request_answer_select():
    seed()
    cn_id, ai_id, x_id, y_id = _setup()
    with SessionLocal() as db:   # нет действующей политики: предложение компании группы не рассчитывается
        for p in db.scalars(select(MarkupPolicy).where(MarkupPolicy.status.in_(("approved", "superseded")))).all():
            p.status = "draft"
        db.commit()
    with TestClient(app) as c:
        did, pos_id = _deal(c)
        url = f"/api/deals/{did}/positions/{pos_id}/tech-params/request"
        assert c.post(url, headers=H(MGR), json={"factory_ids": [ai_id], "via_group": {ai_id: [x_id]}}).status_code == 422   # менеджер компанию группы не выбирает (D-164)
        assert c.post(url, headers=H(GD), json={"factory_ids": [ai_id]}).status_code == 422              # не выбран конечный поставщик
        assert c.post(url, headers=H(GD), json={"factory_ids": [ai_id], "via_group": {ai_id: [ai_id]}}).status_code == 422   # компания группы сама себе
        r = c.post(url, headers=H(GD), json={"factory_ids": [ai_id], "via_group": {ai_id: [x_id, y_id]}})
        assert r.status_code == 200, r.text
        sent = r.json()["sent"]
        assert [s["end_supplier"] for s in sent] == [f"Завод X {RUN}", f"Завод Y {RUN}"] and all(s["sender"] == "Компания в Китае" or s["sender"] for s in sent)
        assert sent[0]["mail"].startswith("console") and sent[1]["mail"].startswith("console")      # консольная почта стенда, от имени компании группы
        assert sent[0]["route"] == "Поставщик КНР → компания в Китае → РФ"
        again = c.post(url, headers=H(GD), json={"factory_ids": [ai_id], "via_group": {ai_id: [x_id]}}).json()["sent"]
        assert again[0]["offer_id"] == sent[0]["offer_id"]                                                 # повторный запрос — та же пара
        ux, lx, uy, ly = sent[0]["offer_id"], sent[0]["lower_offer_id"], sent[1]["offer_id"], sent[1]["lower_offer_id"]

        # сравнение — только предложения компании группы; поставщик назван, цены у менеджера нет
        cmp_m = c.get(f"/api/deals/{did}/offers/compare?position_id={pos_id}", headers=H(MGR)).json()
        assert {o["offer_id"] for o in cmp_m["offers"]} == {ux, uy}
        cx = next(o for o in cmp_m["offers"] if o["offer_id"] == ux)
        assert cx["mirror"]["end_supplier"] == f"Завод X {RUN}" and "supplier_price" not in cx["mirror"]

        # ответ X: цену конечного поставщика менеджер не вносит; предложение компании группы руками не правится
        assert c.post(f"/api/deals/{did}/offers", headers=H(MGR), json={"factory_id": x_id, "offer_id": lx, "unit_price": 100000, "currency": "CNY"}).status_code == 403
        assert c.post(f"/api/deals/{did}/offers", headers=H(CD), json={"factory_id": ai_id, "offer_id": ux, "unit_price": 1}).status_code == 409
        ans = {"factory_id": x_id, "offer_id": lx, "model": "XG-90", "unit_price": 100000, "currency": "CNY", "basis": "EXW",
               "production_term": {"value": 60, "unit": "calendar_days"}, "params": [{"no": 1, "name_ru": "Грузоподъёмность", "value_ru": "90 т"}]}
        r = c.post(f"/api/deals/{did}/offers", headers=H(CD), json=ans)
        assert r.status_code == 201, r.text
        o = {x["id"]: x for x in _offers(c, did, CD)}
        assert o[ux]["mirror"]["state"] == "no_policy" and o[ux].get("unit_price") is None                  # политика без ставок ГШО не утверждена

        _approve_policy(cn_id, 7.0, [{"entity_id": cn_id, "machine_type": "Самосвал", "pct": 9.0}])
        c.post(f"/api/deals/{did}/offers", headers=H(CD), json=ans)
        o = {x["id"]: x for x in _offers(c, did, CD)}
        assert o[ux]["unit_price"] == 109000.0 and o[ux]["currency"] == "CNY" and o[ux]["basis"] == "FCA"     # 100 000 × (1 + 9 % для самосвала)
        assert o[ux]["mirror"]["state"] == "ok" and o[ux]["mirror"]["supplier_price"] == 100000 and abs(o[ux]["mirror"]["group_margin_rub"] - 9000 * _cny()) < 0.01
        assert o[ux]["model"] == "XG-90" and o[ux]["production_term"]["value"] == 60 and o[ux]["params"][0]["value_ru"] == "90 т" and o[ux]["revision"] == 1
        m = {x["id"]: x for x in _offers(c, did, MGR)}
        assert "unit_price" not in m[lx] and "chain" not in m[ux] and "supplier_price" not in m[ux]["mirror"]  # менеджер: без цены поставщика и цепочки
        lg = c.get(f"/api/deals/{did}/offers/{ux}/chain", headers=H(LOG)); 
        assert lg.status_code in (200, 404)                                                                 # логист: цепочка (если сделка ему видна)
        if lg.status_code == 200:
            assert lg.json()["supplier_price"] == 100000
        assert c.get(f"/api/deals/{did}/offers/{ux}/chain", headers=H(MGR)).status_code == 403
        assert c.get(f"/api/deals/{did}/offers/{ux}/chain", headers=H(LAW)).status_code in (403, 404)
        ch = c.get(f"/api/deals/{did}/offers/{ux}/chain", headers=H(FD)).json()
        assert [s["seller"] for s in ch["steps"]] == ["Поставщик", "Компания в Китае"] and ch["companies"][0]["margin"] == 9000.0

        # ответ Y и трансфертная цена ФД
        c.post(f"/api/deals/{did}/offers", headers=H(LOG if lg.status_code == 200 else CD), json={"factory_id": y_id, "offer_id": ly, "unit_price": 200000, "currency": "CNY"})
        assert {x["id"]: x for x in _offers(c, did, CD)}[uy]["unit_price"] == 218000.0
        tp = f"/api/deals/{did}/offers/{uy}/transfer-price"
        assert c.post(tp, headers=H(CD), json={"price": 210000, "note": "договорённость"}).status_code == 403
        assert c.post(tp, headers=H(FD), json={"price": 210000}).status_code == 422
        assert c.post(tp, headers=H(FD), json={"price": 210000, "note": "рамочная договорённость с заводом"}).json()["unit_price"] == 210000.0
        assert c.post(tp, headers=H(FD), json={"price": None}).json()["unit_price"] == 218000.0

        # отбор: позиция берёт цену компании группы; запрос X отобран, его цена — в истории поставщика; Y отклонён
        assert c.post(f"/api/deals/{did}/offers/select", headers=H(CD), json={"offer_ids": [lx]}).status_code == 422
        r = c.post(f"/api/deals/{did}/offers/select", headers=H(CD), json={"offer_ids": [ux], "copy_calcs": False})
        assert r.status_code == 200, r.text
        with SessionLocal() as db:
            pos = db.get(Position, pos_id)
            assert pos.factory_id == ai_id and pos.unit_price == 109000.0 and pos.offer_id == ux and pos.model == "XG-90"
            st = {f.id: f.status for f in db.scalars(select(FactoryOffer).where(FactoryOffer.deal_id == did)).all()}
            assert st[ux] == "selected" and st[lx] == "selected" and st[uy] == "rejected" and st[ly] == "rejected"
            assert db.scalar(select(FactoryPriceHistory).where(FactoryPriceHistory.factory_id == x_id, FactoryPriceHistory.source == "via_group")).unit_price == 100000
            assert db.scalar(select(FactoryPriceHistory).where(FactoryPriceHistory.factory_id == ai_id, FactoryPriceHistory.deal_id == did)).unit_price == 109000.0

        # ответ поставщика изменился после отбора: цена в сделке не меняется, отметка и уведомление КД
        c.post(f"/api/deals/{did}/offers", headers=H(CD), json={**ans, "unit_price": 104000})
        o = {x["id"]: x for x in _offers(c, did, CD)}
        assert o[ux]["unit_price"] == 109000.0 and o[ux]["mirror"]["state"] == "changed_after_select"
        assert o[ux]["mirror"]["pending"]["pending_price"] == 113360.0
        with SessionLocal() as db:
            pos = db.get(Position, pos_id)
            assert pos.unit_price == 109000.0
            cd = db.scalar(select(User).where(User.email == CD))
            assert db.scalar(select(NotificationLog).where(NotificationLog.user_id == cd.id, NotificationLog.event == "mirror_changed"))
        # правка таблицы позиции не создаёт обычного «ответа» компании группы
        with SessionLocal() as db:
            n = len(db.scalars(select(FactoryOffer).where(FactoryOffer.deal_id == did)).all())
        put = c.put(f"/api/deals/{did}/positions/{pos_id}/tech-params", headers=H(MGR), json=[{"no": 1, "name_ru": "Грузоподъёмность", "value_ru": "91 т"}])
        assert put.status_code == 200, put.text
        with SessionLocal() as db:
            assert len(db.scalars(select(FactoryOffer).where(FactoryOffer.deal_id == did)).all()) == n


def test_no_route_and_entity_channels():
    seed()
    with TestClient(app) as c:
        with SessionLocal() as db:
            kg = db.scalar(select(LegalEntity).where(LegalEntity.code == "kg"))
            g = Factory(name=f"Группа без маршрута {RUN}", country="KG", supplier_status="group_company", legal_entity_id=kg.id)
            x = Factory(name=f"Завод Z {RUN}", country="DE", default_currency="EUR")
            db.add_all([g, x])
            # маршрут «поставщик → Киргизия → РФ» есть только для поставщиков Германии/Киргизии; уберём их, чтобы проверить ошибку
            for r in db.scalars(select(SupplyRoute)).all():
                if r.steps and r.steps[0].buyer_entity_id == kg.id:
                    r.is_active = False
            db.commit()
            gid, xid = g.id, x.id
        did, pos_id = _deal(c)
        r = c.post(f"/api/deals/{did}/positions/{pos_id}/tech-params/request", headers=H(GD), json={"factory_ids": [gid], "via_group": {gid: [xid]}})
        assert r.status_code == 409 and "Нет маршрута" in r.json()["detail"]
        with SessionLocal() as db:
            for r_ in db.scalars(select(SupplyRoute)).all():
                r_.is_active = True
            db.commit()
        # каналы компании: только профили, без секретов
        ent = next(e for e in c.get("/api/parts/entities", headers=H(GD)).json() if e["code"] == "kg") if c.get("/api/parts/entities", headers=H(GD)).status_code == 200 else None
        if ent:
            body = {k: ent[k] for k in ("code", "name", "country", "currency", "warehouse", "sells_to_clients", "is_active", "notes")}
            assert c.put(f"/api/parts/entities/{ent['id']}", headers=H(GD), json={**body, "channels": {"smtp_password": "x"}}).status_code == 422
            ok = c.put(f"/api/parts/entities/{ent['id']}", headers=H(GD), json={**body, "channels": {"mail_profile": "kg", "mail_from": "kg@example.com"}})
            assert ok.status_code == 200 and ok.json()["channels"]["mail_profile"] == "kg"


def test_chain_permission_matrix():
    from app.core.roles import Role, effective_perms
    for r in (Role.CD, Role.FD, Role.GD, Role.LOGISTICIAN):
        assert effective_perms([r])["intragroup_chain"] >= 1, r      # видят цепочку целиком (D-152)
    for r in (Role.MANAGER, Role.LAWYER):
        assert effective_perms([r])["intragroup_chain"] == 0, r      # только предложение компании группы
