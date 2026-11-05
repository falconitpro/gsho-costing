"""Запчасти, шаг 5а — запросы цен поставщикам на живой PostgreSQL: создание по строкам, файл, ответы, сравнение и выбор.

Запускается, когда задан DATABASE_URL (в CI — сервис postgres). Без БД — пропускается.
"""
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

from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

from app.main import app  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, LOG = "manager@example.com", "cd@example.com", "logist@example.com"


@pytest.fixture(scope="module")
def client():
    seed()
    with TestClient(app) as c:
        yield c


def _ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:800])
    return r.json() if r.content else None


def test_rfq_flow(client):
    t = uuid.uuid4().hex[:5].upper()
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    cn = _ok(client.post("/api/refs/factories", headers=H(CD), json={"name": f"RFQ CN {t}", "country": "CN", "default_currency": "CNY",
                                                                     "contacts": [{"name": "Li", "roles": ["rfq"], "email": f"li-{t}@example.com"}]}), 201)
    de = _ok(client.post("/api/refs/factories", headers=H(CD), json={"name": f"RFQ DE {t}", "country": "DE", "default_currency": "EUR"}), 201)
    p = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": f"RFQ-{t}", "name_ru": "Фильтр гидравлический", "names": {"cn": "液压滤芯"},
                                                                   "cross_refs": [{"kind": "supplier", "number": f"CN-{t}", "supplier_id": cn["id"]}]}), 201)
    r = _ok(client.post("/api/parts/requests", headers=H(MGR), json={"client_id": cl["id"], "lines_text": f"RFQ-{t}\t\t2\nNONE-{t}\tПалец\t4"}), 201)
    ids = [l["id"] for l in r["lines"]]

    assert client.post(f"/api/parts/requests/{r['id']}/rfq", headers=H(LOG), json={"line_ids": ids, "supplier_ids": [cn["id"]]}).status_code == 403
    res = _ok(client.post(f"/api/parts/requests/{r['id']}/rfq", headers=H(MGR), json={"line_ids": ids, "supplier_ids": [cn["id"], de["id"]]}), 201)
    assert len(res["rfqs"]) == 2 and all(l["status"] == "rfq" for l in res["request"]["lines"])
    q_cn, q_de = res["rfqs"]
    assert q_cn["number"].startswith("ЗП-") and q_cn["language"] == "zh" and q_de["language"] == "de"
    l1 = q_cn["lines"][0]
    assert (l1["number"], l1["name_foreign"], l1["machine_translated"]) == (f"CN-{t}", "液压滤芯", False)   # номер поставщика и имя из карточки
    assert q_de["lines"][0]["number"] == f"RFQ-{t}"

    # файл запроса на двух языках
    x = client.get(f"/api/parts/rfq/{q_cn['id']}/xlsx", headers=H(MGR))
    assert x.status_code == 200
    ws = load_workbook(io.BytesIO(x.content)).active
    rows = list(ws.iter_rows(values_only=True))
    hdr = next(i for i, row in enumerate(rows) if row[0] and str(row[0]).startswith("№"))
    assert "序号" in rows[hdr][0] and rows[hdr + 1][1] == f"CN-{t}"

    # отправка: у китайского поставщика есть e-mail, у немецкого — нет контакта
    s = _ok(client.post(f"/api/parts/rfq/{q_de['id']}/send", headers=H(MGR), json={}))
    assert all(not x["ok"] and "не отправлено" in x["error"] for x in s["send_results"]) and s["status"] == "draft"
    _ok(client.post(f"/api/parts/rfq/{q_de['id']}/mark-sent", headers=H(MGR)))

    # ответ китайского поставщика файлом: только первая строка
    ws.cell(row=hdr + 2, column=7, value="有")
    ws.cell(row=hdr + 2, column=8, value="120,5")
    ws.cell(row=hdr + 2, column=9, value="CNY")
    ws.cell(row=hdr + 2, column=10, value=20)
    buf = io.BytesIO()
    ws.parent.save(buf)
    imp = _ok(client.post(f"/api/parts/rfq/{q_cn['id']}/import", headers=H(MGR), files={"file": ("a.xlsx", buf.getvalue())}))
    assert imp["imported"] == 1 and imp["status"] == "partial" and imp["lines"][0]["price"] == 120.5 and imp["lines"][0]["availability"] == "available"

    # ответ немецкого поставщика вручную; без цены «в наличии» не принимается
    dl = q_de["lines"]
    assert client.put(f"/api/parts/rfq/{q_de['id']}/answers", headers=H(MGR), json={"lines": [{"id": dl[0]["id"], "availability": "available"}]}).status_code == 422
    a = _ok(client.put(f"/api/parts/rfq/{q_de['id']}/answers", headers=H(MGR), json={"lines": [
        {"id": dl[0]["id"], "availability": "on_order", "price": 25, "currency": "EUR", "lead_days": 30},
        {"id": dl[1]["id"], "availability": "none"}]}))
    assert a["status"] == "answered"

    # сравнение и выбор
    qs = _ok(client.get(f"/api/parts/requests/{r['id']}/quotes", headers=H(MGR)))
    first = qs[ids[0]]
    assert {x["supplier_name"] for x in first} == {f"RFQ CN {t}", f"RFQ DE {t}"}
    best = next(x for x in first if x["supplier_id"] == cn["id"])
    none = next(x for x in qs[ids[1]] if x["supplier_id"] == de["id"])
    assert client.post(f"/api/parts/requests/{r['id']}/lines/{ids[1]}/choose", headers=H(MGR), json={"rfq_line_id": none["id"]}).status_code == 422
    assert client.post(f"/api/parts/requests/{r['id']}/lines/{ids[1]}/choose", headers=H(MGR), json={"rfq_line_id": best["id"]}).status_code == 422
    ch = _ok(client.post(f"/api/parts/requests/{r['id']}/lines/{ids[0]}/choose", headers=H(MGR), json={"rfq_line_id": best["id"]}))
    L = next(l for l in ch["lines"] if l["id"] == ids[0])
    assert (L["supplier_id"], L["quote_price"], L["quote_currency"], L["status"], L["lead_days"]) == (cn["id"], 120.5, "CNY", "quoted", 20)
    assert L["price"] and L["price_source"] == "calc"                  # цена клиенту — от себестоимости по ответу поставщика
    assert "cost" not in L
    cd = next(l for l in _ok(client.get(f"/api/parts/requests/{r['id']}", headers=H(CD)))["lines"] if l["id"] == ids[0])
    assert cd["cost"] and "ответ поставщика" in (cd["cost_note"] or "")
    lst = _ok(client.get(f"/api/parts/rfq?request_id={r['id']}", headers=H(MGR)))
    assert {x["id"] for x in lst} == {q_cn["id"], q_de["id"]}
    # напоминание только по строкам без ответа
    rem = _ok(client.post(f"/api/parts/rfq/{q_cn['id']}/send", headers=H(MGR), json={"only_unanswered": True}))
    assert rem["send_results"][0]["via"] == "mail"
