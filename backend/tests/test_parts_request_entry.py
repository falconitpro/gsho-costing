"""Удобное заведение заявки на запчасти (D-171…D-173): заказ клиента из файла, подбор до создания, строки из каталога, удаление ошибочной заявки."""
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

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, GD, LOG = "manager@example.com", "cd@example.com", "evgeniy@dobrov.pro", "logist@example.com"
T = uuid.uuid4().hex[:5].upper()


def _xlsx(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def test_order_file_preview_catalog_and_delete():
    seed()
    with TestClient(app) as c:
        p = c.post("/api/parts/catalog", headers=H(CD), json={"article": f"EN-{T}", "name_ru": "Фильтр топливный", "oem_number": f"OEM-{T}"}).json()
        # Excel клиента: шапка не в первой строке, колонки в своём порядке, строка «Итого» пропускается
        x = _xlsx([["Заказ № 15 от ООО «Рудник»"], [], ["№", "Наименование", "Кат. номер", "Ед. изм.", "Кол-во"],
                   [1, "Фильтр топливный", f"OEM-{T}", "шт", 12], [2, "Палец рулевой", f"NEW-{T}", "шт", "2,5"], [None, "Итого", None, None, 14.5]])
        assert c.post("/api/parts/requests/parse-file", headers=H(LOG), files={"file": ("order.xlsx", x)}).status_code == 403
        r = c.post("/api/parts/requests/parse-file", headers=H(MGR), files={"file": ("order.xlsx", x)})
        assert r.status_code == 200, r.text
        res = r.json()
        assert res["header_row"] == 3 and res["columns"]["article"] == "Кат. номер" and res["columns"]["qty"] == "Кол-во"
        assert [(l["raw_article"], l["qty"]) for l in res["lines"]] == [(f"OEM-{T}", 12), (f"NEW-{T}", 2.5)]
        assert res["lines"][0]["part"]["id"] == p["id"] and res["lines"][0]["match_kind"] == "oem" and res["lines"][1]["part"] is None
        # CSV без шапки (; и cp1251)
        csv = f"EN-{T};Фильтр;3\nZZ-{T};Неизвестная;1\n".encode("cp1251")
        res2 = c.post("/api/parts/requests/parse-file", headers=H(MGR), files={"file": ("order.csv", csv)}).json()
        assert [(l["raw_article"], l["qty"]) for l in res2["lines"]] == [(f"EN-{T}", 3), (f"ZZ-{T}", 1)] and res2["lines"][0]["part"]["id"] == p["id"]
        assert c.post("/api/parts/requests/parse-file", headers=H(MGR), files={"file": ("order.pdf", b"%PDF")}).status_code == 422
        # наличие по складам — сразу в строке
        from app.db import SessionLocal
        from app.models import PartStock, Warehouse
        from sqlalchemy import select
        with SessionLocal() as db:
            whs = [Warehouse(name=f"Склад Алдан {T}"), Warehouse(name=f"Склад Мирный {T}")]
            db.add_all(whs)
            db.flush()
            db.add(PartStock(part_id=p["id"], warehouse_id=whs[0].id, qty=5, reserved=1))
            if len(whs) > 1:
                db.add(PartStock(part_id=p["id"], warehouse_id=whs[1].id, qty=10, reserved=0))
            db.commit()
            total = 14
            names = {w.name for w in whs}
        res = c.post("/api/parts/requests/parse-file", headers=H(MGR), files={"file": ("order.xlsx", x)}).json()
        l0 = res["lines"][0]
        assert l0["part"]["available"] == total and l0["stock_state"] == ("full" if total >= 12 else "partial")
        assert {w["warehouse"] for w in l0["part"]["warehouses"]} == names and res["lines"][1]["stock_state"] is None
        pv = c.post("/api/parts/requests/preview-lines", headers=H(MGR), json={"lines": [{"part_id": p["id"], "qty": 2}], "lines_text": f"EN-{T}\t\t4"}).json()["lines"]
        assert pv[0]["part"]["article"] == f"EN-{T}" and pv[0]["match_kind"] == "catalog" and pv[1]["part"]["id"] == p["id"]

        # заявка: строки из файла и из каталога
        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        req = c.post("/api/parts/requests", headers=H(MGR), json={"client_id": cl["id"], "lines": [
            {"raw_article": l["raw_article"], "raw_name": l["raw_name"], "qty": l["qty"], "unit": l["unit"]} for l in res["lines"]] + [{"part_id": p["id"], "qty": 1}]})
        assert req.status_code == 201, req.text
        rq = req.json()
        assert len(rq["lines"]) == 3 and rq["lines"][2]["part_id"] == p["id"] and rq["lines"][0]["part_id"] == p["id"]
        assert rq["lines"][0]["warehouses"] and rq["lines"][1]["warehouses"] == []
        # удаление ошибочной заявки: чужой менеджер/логист — нельзя, автор — можно; заявка с КП — нельзя
        assert c.delete(f"/api/parts/requests/{rq['id']}", headers=H(LOG)).status_code in (403, 404)
        d = c.delete(f"/api/parts/requests/{rq['id']}", headers=H(MGR))
        assert d.status_code == 200 and d.json()["deleted"] == rq["number"]
        assert c.get(f"/api/parts/requests/{rq['id']}", headers=H(MGR)).status_code == 404
        rq2 = c.post("/api/parts/requests", headers=H(MGR), json={"client_id": cl["id"], "lines": [{"part_id": p["id"], "qty": 1}]}).json()
        line = rq2["lines"][0]
        c.patch(f"/api/parts/requests/{rq2['id']}/lines/{line['id']}", headers=H(MGR), json={"price": 1000, "currency": "RUB"})
        kp = c.post(f"/api/parts/requests/{rq2['id']}/kp", headers=H(CD), json={"line_ids": [line["id"]]})
        if kp.status_code == 201:
            r = c.delete(f"/api/parts/requests/{rq2['id']}", headers=H(GD))
            assert r.status_code == 409 and "КП" in r.json()["detail"]



def test_stock_balances_by_warehouse():
    seed()
    from app.db import SessionLocal
    from app.models import Part, PartStock, Warehouse
    t = uuid.uuid4().hex[:5].upper()
    with SessionLocal() as db:
        w1, w2 = Warehouse(name=f"Склад А {t}"), Warehouse(name=f"Склад Б {t}")
        p1 = Part(article=f"BAL-{t}-1", name_ru="Фильтр", status="active", unit="шт", part_group=f"Фильтры {t}", purchase_price=100, purchase_currency="RUB")
        p2 = Part(article=f"BAL-{t}-2", name_ru="Ремень", status="active", unit="шт", part_group=f"Ремни {t}")
        db.add_all([w1, w2, p1, p2])
        db.flush()
        db.add_all([PartStock(part_id=p1.id, warehouse_id=w1.id, qty=10, reserved=3, min_qty=8), PartStock(part_id=p1.id, warehouse_id=w2.id, qty=5),
                    PartStock(part_id=p2.id, warehouse_id=w2.id, qty=2)])
        db.commit()
        w1_id, w2_id = w1.id, w2.id
    with TestClient(app) as c:
        d = c.get(f"/api/parts/stock/balances?q=BAL-{t}", headers=H(CD)).json()
        sw = {w["id"]: w for w in d["warehouses"]}
        assert (sw[w1_id]["positions"], sw[w1_id]["qty"], sw[w1_id]["reserved"], sw[w1_id]["available"], sw[w1_id]["below_min"]) == (1, 10, 3, 7, 1)
        assert sw[w2_id]["positions"] == 2 and d["costs"] is True and sw[w1_id]["value_rub"] == 1000
        r1 = next(r for r in d["rows"] if r["article"] == f"BAL-{t}-1")
        assert r1["available"] == 12 and r1["below_min"] is True and r1["cells"][w1_id]["below_min"] is True and r1["value_rub"] == 1500
        assert [r["article"] for r in c.get(f"/api/parts/stock/balances?q=BAL-{t}&below_min=true", headers=H(CD)).json()["rows"]] == [f"BAL-{t}-1"]
        assert [r["article"] for r in c.get(f"/api/parts/stock/balances?q=BAL-{t}&group=Ремни {t}", headers=H(CD)).json()["rows"]] == [f"BAL-{t}-2"]
        one = c.get(f"/api/parts/stock/balances?q=BAL-{t}&warehouse_id={w1_id}", headers=H(CD)).json()
        assert [r["article"] for r in one["rows"]] == [f"BAL-{t}-1"] and list(one["rows"][0]["cells"]) == [w1_id]
        lg = c.get(f"/api/parts/stock/balances?q=BAL-{t}", headers=H(LOG))
        if lg.status_code == 200:            # склад без права «Себестоимость» — без стоимости
            assert lg.json()["costs"] is False and "value_rub" not in lg.json()["rows"][0] and "value_rub" not in lg.json()["warehouses"][0]
        x = c.get(f"/api/parts/stock/balances/xlsx?q=BAL-{t}", headers=H(CD))
        assert x.status_code == 200 and x.content[:2] == b"PK"
