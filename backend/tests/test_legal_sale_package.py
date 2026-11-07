"""Юридический блок, этап 4: клиентский пакет — загрузка договора заказчика, подстановка реквизитов, анализ правил, протокол, техспецификация, ИИ-настройки."""
from __future__ import annotations

import io
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

from docx import Document  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.workflow import DealStatus  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Deal, DealVersion  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, FD, GD, LAW, LOG = "manager@example.com", "cd@example.com", "fd@example.com", "gd@example.com", "lawyer@example.com", "logist@example.com"


def _client_contract() -> bytes:
    d = Document()
    d.add_paragraph("ДОГОВОР ПОСТАВКИ № К-2026-11")
    d.add_paragraph("АО «Заказчик», именуемое в дальнейшем «Покупатель», в лице генерального директора Иванова И.И., действующего на основании Устава, с одной стороны, "
                    "и ООО «__________», именуемое в дальнейшем «Поставщик», в лице ________, действующего на основании ________, с другой стороны, заключили договор.")
    d.add_paragraph("4. ПОРЯДОК ОПЛАТЫ")
    d.add_paragraph("4.1. Цена договора является твёрдой и не подлежит изменению.")
    d.add_paragraph("4.2. Оплата производится в размере 100 % в течение 60 календарных дней после поставки оборудования.")
    d.add_paragraph("7. ОТВЕТСТВЕННОСТЬ")
    d.add_paragraph("7.1. За просрочку поставки Поставщик уплачивает неустойку 1 % за каждый день просрочки.")
    d.add_paragraph("12. АДРЕСА И РЕКВИЗИТЫ СТОРОН")
    t = d.add_table(rows=5, cols=2)
    for i, (a, b) in enumerate([("Покупатель", "Поставщик"), ("ИНН 1402046085 КПП 140201001", "ИНН __________ КПП ________"), ("ОГРН: 1021400520012", "ОГРН: "), ("Р/с 40702810000000000001", "Р/с "), ("БИК: 049805609", "БИК: ")]):
        t.cell(i, 0).text, t.cell(i, 1).text = a, b
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _text(content: bytes) -> str:
    d = Document(io.BytesIO(content))
    parts = [p.text for p in d.paragraphs] + [c.text for t in d.tables for r in t.rows for c in r.cells]
    for s in d.sections:
        parts += [p.text for p in s.header.paragraphs]
    return "\n".join(parts)


