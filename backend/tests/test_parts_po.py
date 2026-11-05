"""Заказы поставщикам запчастей: статусы строк и заказа, дата на складе, внутригрупповые ступени, файл заказа."""
from __future__ import annotations

import os
import tempfile
from datetime import date

from openpyxl import load_workbook

from app.services.parts_po import build_po_xlsx, can_move, eta, intragroup_steps, po_status


def test_eta_and_moves():
    assert eta(date(2026, 11, 5), 20, 25) == date(2026, 12, 20)
    assert eta(date(2026, 11, 5), None) == date(2026, 11, 5)
    assert can_move("ordered", "transit") and can_move("confirmed", "cancelled")
    assert not can_move("transit", "confirmed") and not can_move("received", "cancelled") and not can_move("cancelled", "ordered")


def test_po_status():
    assert po_status("draft", ["ordered"]) == "draft"
    assert po_status("sent", ["confirmed", "confirmed"]) == "confirmed"
    assert po_status("confirmed", ["confirmed", "shipped"]) == "in_progress"
    assert po_status("in_progress", ["received", "cancelled"]) == "closed"
    assert po_status("sent", ["cancelled"]) == "cancelled"


def test_intragroup_steps():
    sim = {"steps": [{"no": 1, "seller_entity_id": None, "buyer_entity_id": "cn", "currency": "CNY", "price": 100},
                     {"no": 2, "seller_entity_id": "cn", "buyer_entity_id": "kg", "currency": "USD", "incoterms": "FCA", "price": 14.7},
                     {"no": 3, "seller_entity_id": "kg", "buyer_entity_id": "ru", "currency": "USD", "incoterms": "DAP", "price": 15.6}]}
    st = intragroup_steps(sim)
    assert [(s["no"], s["seller_entity_id"], s["buyer_entity_id"], s["price"]) for s in st] == [(2, "cn", "kg", 14.7), (3, "kg", "ru", 15.6)]


def test_po_xlsx():
    path = os.path.join(tempfile.mkdtemp(), "po.xlsx")
    build_po_xlsx(path, number="ЗК-2026-0001", seller="Завод", buyer="Компания в Китае", lang="zh", currency="CNY", incoterms="EXW",
                  ship_date="01.12.2026", lines=[{"no": 1, "number": "A-1", "name": "Фильтр", "name_foreign": "滤芯", "qty": 2, "unit": "шт", "price": 10.5}])
    rows = list(load_workbook(path).active.iter_rows(values_only=True))
    assert "订单" in rows[0][0]
    hdr = next(i for i, r in enumerate(rows) if r[0] and str(r[0]).startswith("№"))
    assert rows[hdr + 1][:8] == (1, "A-1", "Фильтр", "滤芯", 2, "шт", 10.5, 21.0)
    assert rows[hdr + 2][-1] == 21.0
