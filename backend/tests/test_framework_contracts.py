"""Рамочные договоры (D-168…D-170): разовый/рамочный в карточке (и после подписания), сделка оформляется спецификацией к рамочному
договору вместо нового договора; лимит рамочного договора по спецификациям."""
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
from app.models import Contract, Deal, DealVersion, Factory, Position  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, FD, GD, LAW = "manager@example.com", "cd@example.com", "fd@example.com", "evgeniy@dobrov.pro", "lawyer@example.com"
T = uuid.uuid4().hex[:6].upper()
BUILTIN = {"purchase_contract": "", "spec_equipment": "", "spec_zip": ""}


def _text(content: bytes) -> str:
    d = Document(io.BytesIO(content))
    return "\n".join(p.text for p in d.paragraphs) + "\n" + "\n".join(c.text for t in d.tables for r in t.rows for c in r.cells)


def _deal(c, fid: str, price: float) -> str:
    cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
    did = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": f"рамка-{T}-{price}"}).json()["id"]
    with SessionLocal() as db:
        db.add(Position(deal_id=did, factory_id=fid, model="FW-1", machine_type="Самосвал", qty=1, unit_price=price, currency="CNY",
                        factory_payments=[{"event": "factory_contract", "share_pct": 30}, {"event": "readiness", "share_pct": 70}]))
        deal = db.get(Deal, did)
        deal.status = DealStatus.CONTRACT
        ver = DealVersion(deal_id=did, version_no=1, reason="approval", calc_input={}, calc_result={"variants": {}})
        db.add(ver)
        db.flush()
        deal.approved_version_id = ver.id
        db.commit()
    return did


