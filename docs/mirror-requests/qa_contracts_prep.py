"""Стенд для браузерной проверки PR 3: сделка на стадии контракта, позиция через Альянс-Импекс, реквизиты и ЗИП заполнены."""
import json, sys
sys.path.insert(0, ".")
from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.core.workflow import DealStatus
from app.db import SessionLocal
from app.main import app
from app.models import Deal, DealVersion, Factory, LegalEntity, MarkupPolicy, Position, Variant
from scripts.seed import run as seed
H = lambda e: {"X-Dev-User": e}
MGR, CD, FD = "manager@example.com", "cd@example.com", "fd@example.com"
seed()
with SessionLocal() as db:
    cn = db.scalar(select(LegalEntity).where(LegalEntity.code == "cn")); cn.name = "Альянс-Импекс"
    ai = Factory(name="Альянс-Импекс", country="CN", supplier_status="group_company", legal_entity_id=cn.id, default_currency="CNY")
    x = Factory(name="Shandong Lingong Heavy", country="CN", default_currency="CNY", contacts=[{"name": "Li", "email": "sales@lingong.example", "roles": ["rfq"]}])
    db.add_all([ai, x])
    p = db.scalars(select(MarkupPolicy).order_by(MarkupPolicy.version_no.desc())).first(); p.status = "approved"
    p.rules = {**p.rules, "gsho_default": {cn.id: 8.0}, "gsho_by_machine_type": [], "gsho_by_supplier": []}
    db.commit(); cn_id, ai_id, x_id = cn.id, ai.id, x.id
def prof(c, kind, oid, who, data, bank):
    p = c.get(f"/api/legal/profiles?owner_kind={kind}&owner_id={oid}", headers=H(who)).json()["items"][0]
    c.put(f"/api/legal/profiles/{p['id']}", headers=H(who), json=data)
    c.post(f"/api/legal/profiles/{p['id']}/banks", headers=H(who), json=bank)
with TestClient(app) as c:
    cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
    did = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "карьер «Восточный»"}).json()["id"]
    with SessionLocal() as db:
        pos = Position(deal_id=did, factory_id=x_id, model="ZK-90", machine_type="Самосвал", qty=2, unit_price=1, currency="CNY"); db.add(pos); db.flush()
        db.add(Variant(deal_id=did, name="Основное", position_ids=[pos.id], price_rub=36_000_000, is_default=True)); db.commit(); pos_id = pos.id
    s = c.post(f"/api/deals/{did}/positions/{pos_id}/tech-params/request", headers=H(MGR), json={"factory_ids": [ai_id], "via_group": {ai_id: [x_id]}}).json()["sent"][0]
    c.post(f"/api/deals/{did}/offers", headers=H(CD), json={"factory_id": x_id, "offer_id": s["lower_offer_id"], "model": "XG-90", "unit_price": 1_200_000, "currency": "CNY", "basis": "FCA",
                                                           "production_term": {"value": 75, "unit": "calendar_days"}, "factory_payments": [{"event": "factory_contract", "share_pct": 30}, {"event": "readiness", "share_pct": 70}]})
    c.post(f"/api/deals/{did}/offers/select", headers=H(CD), json={"offer_ids": [s["offer_id"]], "copy_calcs": False})
    with SessionLocal() as db:
        d = db.get(Deal, did); d.status = DealStatus.CONTRACT
        v = DealVersion(deal_id=did, version_no=1, reason="approval", calc_input={}, calc_result={"variants": {}}); db.add(v); db.flush(); d.approved_version_id = v.id; db.commit()
    base = f"/api/deals/{did}/legal"
    opts = c.get(f"{base}/purchase/options", headers=H(MGR)).json()
    own = next(e for e in opts["entities"] if e["default"])
    names = lambda ru, en: {"ru": {"full": ru, "short": ru}, "en": {"full": en, "short": ""}, "zh": {"full": "", "short": ""}}
    prof(c, "own", own["id"], FD, {"names": names("ООО «БЕРГ-ХАУС»", "BERG-HAUS LLC"), "tax_number": "7700000000", "kpp": "770101001", "reg_number": "1027700000000", "legal_address": "Москва",
                                  "signatory_name": "Иванов И. И.", "signatory_position": "Генеральный директор", "signatory_basis": "Устава"}, {"title": "CNY", "currency": "CNY", "bank_name": "Bank RU", "swift": "BANKRUMM", "account": "40702156000000000001"})
    prof(c, "own", cn_id, FD, {"names": names("Альянс-Импекс Ко., Лтд.", "Alliance-Impex Co., Ltd."), "tax_number": "91650100AI", "reg_number": "91650100AI", "legal_address": "Urumqi",
                              "signatory_name": "Zhang Wei", "signatory_position": "General Manager", "signatory_basis": "Articles"}, {"title": "CNY", "currency": "CNY", "bank_name": "ICBC", "swift": "ICBKCNBJ", "account": "222"})
    seller = {"reg_number": "9137", "legal_address": "Jining", "signatory_name": "Li", "signatory_position": "Director", "signatory_basis": "Articles"}
    for fid in (ai_id, x_id):
        prof(c, "factory", fid, MGR, seller, {"title": "CNY", "currency": "CNY", "bank_name": "BOC", "swift": "BKCHCNBJ", "account": "333"})
        c.put(f"{base}/zip-set/{fid}", headers=H(MGR), json={"lines": [{"article": "F-1", "name": "Фильтр", "qty": 2, "unit_price": 10, "currency": "CNY", "selected": True, "included": True}], "pricing_mode": "included"})
        c.post(f"{base}/zip-set/{fid}/confirm", headers=H(MGR))
json.dump({"deal_id": did}, open("prep_contracts.json", "w")); print(did)
