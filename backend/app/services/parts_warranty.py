"""Гарантийные претензии к заводу и распределение расходов сборной партии (ТЗ запчастей, разделы 7 и 9)."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

CLAIM_STATUS = {"draft": "черновик", "sent": "отправлена заводу", "accepted": "признана", "partial": "признана частично", "rejected": "отказ завода",
                "compensated": "компенсация получена", "closed": "закрыта"}
COMP_KINDS = {"part": "деталью", "money": "деньгами", "discount": "скидкой на следующий заказ"}
BATCH_STATUS = {"forming": "формируется у экспедитора", "transit": "в пути", "customs": "таможня", "arrived": "прибыла", "closed": "закрыта"}
BATCH_LINE_STATUS = {"forming": "consolidation", "transit": "transit", "customs": "customs"}


def status_after_compensation(status: str, compensations: Iterable[Mapping[str, Any]]) -> str:
    """Все назначенные компенсации получены → «компенсация получена»."""
    comps = list(compensations)
    if status in ("accepted", "partial") and comps and all(c.get("received_on") for c in comps):
        return "compensated"
    return status


def allocate(lines: list[Mapping[str, Any]], total: float, basis: str = "weight") -> dict[str, float]:
    """Расходы партии на строки пропорционально весу (или стоимости); без весов — по стоимости, без стоимости — поровну.
    Округление до копеек, остаток — на самую крупную строку, сумма сходится точно."""
    if not lines or not total:
        return {l["id"]: 0.0 for l in lines}
    key = "weight" if basis == "weight" and sum(float(l.get("weight") or 0) for l in lines) > 0 else "value"
    base = [max(float(l.get(key) or 0), 0) for l in lines]
    if sum(base) <= 0:
        base = [1.0] * len(lines)
    s = sum(base)
    out = {l["id"]: round(total * b / s, 2) for l, b in zip(lines, base)}
    diff = round(total - sum(out.values()), 2)
    if diff:
        big = max(zip(lines, base), key=lambda x: x[1])[0]["id"]
        out[big] = round(out[big] + diff, 2)
    return out


def build_claim_xlsx(path: str, *, number: str, supplier: str, client: str, machine: str, hours: Any, failure_on: str, description: str,
                     lines: Iterable[Mapping[str, Any]], lang: str = "zh") -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    two = lang != "ru"
    t = {"zh": "保修索赔", "en": "Warranty claim", "de": "Garantieanspruch"}.get(lang, "")
    wb = Workbook()
    ws = wb.active
    ws.title = "Claim"
    ws.append([f"Гарантийная претензия {number}" + (f" / {t} {number}" if two else "")])
    ws["A1"].font = Font(bold=True)
    for k, v in (("Поставщик", supplier), ("Клиент", client), ("Машина / зав. №", machine), ("Наработка, м·ч", hours), ("Дата отказа", failure_on)):
        ws.append([k, v])
    ws.append(["Описание неисправности", description])
    ws.append([])
    ws.append(["№", "Артикул / Part No.", "Наименование / Description", "Кол-во / Qty"])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    for i, l in enumerate(lines, start=1):
        ws.append([i, l.get("article"), l.get("name"), l.get("qty")])
    ws.append([])
    ws.append(["Фото прилагаются отдельно." + (" / Photos attached separately." if two else "")])
    for col, w in zip("ABCD", (24, 22, 44, 12)):
        ws.column_dimensions[col].width = w
    wb.save(path)
    return path
