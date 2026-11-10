"""Зеркальные договоры и маржа группы (D-160, D-161): договор с компанией группы → пакет компании группы с конечным
поставщиком по его ответу; договор в реестре от имени компании группы; маржа группы на экране «Расчёт и утверждение»."""
from __future__ import annotations

import io
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

from docx import Document  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.workflow import DealStatus  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Contract, Deal, DealVersion, Factory, FxRate, LegalEntity, MarkupPolicy, Position, Variant  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, FD, LAW = "manager@example.com", "cd@example.com", "fd@example.com", "lawyer@example.com"
T = uuid.uuid4().hex[:6].upper()
BUILTIN = {"purchase_contract": "", "spec_equipment": "", "spec_zip": ""}   # встроенные генераторы (шаблоны других тестов не влияют)


def _text(content: bytes) -> str:
    d = Document(io.BytesIO(content))
    return "\n".join(p.text for p in d.paragraphs) + "\n" + "\n".join(c.text for t in d.tables for r in t.rows for c in r.cells)


def _prepare():
    with SessionLocal() as db:
        cn = db.scalar(select(LegalEntity).where(LegalEntity.code == "cn"))
        ai = Factory(name=f"Альянс-Импекс Д{T}", country="CN", supplier_status="group_company", legal_entity_id=cn.id, default_currency="CNY")
        x = Factory(name=f"Завод договора {T}", country="CN", default_currency="CNY", contacts=[{"name": "Li", "email": f"c{T}@x.cn", "roles": ["rfq"]}])
        db.add_all([ai, x])
        if not db.scalar(select(FxRate).where(FxRate.currency == "CNY")):
            db.add(FxRate(currency="CNY", rate=12.5, on=date.today(), source="test"))
        for p in db.scalars(select(MarkupPolicy).where(MarkupPolicy.status == "approved")).all():
            p.status = "superseded"
        last = db.scalars(select(MarkupPolicy).order_by(MarkupPolicy.version_no.desc())).first()
        db.add(MarkupPolicy(version_no=(last.version_no + 1) if last else 1, status="approved", reason="тест договоров", valid_from=date.today(),
                            rules={**(last.rules if last else {}), "gsho_default": {cn.id: 10.0}, "gsho_by_machine_type": [], "gsho_by_supplier": []}))
        db.commit()
        return cn.id, ai.id, x.id


def _profile(c, owner_kind, owner_id, who, data, bank):
    p = c.get(f"/api/legal/profiles?owner_kind={owner_kind}&owner_id={owner_id}", headers=H(who)).json()["items"][0]
    assert c.put(f"/api/legal/profiles/{p['id']}", headers=H(who), json=data).status_code == 200
    b = c.post(f"/api/legal/profiles/{p['id']}/banks", headers=H(who), json=bank).json()
    return p, b


