"""Юридический блок, этап 3: мастер закупочного пакета — варианты, договорный ЗИП, проверки, формирование трёх DOCX, версии, статусы, ZIP."""
from __future__ import annotations

import io
import os
import tempfile
import uuid
import zipfile
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

from app.core.workflow import DealStatus  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Deal, DealVersion  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, FD, LAW, LOG = "manager@example.com", "cd@example.com", "fd@example.com", "lawyer@example.com", "logist@example.com"


def _text(content: bytes) -> str:
    d = Document(io.BytesIO(content))
    return "\n".join(p.text for p in d.paragraphs) + "\n" + "\n".join(c.text for t in d.tables for r in t.rows for c in r.cells)


def test_purchase_package_flow():
    seed()
    with TestClient(app) as c:
        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        facs = c.get("/api/refs/factories", headers=H(MGR)).json()
        f1, f2 = facs[0], facs[1]
        d = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": f"юрпакет-{uuid.uuid4().hex[:4]}"}).json()
        # две позиции первого завода и одна второго — пакеты раздельные
        pay = [{"event": "factory_contract", "share_pct": 30}, {"event": "readiness", "share_pct": 70}]
        c.post(f"/api/deals/{d['id']}/positions", headers=H(MGR), json={"factory_id": f1["id"], "model": "X3000", "machine_type": "самосвал", "qty": 2, "unit_price": 1500000, "currency": "CNY", "basis": "FCA",
                                                                        "production_term": {"value": 90, "unit": "calendar_days", "start_event": "factory_prepayment"}, "warranty_months": 12, "factory_payments": pay,
                                                                        "options": [{"code": "12.1", "name": "Кондиционер"}, {"code": "12.2", "name": "Система пожаротушения"}]})
        c.post(f"/api/deals/{d['id']}/positions", headers=H(MGR), json={"factory_id": f1["id"], "model": "SHL-4000", "machine_type": "ПДМ", "qty": 1, "unit_price": 2200000, "currency": "CNY", "basis": "FCA", "factory_payments": pay})
        c.post(f"/api/deals/{d['id']}/positions", headers=H(MGR), json={"factory_id": f2["id"], "model": "ZL50", "qty": 1, "unit_price": 100000, "currency": "USD"})
        base = f"/api/deals/{d['id']}/legal"

        # до выигрыша формировать нельзя; варианты доступны
        opts = c.get(f"{base}/purchase/options", headers=H(MGR)).json()
        assert {s["id"] for s in opts["suppliers"]} == {f1["id"], f2["id"]} and opts["can_build"] is False and opts["can_choose_entity"] is False
        sup1 = next(s for s in opts["suppliers"] if s["id"] == f1["id"])
        assert sup1["positions"] == 2 and sup1["units"] == 3 and sup1["amount"] == 5200000.0
        assert c.get(f"{base}/purchase/options", headers=H(CD)).json()["can_choose_entity"] is True
        assert c.post(f"{base}/purchase/build", headers=H(MGR), json={"factory_id": f1["id"]}).status_code == 409

        with SessionLocal() as db:
            deal = db.get(Deal, d["id"])
            deal.status = DealStatus.CONTRACT
            ver = DealVersion(deal_id=deal.id, version_no=1, reason="approval", calc_input={}, calc_result={"variants": {}})
            db.add(ver)
            db.flush()
            deal.approved_version_id = ver.id
            db.commit()

        # предпросмотр: ошибки — нет реквизитов покупателя/поставщика, ЗИП не подтверждён
        pv = c.post(f"{base}/purchase/preview", headers=H(MGR), json={"factory_id": f1["id"]}).json()
        assert pv["context"]["language"] in ("ru-zh", "ru-en", "ru") and len(pv["context"]["items"]) == 2 and pv["context"]["totals"]["equipment_amount"] == 5200000.0
        assert not pv["checks"]["ok"] and any("ЗИП" in e for e in pv["checks"]["errors"])
        assert pv["context"]["sources"]["items"]
        # менеджер не может выбрать другую компанию группы
        other = next((e for e in opts["entities"] if not e["default"]), None)
        if other:
            assert c.post(f"{base}/purchase/preview", headers=H(MGR), json={"factory_id": f1["id"], "own_entity_id": other["id"]}).status_code == 403

        # заполняем карточку БЕРГ-ХАУС (ФД) и профиль поставщика (менеджер); банки
        own = next(e for e in opts["entities"] if e["default"])
        op = own["profiles"][0]
        c.put(f"/api/legal/profiles/{op['id']}", headers=H(FD), json={"names": {"ru": {"full": "ООО «БЕРГ-ХАУС»", "short": "БЕРГ-ХАУС"}, "en": {"full": "BERG-HAUS LLC", "short": ""}, "zh": {"full": "", "short": ""}},
                                                                      "tax_number": "7700000000", "kpp": "770101001", "reg_number": "1027700000000", "legal_address": "Москва, Пример, 1", "signatory_name": "Иванов И. И.", "signatory_position": "Генеральный директор", "signatory_basis": "Устава"})
        ob = c.post(f"/api/legal/profiles/{op['id']}/banks", headers=H(FD), json={"title": "Валютный CNY", "currency": "CNY", "bank_name": "Bank RU", "swift": "BANKRUMM", "account": "40702156000000000001"}).json()
        c.post(f"/api/legal/banks/{ob['id']}/verify", headers=H(FD))
        sp = c.get(f"/api/legal/profiles?owner_kind=factory&owner_id={f1['id']}", headers=H(MGR)).json()["items"][0]
        c.put(f"/api/legal/profiles/{sp['id']}", headers=H(MGR), json={"reg_number": "91370800MA3X", "legal_address": "Jining, Shandong", "signatory_name": "Li Wei", "signatory_position": "General Manager", "signatory_basis": "Articles"})
        sb = c.post(f"/api/legal/profiles/{sp['id']}/banks", headers=H(MGR), json={"title": "Main CNY", "currency": "CNY", "bank_name": "Bank of China", "swift": "BKCHCNBJ", "account": "123456789"}).json()

        # договорный ЗИП: предзаполнение пустое (у позиций нет ЗИП) → ручная строка → подтверждение
        zs = c.get(f"{base}/zip-set/{f1['id']}", headers=H(MGR)).json()
        assert zs["lines"] == [] and zs["confirmed_at"] is None
        assert c.put(f"{base}/zip-set/{f1['id']}", headers=H(LOG), json={"lines": []}).status_code == 403
        zs = c.put(f"{base}/zip-set/{f1['id']}", headers=H(MGR), json={"lines": [{"article": "HF-1001", "name": "Фильтр гидравлический", "qty": 4, "unit_price": 350, "currency": "CNY", "kind": "to", "interval_hours": 500, "selected": True, "included": True},
                                                                                {"article": "SKIP", "name": "Не в договор", "qty": 1, "selected": False}], "pricing_mode": "included"}).json()
        assert len(zs["lines"]) == 2 and len(zs["rows"]) == 1 and zs["rows"][0]["amount"] == 1400.0
        zs = c.post(f"{base}/zip-set/{f1['id']}/confirm", headers=H(MGR)).json()
        assert zs["confirmed_at"]

        pv = c.post(f"{base}/purchase/preview", headers=H(MGR), json={"factory_id": f1["id"], "own_bank_id": ob["id"], "cp_bank_id": sb["id"]}).json()
        assert pv["checks"]["ok"], pv["checks"]
        assert pv["context"]["buyer"]["bank"]["verified"] and not pv["context"]["seller"]["bank"]["verified"]
        assert any("поставщика не подтверждены" in w for w in pv["checks"]["warnings"])
        assert pv["context"]["totals"]["zip_amount"] == 1400.0 and pv["context"]["totals"]["amount"] == 5200000.0   # ЗИП включён в стоимость
        assert pv["context"]["contract"]["payment_terms"].startswith("30 %")
        assert pv["templates"]["purchase_contract"] is None                                                      # шаблон не загружен → встроенный

        # формирование: три DOCX, папка 08, пакет v1 черновик, только позиции первого завода
        r = c.post(f"{base}/purchase/build", headers=H(MGR), json={"factory_id": f1["id"], "own_bank_id": ob["id"], "cp_bank_id": sb["id"], "contract_number": "BH-77", "contract_date": "2026-11-20", "place": "г. Москва"})
        assert r.status_code == 201, r.text
        pkg = r.json()
        assert pkg["version_no"] == 1 and pkg["status"] == "draft" and [f["kind"] for f in pkg["files"]] == ["purchase_contract", "spec_equipment", "spec_zip"]
        contract = c.get(f"/api/legal/packages/{pkg['id']}/files/purchase_contract", headers=H(MGR))
        assert contract.status_code == 200
        txt = _text(contract.content)
        assert "BH-77" in txt and "БЕРГ-ХАУС" in txt and "5 200 000" in txt and "30 % — контракт с заводом" in txt and "ЧЕРНОВИК" in txt
        spec = _text(c.get(f"/api/legal/packages/{pkg['id']}/files/spec_equipment", headers=H(MGR)).content)
        assert "X3000" in spec and "SHL-4000" in spec and "ZL50" not in spec            # только выбранный поставщик
        zipdoc = _text(c.get(f"/api/legal/packages/{pkg['id']}/files/spec_zip", headers=H(MGR)).content)
        assert "HF-1001" in zipdoc and "SKIP" not in zipdoc                               # только подтверждённые строки
        docs = c.get(f"/api/deals/{d['id']}/documents", headers=H(MGR)).json()["documents"]
        assert sum(1 for x in docs if x["category"] == "contract_draft") >= 3           # файлы в папке «08 Документы клиента и юридические»
        z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/legal/packages/{pkg['id']}/zip", headers=H(MGR)).content))
        assert len(z.namelist()) == 3

        # смена справочника после формирования не меняет пакет (снимок); повторное формирование — новая версия, прежняя «заменена»
        c.put(f"/api/legal/banks/{sb['id']}", headers=H(MGR), json={"account": "999"})
        snap = c.get(f"/api/legal/packages/{pkg['id']}", headers=H(MGR)).json()["snapshot"]
        assert snap["seller"]["bank"]["account"] == "123456789"
        pkg2 = c.post(f"{base}/purchase/build", headers=H(LAW), json={"factory_id": f1["id"], "own_bank_id": ob["id"], "cp_bank_id": sb["id"]}).json()
        assert pkg2["version_no"] == 2
        lst = c.get(f"{base}/packages", headers=H(MGR)).json()["items"]
        assert next(p for p in lst if p["id"] == pkg["id"])["status"] == "superseded"

        # статусы: черновик → юристу → коммерческое → утверждён (КД) → подписан только после даты подписания с заводом
        assert c.post(f"/api/legal/packages/{pkg2['id']}/status", headers=H(MGR), json={"status": "approved"}).status_code == 409
        assert c.post(f"/api/legal/packages/{pkg2['id']}/status", headers=H(MGR), json={"status": "lawyer_review"}).json()["status"] == "lawyer_review"
        assert c.post(f"/api/legal/packages/{pkg2['id']}/status", headers=H(MGR), json={"status": "commercial_review"}).status_code == 403
        assert c.post(f"/api/legal/packages/{pkg2['id']}/status", headers=H(LAW), json={"status": "commercial_review"}).json()["status"] == "commercial_review"
        assert c.post(f"/api/legal/packages/{pkg2['id']}/status", headers=H(LAW), json={"status": "approved"}).status_code == 403
        assert c.post(f"/api/legal/packages/{pkg2['id']}/status", headers=H(CD), json={"status": "approved"}).json()["status"] == "approved"
        assert c.post(f"/api/legal/packages/{pkg2['id']}/status", headers=H(MGR), json={"status": "signed"}).status_code == 409
        with SessionLocal() as db:
            db.get(Deal, d["id"]).factory_contract_signed_at = date(2026, 11, 20)
            db.commit()
        assert c.post(f"/api/legal/packages/{pkg2['id']}/status", headers=H(MGR), json={"status": "signed"}).json()["status"] == "signed"
        # реестр пакетов
        reg = c.get("/api/legal/packages?kind=purchase", headers=H(CD)).json()["items"]
        assert any(p["id"] == pkg2["id"] and p["deal_number"] == d["number"] for p in reg)
        # второй поставщик — отдельный пакет (USD), без подтверждённого ЗИП — ошибка
        pv2 = c.post(f"{base}/purchase/preview", headers=H(MGR), json={"factory_id": f2["id"]}).json()
        assert len(pv2["context"]["items"]) == 1 and pv2["context"]["contract"]["currency"] == "USD" and not pv2["checks"]["ok"]