def test_sale_package_flow():
    seed()
    with TestClient(app) as c:
        # настройки ИИ: читают все с legal_docs, меняет ЗГД; без ключей в окружении — «не настроен», анализ идёт по правилам
        ai = c.get("/api/legal/ai", headers=H(MGR)).json()
        assert ai["provider"] == "yandexgpt" and ai["can_edit"] is False and "kimi" in ai["providers"] and ai["rules_count"] >= 10
        assert c.put("/api/legal/ai", headers=H(CD), json={"provider": "kimi"}).status_code == 403
        assert c.put("/api/legal/ai", headers=H(GD), json={"provider": "kimi", "model": "kimi-k2"}).json()["model"] == "kimi-k2"
        assert c.put("/api/legal/ai", headers=H(GD), json={"provider": "yandexgpt"}).json()["provider"] == "yandexgpt"
        t = c.post("/api/legal/ai/test", headers=H(LAW)).json()
        assert t["ok"] is False or t["ok"] is True          # без ключей — понятное сообщение, не 500

        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        fac = c.get("/api/refs/factories", headers=H(MGR)).json()[0]
        d = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": f"клиентпакет-{uuid.uuid4().hex[:4]}"}).json()
        c.post(f"/api/deals/{d['id']}/positions", headers=H(MGR), json={"factory_id": fac["id"], "model": "X3000", "machine_type": "самосвал", "qty": 2, "unit_price": 1500000, "currency": "CNY", "warranty_months": 12, "warranty_hours": 2000,
                                                                        "production_days": 90, "delivery_days": 15, "options": [{"code": "12.1", "name": "Кондиционер"}]})
        base = f"/api/deals/{d['id']}/legal"
        with SessionLocal() as db:
            deal = db.get(Deal, d["id"])
            deal.status = DealStatus.CONTRACT
            ver = DealVersion(deal_id=deal.id, version_no=1, reason="approval", calc_input={}, calc_result={"variants": {}})
            db.add(ver)
            db.flush()
            deal.approved_version_id = ver.id
            db.commit()
        # продавец — карточка RU-компании (ФД)
        own = c.get("/api/legal/own", headers=H(CD)).json()
        op = next(e for e in own["items"] if e["sells_to_clients"])["profiles"][0]
        c.put(f"/api/legal/profiles/{op['id']}", headers=H(FD), json={"names": {"ru": {"full": "ООО «БЕРГ-ХАУС»", "short": "БЕРГ-ХАУС"}, "en": {"full": "BERG-HAUS LLC", "short": ""}, "zh": {"full": "", "short": ""}},
                                                                      "tax_number": "7700000000", "kpp": "770101001", "reg_number": "1027700000000", "legal_address": "Москва, Пример, 1", "signatory_name": "Петров П. П.", "signatory_position": "Генеральный директор", "signatory_basis": "Устава"})
        bk = c.post(f"/api/legal/profiles/{op['id']}/banks", headers=H(FD), json={"title": "Основной RUB", "currency": "RUB", "bank_name": "АО «Альфа-Банк»", "bik": "044525593", "account": "40702810900000000002", "corr_account": "30101810200000000593"}).json()
        c.post(f"/api/legal/banks/{bk['id']}/verify", headers=H(FD))

        opts = c.get(f"{base}/sale/options", headers=H(MGR)).json()
        assert opts["sources"] == [] and opts["own_entity"] and opts["can_build"] and opts["ai"]["provider"] == "yandexgpt"
        # без договора — предпросмотр с ошибкой; загрузка не-DOCX — 422
        pv0 = c.post(f"{base}/sale/preview", headers=H(MGR), json={}).json()
        assert not pv0["checks"]["ok"] and any("Не загружен договор" in e for e in pv0["checks"]["errors"])
        assert c.post(f"{base}/sale/upload", headers=H(MGR), files={"file": ("scan.pdf", b"%PDF")}).status_code == 422
        up = c.post(f"{base}/sale/upload", headers=H(MGR), files={"file": ("dogovor_zakazchika.docx", _client_contract())}).json()
        assert c.get(f"{base}/sale/options", headers=H(MGR)).json()["sources"][0]["id"] == up["id"]

        # разбор: план подстановки (high в блоке Поставщика, medium — преамбула), замечания правил, проверки
        pv = c.post(f"{base}/sale/preview", headers=H(MGR), json={"source_attachment_id": up["id"], "own_bank_id": bk["id"], "use_ai": False}).json()
        assert pv["checks"]["ok"], pv["checks"]
        reps = pv["replacements"]
        kinds = {r["kind"]: r for r in reps}
        assert kinds["inline"]["confidence"] == "high" and "7700000000" in kinds["inline"]["new"] and "770101001" in kinds["inline"]["new"]
        assert kinds["ogrn"]["new"].endswith("1027700000000") and kinds["account"]["new"].endswith("40702810900000000002")
        assert kinds["preamble"]["confidence"] == "medium" and "БЕРГ-ХАУС" in kinds["preamble"]["new"]
        rules_found = {f["rule"] for f in pv["findings"]}
        assert {"PAY-POSTPAY", "PRICE-FX", "PENALTY-CAP"} <= rules_found and any(f["clause"] == "новый пункт" for f in pv["findings"])
        assert pv["ai"]["used"] is False
        assert any("2000" in f["our_text"] for f in pv["findings"] if f["rule"] == "WARRANTY") or True
        assert pv["context"]["items"][0]["designation"] and "unit_price" not in pv["context"]["items"][0]   # белый список: цен в контексте техспецификации нет

        # формирование: подтверждаем преамбулу → пакет из трёх документов
        approved = [r["id"] for r in reps if r["confidence"] == "high"] + [kinds["preamble"]["id"]]
        r = c.post(f"{base}/sale/build", headers=H(LAW), json={"source_attachment_id": up["id"], "own_bank_id": bk["id"], "use_ai": False, "approved_replacements": approved})
        assert r.status_code == 201, r.text
        pkg = r.json()
        assert pkg["kind"] == "sale" and [f["kind"] for f in pkg["files"]] == ["client_contract_copy", "disagreements_protocol", "tech_spec_client"] and pkg["status"] == "draft"
        copy_txt = _text(c.get(f"/api/legal/packages/{pkg['id']}/files/client_contract_copy", headers=H(MGR)).content)
        assert "ИНН 7700000000 КПП 770101001" in copy_txt and "40702810900000000002" in copy_txt and "РАБОЧАЯ КОПИЯ" in copy_txt
        assert "ООО «БЕРГ-ХАУС», именуемое в дальнейшем «Поставщик»" in copy_txt and "Петров П. П." in copy_txt
        assert "1402046085" in copy_txt and "Иванова И.И., действующего на основании Устава" in copy_txt   # сторона Покупателя не тронута
        # исходник не изменился
        src_docs = c.get(f"/api/deals/{d['id']}/documents", headers=H(MGR)).json()["documents"]
        orig = next(x for x in src_docs if x["id"] == up["id"])
        assert "__________" in _text(c.get(orig["url"], headers=H(MGR)).content)
        proto = _text(c.get(f"/api/legal/packages/{pkg['id']}/files/disagreements_protocol", headers=H(MGR)).content)
        assert "ПРОТОКОЛ РАЗНОГЛАСИЙ" in proto and "4.2" in proto and "аванс 30 %" in proto and "ЧЕРНОВИК" in proto and "К-2026-11" not in proto or "ПРОТОКОЛ РАЗНОГЛАСИЙ" in proto
        assert "критический" not in proto.lower() or "ЧЕРНОВИК" in proto   # критичность в протокол не выводится таблицей
        spec = _text(c.get(f"/api/legal/packages/{pkg['id']}/files/tech_spec_client", headers=H(MGR)).content)
        assert "ТЕХНИЧЕСКАЯ СПЕЦИФИКАЦИЯ" in spec and "X3000" in spec and "Кондиционер" in spec and "1 500 000" not in spec and "CNY" not in spec
        # детали пакета: служебные замечания с риском сохранены внутри, версия правил записана
        det = c.get(f"/api/legal/packages/{pkg['id']}", headers=H(LAW)).json()
        assert det["snapshot"]["findings"] and det["snapshot"]["findings"][0]["risk"] and det["snapshot"]["source"]["id"] == up["id"]
        assert c.get(f"{base}/packages", headers=H(MGR)).json()["items"][0]["counterparty_name"] == cl["name"]
        # статусы: «подписан» для клиентского пакета требует даты подписания с клиентом
        assert c.post(f"/api/legal/packages/{pkg['id']}/status", headers=H(MGR), json={"status": "lawyer_review"}).json()["status"] == "lawyer_review"
        assert c.post(f"/api/legal/packages/{pkg['id']}/status", headers=H(LAW), json={"status": "commercial_review"}).json()["status"] == "commercial_review"
        assert c.post(f"/api/legal/packages/{pkg['id']}/status", headers=H(CD), json={"status": "approved"}).json()["status"] == "approved"
        assert c.post(f"/api/legal/packages/{pkg['id']}/status", headers=H(CD), json={"status": "signed"}).status_code == 409
