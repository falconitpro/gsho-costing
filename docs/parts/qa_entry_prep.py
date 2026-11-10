"""Стенд для браузерной проверки заведения заявки на запчасти: каталог, склады с остатками, файл заказа клиента."""
import json, sys
sys.path.insert(0, ".")
from openpyxl import Workbook
from app.db import SessionLocal
from app.models import Part, PartStock, Warehouse
from scripts.seed import run as seed
seed()
with SessionLocal() as db:
    whs = [Warehouse(name="Котельниково"), Warehouse(name="Куркино"), Warehouse(name="Химки (ТЕМП-ТУЛ)")]
    db.add_all(whs); db.flush()
    p1 = Part(article="1R-0750", name_ru="Фильтр топливный", oem_number="CAT 1R0750", manufacturer="Caterpillar", status="active", unit="шт")
    p2 = Part(article="4N-9618", name_ru="Палец рулевого цилиндра", manufacturer="Caterpillar", status="active", unit="шт")
    p3 = Part(article="56049301", name_ru="Фильтр гидравлический", manufacturer="Sandvik", status="active", unit="шт")
    db.add_all([p1, p2, p3]); db.flush()
    db.add_all([PartStock(part_id=p1.id, warehouse_id=whs[0].id, qty=8, reserved=2), PartStock(part_id=p1.id, warehouse_id=whs[1].id, qty=10),
                PartStock(part_id=p3.id, warehouse_id=whs[2].id, qty=1)])
    db.commit()
wb = Workbook(); ws = wb.active
for r in [["Заявка ООО «Рудник Северный» № 48"], [], ["№", "Наименование", "Каталожный номер", "Ед.", "Кол-во"],
          [1, "Фильтр гидравлический", "56049301", "шт", 4], [2, "Ремень приводной", "7S-2938", "шт", 2], [None, "Итого", None, None, 6]]:
    ws.append(r)
wb.save("order.xlsx"); print("ok")