def test_mirror_contract_and_group_margin():
    seed()
    cn_id, ai_id, x_id = _prepare()
    with TestClient(app) as c:
        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        d = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": f"зерк-договор-{T}"}).json()
        did = d["id"]
        with SessionLocal() as db:
            pos = Position(deal_id=did, factory_id=x_id, model="ZK-90", machine_type="Самосвал", qty=2, unit_price=1, currency="CNY")
            db.add(pos)
            db.flush()
            db.add(Variant(deal_id=did, name="Основное", position_ids=[pos.id], price_rub=5_000_000, is_default=True))
            db.commit()
            pos_id = pos.id
        r = c.post(f"/api/deals/{did}/positions/{pos_id}/tech-params/request", headers=H(MGR), json={"factory_ids": [ai_id], "via_group": {ai_id: [x_id]}})
        assert r.status_code == 200, r.text
        ux, lx = r.json()["sent"][0]["offer_id"], r.json()["sent"][0]["lower_offer_id"]
        pay = [{"event": "factory_contract", "share_pct": 40}, {"event": "readiness", "share_pct": 60}]
        ans = {"factory_id": x_id, "offer_id": lx, "model": "XG-90", "unit_price": 100000, "currency": "CNY", "basis": "EXW",
               "production_term": {"value": 75, "unit": "calendar_days"}, "factory_payments": pay}
        assert c.post(f"/api/deals/{did}/offers", headers=H(CD), json=ans).status_code == 201
        assert c.post(f"/api/deals/{did}/offers/select", headers=H(CD), json={"offer_ids": [ux], "copy_calcs": False}).status_code == 200
        with SessionLocal() as db:
            p = db.get(Position, pos_id)
            assert p.factory_id == ai_id and p.unit_price == 110000.0
            deal = db.get(Deal, did)
            deal.status = DealStatus.CONTRACT
            ver = DealVersion(deal_id=did, version_no=1, reason="approval", calc_input={}, calc_result={"variants": {}})
            db.add(ver)
            db.flush()
            deal.approved_version_id = ver.id
            db.commit()

        base = f"/api/deals/{did}/legal"
        opts = c.get(f"{base}/purchase/options", headers=H(MGR)).json()
        sups = {s["id"]: s for s in opts["suppliers"]}
        assert sups[ai_id]["group_company"] and sups[ai_id]["amount"] == 220000.0
        assert x_id not in sups                                              # менеджер договор компании группы с X не видит
        sups = {s["id"]: s for s in c.get(f"{base}/purchase/options", headers=H(LAW)).json()["suppliers"]}
        assert sups[x_id]["via_group"]["entity_id"] == cn_id and sups[x_id]["amount"] == 200000.0 and sups[x_id]["currency"] == "CNY"

        # реквизиты: БЕРГ-ХАУС и Альянс-Импекс как продавец; ЗИП Альянс-Импекс
        own = next(e for e in opts["entities"] if e["default"])
        op = own["profiles"][0]
        c.put(f"/api/legal/profiles/{op['id']}", headers=H(FD), json={"names": {"ru": {"full": "ООО «БЕРГ-ХАУС»", "short": "БЕРГ-ХАУС"}, "en": {"full": "BERG-HAUS LLC", "short": ""}, "zh": {"full": "", "short": ""}},
                                                                      "tax_number": "7700000000", "kpp": "770101001", "reg_number": "1027700000000", "legal_address": "Москва, Пример, 1",
                                                                      "signatory_name": "Иванов И. И.", "signatory_position": "Генеральный директор", "signatory_basis": "Устава"})
        ob = c.post(f"/api/legal/profiles/{op['id']}/banks", headers=H(FD), json={"title": "CNY", "currency": "CNY", "bank_name": "Bank RU", "swift": "BANKRUMM", "account": "40702156000000000001"}).json()
        seller = {"reg_number": "91370800AI", "legal_address": "Urumqi", "signatory_name": "Zhang", "signatory_position": "Director", "signatory_basis": "Articles"}
        _, ab = _profile(c, "factory", ai_id, MGR, seller, {"title": "CNY", "currency": "CNY", "bank_name": "ICBC", "swift": "ICBKCNBJ", "account": "111"})
        c.put(f"{base}/zip-set/{ai_id}", headers=H(MGR), json={"lines": [{"article": "F-1", "name": "Фильтр", "qty": 2, "unit_price": 10, "currency": "CNY", "selected": True, "included": True}], "pricing_mode": "included"})
        c.post(f"{base}/zip-set/{ai_id}/confirm", headers=H(MGR))

        # договор с компанией группы → пакет её договора с X сразу; не хватает реквизитов и ЗИП — сообщение, основной пакет есть
        r = c.post(f"{base}/purchase/build", headers=H(MGR), json={"factory_id": ai_id, "templates": BUILTIN, "own_bank_id": ob["id"], "cp_bank_id": ab["id"], "contract_number": f"BH-AI-{T}"})
        assert r.status_code == 201, r.text
        main = r.json()
        assert [m["supplier_id"] for m in main["mirror_packages"]] == [x_id] and main["mirror_packages"][0]["ok"] is False
        assert main["mirror_packages"][0]["errors"]

        # реквизиты Альянс-Импекс как покупателя, поставщика X и ЗИП X — пакет X формируется
        cp = c.get(f"/api/legal/profiles?owner_kind=own&owner_id={cn_id}", headers=H(FD)).json()["items"][0]
        c.put(f"/api/legal/profiles/{cp['id']}", headers=H(FD), json={"names": {"ru": {"full": "Альянс-Импекс Ко., Лтд.", "short": "Альянс-Импекс"}, "en": {"full": "Alliance-Impex Co., Ltd.", "short": ""}, "zh": {"full": "", "short": ""}},
                                                                      "tax_number": "91650100AI", "reg_number": "91650100AI", "legal_address": "Urumqi, Xinjiang",
                                                                      "signatory_name": "Zhang Wei", "signatory_position": "General Manager", "signatory_basis": "Articles"})
        cb = c.post(f"/api/legal/profiles/{cp['id']}/banks", headers=H(FD), json={"title": "CNY", "currency": "CNY", "bank_name": "ICBC", "swift": "ICBKCNBJ", "account": "222"}).json()
        _, xb = _profile(c, "factory", x_id, MGR, {**seller, "reg_number": "9137X", "signatory_name": "Li"}, {"title": "CNY", "currency": "CNY", "bank_name": "BOC", "swift": "BKCHCNBJ", "account": "333"})
        c.put(f"{base}/zip-set/{x_id}", headers=H(MGR), json={"lines": [{"article": "F-1", "name": "Фильтр", "qty": 2, "unit_price": 8, "currency": "CNY", "selected": True, "included": True}], "pricing_mode": "included"})
        c.post(f"{base}/zip-set/{x_id}/confirm", headers=H(MGR))
        pv = c.post(f"{base}/purchase/preview", headers=H(LAW), json={"factory_id": x_id, "templates": BUILTIN, "own_entity_id": cn_id, "own_bank_id": cb["id"], "cp_bank_id": xb["id"]}).json()
        assert pv["checks"]["ok"], pv["checks"]
        ctx = pv["context"]
        assert ctx["own_entity"]["id"] == cn_id and ctx["mirror"]["entity_id"] == cn_id
        assert ctx["items"][0]["unit_price"] == 100000 and ctx["items"][0]["model"] == "XG-90" and ctx["totals"]["equipment_amount"] == 200000.0
        assert ctx["contract"]["payment_terms"].startswith("40 %") and ctx["contract"]["number"] == ""   # номер договора сделки с заводом не подставляется
        r = c.post(f"{base}/purchase/build", headers=H(LAW), json={"factory_id": x_id, "templates": BUILTIN, "own_bank_id": cb["id"], "cp_bank_id": xb["id"], "contract_number": f"AI-X-{T}"})
        assert r.status_code == 201, r.text
        xp = r.json()
        txt = _text(c.get(f"/api/legal/packages/{xp['id']}/files/purchase_contract", headers=H(LAW)).content)
        assert "Альянс-Импекс" in txt and "200 000" in txt and f"AI-X-{T}" in txt and "БЕРГ-ХАУС" not in txt

        with SessionLocal() as db:
            ctrs = {k.cp_id: k for k in db.scalars(select(Contract).where(Contract.deal_id == did, Contract.doc_type == "contract")).all()}
            m, xc = ctrs[ai_id], ctrs[x_id]
            assert m.mirror_kind == "" and m.amount == 220000.0
            assert xc.mirror_kind == "via_group" and xc.own_entity_id == cn_id and xc.amount == 200000.0 and xc.currency == "CNY"
            assert [l["share_pct"] for l in xc.payment_terms] == [40, 60] and "через" in xc.subject
            # подписание договора компании группы с X не трогает реквизиты договора сделки с заводом
            from app.services.legal_contracts import apply_signed_to_deal
            xc.signed_on = date(2026, 11, 25)
            apply_signed_to_deal(db, xc)
            assert db.get(Deal, did).factory_contract_number in (None, "")
            db.rollback()

        # видимость: менеджер не видит договор и пакет компании группы с X (цена X), юрист и КД — видят
        assert c.post(f"{base}/purchase/preview", headers=H(MGR), json={"factory_id": x_id, "templates": BUILTIN}).status_code == 403
        assert xp["id"] not in {p["id"] for p in c.get(f"{base}/packages", headers=H(MGR)).json()["items"]}
        assert c.get(f"/api/legal/packages/{xp['id']}", headers=H(MGR)).status_code == 404
        assert xp["id"] in {p["id"] for p in c.get(f"{base}/packages", headers=H(LAW)).json()["items"]}
        mgr_ctr = {k["cp_id"] for k in c.get(f"/api/deals/{did}/contracts", headers=H(MGR)).json()["items"]}
        assert ai_id in mgr_ctr and x_id not in mgr_ctr
        assert x_id in {k["cp_id"] for k in c.get(f"/api/deals/{did}/contracts", headers=H(LAW)).json()["items"]}
        assert x_id in {k["cp_id"] for k in c.get(f"/api/legal/contracts?deal_id={did}", headers=H(CD)).json()["items"]}
        assert x_id not in {k["cp_id"] for k in c.get(f"/api/legal/contracts?deal_id={did}", headers=H(MGR)).json()["items"]}

        # повторное формирование договора с компанией группы — пакет X формируется сразу (новая версия)
        r = c.post(f"{base}/purchase/build", headers=H(MGR), json={"factory_id": ai_id, "templates": BUILTIN, "own_bank_id": ob["id"], "cp_bank_id": ab["id"], "contract_number": f"BH-AI-{T}"})
        mp = r.json()["mirror_packages"][0]
        assert mp["ok"] is True and mp["version_no"] == 2, mp

        # маржа группы на экране расчёта: только с правом цепочки
        with SessionLocal() as db:
            from app.services.mirror_offers import group_chain_summary
            g = group_chain_summary(db, db.get(Deal, did), None)
            row = g["positions"][0]
            assert row["supplier_price"] == 100000 and row["group_price"] == 110000.0 and row["qty"] == 2
            assert abs(row["group_margin_rub"] - 2 * row["group_margin_rub_unit"]) < 0.01 and row["group_margin_rub_unit"] > 0
        calc = c.get(f"/api/deals/{did}/calc", headers=H(CD))
        assert calc.status_code == 200, calc.text[:500]
        gc = calc.json()["group_chain"]
        assert gc["positions"][0]["position_id"] == pos_id
        assert gc["variants"]
        for v in gc["variants"].values():
            assert v["group_margin_rub"] == gc["positions"][0]["group_margin_rub"]
            if v["deal_profit_rub"] is not None:
                assert abs(v["group_profit_rub"] - v["deal_profit_rub"] - v["group_margin_rub"]) < 0.02
        from app.core.roles import Role, filter_payload
        assert "group_chain" not in filter_payload({"group_chain": g}, {Role.MANAGER})
        assert "group_chain" in filter_payload({"group_chain": g}, {Role.CD})
