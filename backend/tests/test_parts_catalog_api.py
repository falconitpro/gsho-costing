"""Запчасти, шаг 2 — API каталога на живой PostgreSQL: карточка, дубли, поиск, перенос из МойСклад.

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
from openpyxl import Workbook  # noqa: E402

from app.main import app  # noqa: E402
from scripts.seed import run as seed  # noqa: E402
from tests.test_moysklad_import import HEADER, row  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, LOG, FD = "manager@example.com", "cd@example.com", "logist@example.com", "fd@example.com"


@pytest.fixture(scope="module")
def client():
    seed()
    with TestClient(app) as c:
        yield c


def _ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:800])
    return r.json() if r.content else None


def _xlsx(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_card_duplicates_search(client):
    art = f"T-{uuid.uuid4().hex[:6]}"
    body = {"article": art, "name_ru": "Фильтр гидравлический", "manufacturer": "Sandvik", "oem_number": f"OEM-{art}", "part_group": "Фильтры",
            "length_mm": 200, "width_mm": 100, "height_mm": 100, "purchase_price": 50, "purchase_currency": "EUR",
            "cross_refs": [{"kind": "supplier", "number": f"S-{art}"}, {"kind": "analog_full", "number": f"A-{art}", "manufacturer": "MANN"}],
            "applicability": [{"other_manufacturer": "Sandvik", "other_model": "LH410", "assembly": "Гидросистема"}]}
    assert client.post("/api/parts/catalog", headers=H(LOG), json=body).status_code == 403          # логист карточки не заводит
    p = _ok(client.post("/api/parts/catalog", headers=H(MGR), json=body), 201)
    assert p["unit"] == "шт" and p["volume_m3"] == 0.002 and len(p["cross_refs"]) == 2 and p["applicability"][0]["assembly"] == "Гидросистема"
    # дубль по артикулу, по OEM и по кросс-номеру
    r = client.post("/api/parts/catalog", headers=H(MGR), json={**body, "oem_number": ""})
    assert r.status_code == 409 and r.json()["detail"]["duplicates"][0]["id"] == p["id"]
    assert _ok(client.get(f"/api/parts/catalog/check-duplicates?article=S-{art}", headers=H(MGR)))[0]["id"] == p["id"]
    # тот же номер у другого производителя — не дубль
    other = _ok(client.post("/api/parts/catalog", headers=H(MGR), json={**body, "manufacturer": "Caterpillar", "oem_number": "", "cross_refs": []}), 201)
    assert other["id"] != p["id"]
    # поиск по артикулу, OEM и кросс-номеру; фильтр по группе
    assert _ok(client.get(f"/api/parts/catalog?q=A-{art}", headers=H(MGR)))["total"] == 1
    assert _ok(client.get(f"/api/parts/catalog?q={art}&group=Фильтры", headers=H(MGR)))["total"] >= 2
    meta = _ok(client.get("/api/parts/meta/catalog", headers=H(MGR)))
    assert "Фильтры" in meta["groups"] and meta["can_edit"] and not meta["can_import"]
    # архив и правка
    upd = _ok(client.put(f"/api/parts/catalog/{other['id']}", headers=H(CD), json={**body, "manufacturer": "Caterpillar", "oem_number": "",
                                                                                 "cross_refs": [], "status": "archive"}))
    assert upd["status"] == "archive"
    assert other["id"] not in {x["id"] for x in _ok(client.get(f"/api/parts/catalog?q={art}", headers=H(MGR)))["items"]}
    assert client.post("/api/parts/catalog", headers=H(MGR), json={**body, "unit": " "}).status_code == 422


def test_import_moysklad_products_and_stock(client):
    tag = uuid.uuid4().hex[:6]
    rows = [tuple(HEADER),
            row(UUID=f"ms-{tag}-1", Код="1", Наименование="Топливный фильтр", Артикул=f"MS{tag}1"),
            row(UUID=f"ms-{tag}-2", Код="2", Наименование="Ведущий мост", Артикул=f"MS{tag}2", **{
                'Цена: ООО "ЕВРОХИМ-ВОЛГАКАЛИЙ"': "21 862,60", "Неснижаемый остаток для склада Бишкек": "1",
                "Неснижаемый остаток для склада Котельниково": "2"})]
    files = lambda: {"file": ("goods.xlsx", _xlsx(rows), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}   # noqa: E731
    assert client.post("/api/parts/import/moysklad-products", headers=H(MGR), files=files()).status_code == 403
    dry = _ok(client.post("/api/parts/import/moysklad-products", headers=H(CD), files=files(), data={"dry_run": "true"}))
    assert dry["report"]["rows"] == 2 and dry["result"]["created"] == 2 and dry["result"]["dry_run"]
    assert _ok(client.get(f"/api/parts/catalog?q=MS{tag}", headers=H(CD)))["total"] == 0                  # проверка ничего не записала
    res = _ok(client.post("/api/parts/import/moysklad-products", headers=H(CD), files=files(), data={"dry_run": "false"}))
    assert res["result"]["created"] == 2 and res["result"]["unmatched_clients"] == {'ООО "ЕВРОХИМ-ВОЛГАКАЛИЙ"': 1}
    again = _ok(client.post("/api/parts/import/moysklad-products", headers=H(CD), files=files(), data={"dry_run": "false"}))
    assert again["result"]["created"] == 0 and again["result"]["updated"] == 2                           # повторный перенос — по UUID
    items = _ok(client.get(f"/api/parts/catalog?q=MS{tag}", headers=H(CD)))["items"]
    most = next(i for i in items if i["article"] == f"MS{tag}2")
    card = _ok(client.get(f"/api/parts/catalog/{most['id']}", headers=H(CD)))
    assert card["client_prices"][0]["price"] == 21862.6 and card["source"] == "moysklad"
    whs = {w["name"]: w for w in _ok(client.get("/api/parts/warehouses", headers=H(CD)))}
    assert whs["Бишкек"]["kind"] == "virtual" and whs["Бишкек"]["legal_entity"] == "Компания в Киргизии" and whs["Котельниково"]["kind"] == "physical"
    assert {s["warehouse"]: s["min_qty"] for s in card["stock"]} == {"Бишкек": 1.0, "Котельниково": 2.0}

    stock = [("Наименование", "Артикул", "Ед. изм.", "Остаток", "Котельниково", "Куркино"),
             ("Ведущий мост", f"MS{tag}2", "шт", 3, 1, 2), ("Нет в каталоге", "NOPE-1", "шт", 1, 1, 0)]
    st = _ok(client.post("/api/parts/import/stock", headers=H(CD),
                         files={"file": ("stock.xlsx", _xlsx(stock), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}))
    assert st["result"]["updated"] == 1 and st["result"]["missing_articles"] == ["NOPE-1"]
    card = _ok(client.get(f"/api/parts/catalog/{most['id']}", headers=H(MGR)))
    assert card["stock_total"] == 3 and {s["warehouse"]: s["qty"] for s in card["stock"] if s["qty"]} == {"Котельниково": 1.0, "Куркино": 2.0}
    assert _ok(client.get(f"/api/parts/catalog?q=MS{tag}&in_stock=true", headers=H(MGR)))["total"] == 1

    # клиент заведён после переноса — договорные цены привязываются к нему сами
    assert card["client_prices"][0]["client_id"] is None
    cl = _ok(client.post("/api/refs/clients", headers=H(CD), json={"name": "ООО «ЕВРОХИМ-ВОЛГАКАЛИЙ»", "inn": "7710473036"}), 201)
    card = _ok(client.get(f"/api/parts/catalog/{most['id']}", headers=H(CD)))
    assert card["client_prices"][0]["client_id"] == cl["id"]


def test_stock_for_duplicate_article_by_name(client):
    art = f"DUP-{uuid.uuid4().hex[:6]}"
    a = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": art, "name_ru": "Вентилятор"}), 201)
    b = _ok(client.post("/api/parts/catalog?force=true", headers=H(CD), json={"article": art, "name_ru": "Вентилятор кабины"}), 201)
    stock = [("Наименование", "Артикул", "Ед. изм.", "Остаток", "Котельниково"), ("Вентилятор кабины", art, "шт", 5, 5), ("Насос", art, "шт", 1, 1)]
    st = _ok(client.post("/api/parts/import/stock", headers=H(CD),
                         files={"file": ("stock.xlsx", _xlsx(stock), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}))
    assert st["result"]["updated"] == 1 and st["result"]["ambiguous_articles"] == [art]   # «Насос» — не совпало наименование
    assert _ok(client.get(f"/api/parts/catalog/{b['id']}", headers=H(CD)))["stock_total"] == 5
    assert _ok(client.get(f"/api/parts/catalog/{a['id']}", headers=H(CD)))["stock_total"] == 0

    # полный дубль (тот же артикул и наименование): лишнюю карточку — в архив, остаток уходит на оставшуюся
    c = _ok(client.post("/api/parts/catalog?force=true", headers=H(CD), json={"article": art, "name_ru": "Вентилятор кабины"}), 201)
    st = _ok(client.post("/api/parts/import/stock", headers=H(CD),
                         files={"file": ("stock.xlsx", _xlsx(stock[:2]), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}))
    assert st["result"]["ambiguous_articles"] == [art]
    _ok(client.put(f"/api/parts/catalog/{c['id']}", headers=H(CD), json={"article": art, "name_ru": "Вентилятор кабины", "status": "archive"}))
    stock2 = [stock[0], ("Вентилятор кабины", art, "шт", 7, 7)]
    st = _ok(client.post("/api/parts/import/stock", headers=H(CD),
                         files={"file": ("stock.xlsx", _xlsx(stock2), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}))
    assert st["result"]["updated"] == 1 and st["result"]["ambiguous_articles"] == []
    assert _ok(client.get(f"/api/parts/catalog/{b['id']}", headers=H(CD)))["stock_total"] == 7
