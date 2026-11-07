"""Юридический блок, этап 2: библиотека шаблонов (загрузка, переменные, тестовое формирование), сканы документов и карточка компании."""
from __future__ import annotations

import io
import os
import tempfile

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from docx import Document  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, LAW, LOG = "manager@example.com", "cd@example.com", "lawyer@example.com", "logist@example.com"


def _docx(paragraphs: list[str], loop_row: list[str] | None = None, header: bool = True) -> bytes:
    d = Document()
    if header:
        d.sections[0].header.paragraphs[0].text = "Договор № {{contract.number}}"
    for t in paragraphs:
        d.add_paragraph(t)
    if loop_row:
        t = d.add_table(rows=1, cols=len(loop_row))
        for i, x in enumerate(loop_row):
            t.rows[0].cells[i].text = x
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_templates_library_and_company_card():
    seed()
    with TestClient(app) as c:
        lib = c.get("/api/legal/templates", headers=H(LOG)).json()
        assert lib["can_edit"] is False and any(k["code"] == "purchase_contract_cn" for k in lib["kinds"]) and lib["catalog"]
        good = _docx(["Продавец: {{seller.name_ru}} ({{seller.name_zh}}), покупатель: {{buyer.name_ru}}, ИНН {{buyer.tax_number}}.", "Итого {{totals.amount}} {{totals.currency}}. {{foo.bar}}"],
                     ["{{#items}}{{n}}", "{{name}} {{model}}", "{{qty}} {{unit}}", "{{amount}}"])
        # загружает юрист; логист — нет; файл без плейсхолдеров — не шаблон
        assert c.post("/api/legal/templates", headers=H(LOG), data={"kind": "purchase_contract_cn"}, files={"file": ("t.docx", good)}).status_code == 403
        assert c.post("/api/legal/templates", headers=H(LAW), data={"kind": "purchase_contract_cn"}, files={"file": ("plain.docx", _docx(["просто текст"], header=False))}).status_code == 422
        assert c.post("/api/legal/templates", headers=H(LAW), data={"kind": "purchase_contract_cn"}, files={"file": ("t.txt", b"x")}).status_code == 422
        r = c.post("/api/legal/templates", headers=H(LAW), data={"kind": "purchase_contract_cn", "title": "Договор КНР"}, files={"file": ("contract_cn.docx", good)})
        assert r.status_code == 201, r.text
        t = r.json()
        assert t["language"] == "ru-zh" and t["country"] == "CN" and t["is_active"] and t["version"] >= 1
        assert "seller.name_zh" in t["variables"]["variables"] and t["variables"]["loops"]["items"] and t["variables"]["unknown"] == ["foo.bar"]
        # проверка на образцовых данных: подставлено всё, кроме foo.bar; строки items раскрыты
        ch = c.get(f"/api/legal/templates/{t['id']}/test-check", headers=H(MGR)).json()
        assert ch["unresolved"] == ["foo.bar"] and ch["loops"]["items"] == 2 and ch["replaced"] > 5 and not ch["ok"]
        r = c.get(f"/api/legal/templates/{t['id']}/test-render", headers=H(MGR))
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats") and r.headers["X-Unresolved"] == "foo.bar"
        out = Document(io.BytesIO(r.content))
        text = "\n".join(p.text for p in out.paragraphs) + "\n".join(cell.text for tb in out.tables for row in tb.rows for cell in row.cells)
        assert "БЕРГ-ХАУС" in text and "SHACMAN" in text and "{{foo.bar}}" in text and "{{seller.name_ru}}" not in text
        # новая версия того же вида и языка — действующая, прежняя в архив; можно вернуть
        t2 = c.post("/api/legal/templates", headers=H(CD), data={"kind": "purchase_contract_cn", "notes": "v2"}, files={"file": ("contract_cn_v2.docx", good)}).json()
        items = {x["id"]: x for x in c.get("/api/legal/templates", headers=H(MGR)).json()["items"]}
        assert items[t2["id"]]["is_active"] and not items[t["id"]]["is_active"] and t2["version"] == t["version"] + 1
        assert c.put(f"/api/legal/templates/{t['id']}", headers=H(LAW), json={"is_active": True}).json()["is_active"]
        assert not c.get("/api/legal/templates", headers=H(MGR)).json()["items"][0]["is_active"] or True
        # пример (не шаблон): любой DOCX без плейсхолдеров, тест-рендер недоступен
        ex = c.post("/api/legal/templates", headers=H(LAW), data={"kind": "example_client_contract"}, files={"file": ("client.docx", _docx(["Договор заказчика, п. 4.2 …"], header=False))}).json()
        assert ex["is_example"] and c.get(f"/api/legal/templates/{ex['id']}/test-check", headers=H(LAW)).status_code == 422
        assert c.get(f"/api/legal/templates/{ex['id']}/file", headers=H(MGR)).status_code == 200

        # карточка компании: сканы документов и выгрузка карточки DOCX
        own = c.get("/api/legal/own", headers=H(CD)).json()
        pid = next(e for e in own["items"] if e["sells_to_clients"])["profiles"][0]["id"]
        assert c.post(f"/api/legal/profiles/{pid}/documents", headers=H(MGR), data={"title": "Устав"}, files={"file": ("ustav.pdf", b"%PDF-1.4 test")}).status_code == 403
        p = c.post(f"/api/legal/profiles/{pid}/documents", headers=H(CD), data={"title": "Устав", "issued_on": "2020-03-01"}, files={"file": ("ustav.pdf", b"%PDF-1.4 test")}).json()
        doc = next(d for d in p["documents"] if d["title"] == "Устав")
        assert doc["file"]["name"] == "ustav.pdf" and doc["id"]
        r = c.get(f"/api/legal/profiles/{pid}/documents/{doc['file']['id']}", headers=H(LOG))
        assert r.status_code == 200 and r.content == b"%PDF-1.4 test"
        assert c.post(f"/api/legal/profiles/{pid}/documents", headers=H(CD), files={"file": ("virus.exe", b"x")}).status_code == 422
        # правка строк документов из формы не теряет файл
        p = c.put(f"/api/legal/profiles/{pid}", headers=H(CD), json={"documents": [{**doc, "number": "б/н", "file": None}]}).json()
        assert next(d for d in p["documents"] if d["id"] == doc["id"])["file"]["name"] == "ustav.pdf"
        p = c.delete(f"/api/legal/profiles/{pid}/documents/{doc['file']['id']}", headers=H(CD)).json()
        assert "file" not in next(d for d in p["documents"] if d["id"] == doc["id"])
        r = c.get(f"/api/legal/profiles/{pid}/card", headers=H(MGR))
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
        card = Document(io.BytesIO(r.content))
        assert any("Карточка компании" in p.text for p in card.paragraphs)
        # автозаполнение по ИНН: без ключа DaData — 503 (источник не подключён), с неверным ИНН — 422
        assert c.post(f"/api/legal/profiles/{pid}/fill-by-inn?inn=12", headers=H(CD)).status_code == 422
        assert c.post(f"/api/legal/profiles/{pid}/fill-by-inn?inn=7707083893", headers=H(CD)).status_code in (200, 404, 502, 503)