def test_framework_spec_instead_of_new_contract():
    seed()
    with TestClient(app) as c:
        with SessionLocal() as db:
            f = Factory(name=f"Рамочный завод {T}", country="CN", default_currency="CNY")
            other = Factory(name=f"Другой завод {T}", country="CN", default_currency="CNY")
            db.add_all([f, other])
            db.commit()
            fid, oid = f.id, other.id
        opts0 = c.get("/api/legal/contracts/catalog", headers=H(CD)).json()
        assert "specification" in opts0["doc_types"]
        # рамочный договор с поставщиком: лимит 1 000 000 CNY; разовый → рамочный в карточке
        k = c.post("/api/legal/contracts", headers=H(CD), json={"kind": "supplier", "own_entity_id": None, "cp_kind": "factory", "cp_id": fid,
                                                                "subject": "рамочный договор поставки", "amount": 1_000_000, "currency": "CNY"}).json()
        assert k["is_framework"] is False
        k = c.put(f"/api/legal/contracts/{k['id']}", headers=H(CD), json={"is_framework": True}).json()
        assert k["is_framework"] is True and k["framework"]["specs"] == 0 and k["framework"]["remaining"] == 1_000_000
        with SessionLocal() as db:        # подписан — статус «рамочный» меняет и после подписания (ЗГД, юрист)
            x = db.get(Contract, k["id"])
            x.status, x.signed_on, x.cp_number = "active", date(2026, 1, 15), f"RF-{T}"
            if not x.own_entity_id:
                from app.models import LegalEntity
                x.own_entity_id = db.scalar(select(LegalEntity.id).where(LegalEntity.sells_to_clients.is_(True)))
            db.commit()
        assert c.put(f"/api/legal/contracts/{k['id']}", headers=H(GD), json={"is_framework": False}).json()["is_framework"] is False
        assert c.put(f"/api/legal/contracts/{k['id']}", headers=H(GD), json={"is_framework": True}).json()["is_framework"] is True

        did = _deal(c, fid, 400_000)
        base = f"/api/deals/{did}/legal"
        opts = c.get(f"{base}/purchase/options", headers=H(MGR)).json()
        fws = opts["supplier_profiles"][fid]["frameworks"]
        assert [x["id"] for x in fws] == [k["id"]] and fws[0]["remaining"] == 1_000_000
        # реквизиты и ЗИП
        own = next(e for e in opts["entities"] if e["default"])
        op = own["profiles"][0]
        c.put(f"/api/legal/profiles/{op['id']}", headers=H(FD), json={"names": {"ru": {"full": "ООО «БЕРГ-ХАУС»", "short": "БЕРГ-ХАУС"}, "en": {"full": "BERG-HAUS LLC", "short": ""}, "zh": {"full": "", "short": ""}},
                                                                      "tax_number": "7700000000", "kpp": "770101001", "reg_number": "1027700000000", "legal_address": "Москва",
                                                                      "signatory_name": "Иванов И. И.", "signatory_position": "Генеральный директор", "signatory_basis": "Устава"})
        ob = c.post(f"/api/legal/profiles/{op['id']}/banks", headers=H(FD), json={"title": "CNY", "currency": "CNY", "bank_name": "Bank RU", "swift": "BANKRUMM", "account": "40702156000000000001"}).json()
        sp = c.get(f"/api/legal/profiles?owner_kind=factory&owner_id={fid}", headers=H(MGR)).json()["items"][0]
        c.put(f"/api/legal/profiles/{sp['id']}", headers=H(MGR), json={"reg_number": "9137", "legal_address": "Jining", "signatory_name": "Li", "signatory_position": "Director", "signatory_basis": "Articles"})
        sb = c.post(f"/api/legal/profiles/{sp['id']}/banks", headers=H(MGR), json={"title": "CNY", "currency": "CNY", "bank_name": "BOC", "swift": "BKCHCNBJ", "account": "333"}).json()
        c.put(f"{base}/zip-set/{fid}", headers=H(MGR), json={"lines": [], "pricing_mode": "included"})
        c.post(f"{base}/zip-set/{fid}/confirm", headers=H(MGR))
        choice = {"factory_id": fid, "own_bank_id": ob["id"], "cp_bank_id": sb["id"], "templates": BUILTIN}

        # чужой рамочный договор — нельзя
        with SessionLocal() as db:
            alien = Contract(doc_type="contract", kind="supplier", direction="expense", number=f"П-X-{T}", status="draft", cp_kind="factory", cp_id=oid, is_framework=True)
            db.add(alien)
            db.commit()
            alien_id = alien.id
        assert c.post(f"{base}/purchase/preview", headers=H(MGR), json={**choice, "framework_contract_id": alien_id}).status_code == 422

        pv = c.post(f"{base}/purchase/preview", headers=H(MGR), json={**choice, "framework_contract_id": k["id"]}).json()
        assert pv["checks"]["ok"], pv["checks"]
        assert pv["context"]["framework"]["number"] == f"RF-{T}" and pv["context"]["contract"]["spec_number"] == f"{k['number']}/СП-1"
        r = c.post(f"{base}/purchase/build", headers=H(MGR), json={**choice, "framework_contract_id": k["id"]})
        assert r.status_code == 201, r.text
        pkg = r.json()
        assert [f["kind"] for f in pkg["files"]] == ["spec_equipment", "spec_zip"]                 # без нового договора
        txt = _text(c.get(f"/api/legal/packages/{pkg['id']}/files/spec_equipment", headers=H(MGR)).content)
        assert f"Спецификация № {k['number']}/СП-1 к договору № RF-{T} от 15.01.2026" in txt and "400 000" in txt
        with SessionLocal() as db:
            own_contracts = db.scalars(select(Contract).where(Contract.deal_id == did, Contract.doc_type == "contract")).all()
            spec = db.scalar(select(Contract).where(Contract.parent_id == k["id"], Contract.deal_id == did))
            assert own_contracts == [] and spec.doc_type == "specification" and spec.number == f"{k['number']}/СП-1"
            assert spec.amount == 400_000 and spec.currency == "CNY" and [l["share_pct"] for l in spec.payment_terms] == [30, 70]
            assert did in db.get(Contract, k["id"]).deal_ids
        # повторное формирование — та же спецификация; вторая сделка — следующая спецификация; лимит
        assert c.post(f"{base}/purchase/build", headers=H(MGR), json={**choice, "framework_contract_id": k["id"]}).status_code == 201
        did2 = _deal(c, fid, 700_000)
        b2 = f"/api/deals/{did2}/legal"
        c.put(f"{b2}/zip-set/{fid}", headers=H(MGR), json={"lines": [], "pricing_mode": "included"})
        c.post(f"{b2}/zip-set/{fid}/confirm", headers=H(MGR))
        assert c.post(f"{b2}/purchase/build", headers=H(MGR), json={**choice, "framework_contract_id": k["id"]}).status_code == 201
        fw = c.get(f"/api/legal/contracts/{k['id']}", headers=H(CD)).json()
        assert fw["framework"]["specs"] == 2 and fw["framework"]["by_currency"] == {"CNY": 1_100_000} and fw["framework"]["exceeded"] is True
        assert {x["number"] for x in fw["children"]} == {f"{k['number']}/СП-1", f"{k['number']}/СП-2"}
        # без выбора рамочного — по-прежнему новый договор на сделку
        did3 = _deal(c, fid, 10_000)
        b3 = f"/api/deals/{did3}/legal"
        c.put(f"{b3}/zip-set/{fid}", headers=H(MGR), json={"lines": [], "pricing_mode": "included"})
        c.post(f"{b3}/zip-set/{fid}/confirm", headers=H(MGR))
        p3 = c.post(f"{b3}/purchase/build", headers=H(MGR), json=choice).json()
        assert [f["kind"] for f in p3["files"]] == ["purchase_contract", "spec_equipment", "spec_zip"]
        with SessionLocal() as db:
            assert db.scalar(select(Contract).where(Contract.deal_id == did3, Contract.doc_type == "contract")) is not None


def test_client_frameworks_listed_for_sale():
    seed()
    with TestClient(app) as c:
        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        k = c.post("/api/legal/contracts", headers=H(CD), json={"kind": "client_supply", "cp_kind": "client", "cp_id": cl["id"], "subject": "рамочный с клиентом",
                                                                "amount": 50_000_000, "currency": "RUB"}).json()
        c.put(f"/api/legal/contracts/{k['id']}", headers=H(CD), json={"is_framework": True})
        did = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": f"рамка-клиент-{T}"}).json()["id"]
        opts = c.get(f"/api/deals/{did}/legal/sale/options", headers=H(MGR)).json()
        assert k["id"] in {x["id"] for x in opts["frameworks"]}
