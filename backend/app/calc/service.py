"""Блок сервисной службы (раздел 6 ТЗ): стоимость сервиса до целевой наработки.

Количество выполнений регламента = ЦЕЛОЕ(наработка / периодичность).
Стоимость работ = Σ (выполнения × чел·ч × ставка квалификации).
Стоимость выезда = проезд + проживание + суточные (нормативы по региону) + время в пути по ставке.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Optional

from pydantic import BaseModel, Field

from .types import Money, ServiceSummary


class ServiceOperation(BaseModel):
    name: str
    hours: float                 # норматив, чел·ч
    qualification: str           # ключ ставки
    spare_part_rows: list[int] = Field(default_factory=list)   # ссылки на строки листа «ЗИП на ТО»


class MaintenanceRegulation(BaseModel):
    name: str                    # ЕТО, ТО-1, ТО-2 …
    interval_hours: float        # периодичность, м·ч
    operations: list[ServiceOperation]


class SparePartRow(BaseModel):
    """Строка листа «ЗИП на ТО» из RFQ."""

    row: int
    system: str
    part_no: str = ""
    name: str
    interval_hours: Optional[float] = None
    qty_per_service: float = 1.0
    unit_price: float = 0.0
    currency: str = "CNY"
    total_price: Optional[float] = None    # если завод дал цену за весь комплект (13.8)


class RegionNorms(BaseModel):
    travel_rub: float = 30000.0          # проезд туда-обратно на человека
    lodging_rub_per_day: float = 4000.0
    per_diem_rub: float = 1500.0
    travel_hours: float = 16.0            # время в пути (оплачивается по ставке)


class ServiceInput(BaseModel):
    machine_type: str
    model: str
    qty: int = 1
    target_hours: float = 10000.0
    hours_per_month: float = 400.0
    regulations: list[MaintenanceRegulation]
    spare_parts: list[SparePartRow] = Field(default_factory=list)
    rates_rub_per_hour: dict[str, float] = Field(default_factory=lambda: {"engineer_1": 3500.0, "engineer_2": 2800.0, "mechanic": 2000.0})
    region: RegionNorms = Field(default_factory=RegionNorms)
    crew_size: int = 2
    visit_days_default: int = 3
    visit_overrides: dict[int, dict] = Field(default_factory=dict)   # месяц → {"days":, "crew":, "visits":}
    commissioning_rub: float = 0.0    # шеф-монтаж и ПНР
    training_rub: float = 0.0         # обучение персонала (14.8)
    unplanned_reserve_pct: float = 5.0  # % от стоимости ЗИП
    spare_parts_currency: str = "CNY"


class ServiceMonthRow(BaseModel):
    month: int
    hours_cum: float
    regulations: list[str]
    labour_hours: float
    labour_rub: float
    visits: int
    visit_rub: float
    spare_parts_fx: float
    total_rub_part: float


class ServiceResult(BaseModel):
    rows: list[ServiceMonthRow]
    executions: dict[str, int]
    labour_rub: float
    visits_rub: float
    spare_parts_fx: Money
    reserve_fx: Money
    commissioning_rub: float
    training_rub: float
    total_rub_part: float
    total_fx_part: Money

    def summary(self, show_in_kp: bool = False) -> ServiceSummary:
        return ServiceSummary(cost_fx=self.total_fx_part, cost_rub=self.total_rub_part, show_in_kp=show_in_kp)


def calc_service(inp: ServiceInput) -> ServiceResult:
    months_total = max(1, math.ceil(inp.target_hours / max(inp.hours_per_month, 1)))
    executions = {r.name: int(inp.target_hours // r.interval_hours) for r in inp.regulations}

    # план регламентов по месяцам (по интенсивности)
    by_month: dict[int, list[MaintenanceRegulation]] = defaultdict(list)
    for r in inp.regulations:
        for n in range(1, executions[r.name] + 1):
            h = n * r.interval_hours
            m = max(1, math.ceil(h / max(inp.hours_per_month, 1)))
            by_month[m].append(r)

    # ЗИП по регламентам: цена строк листа ЗИП × выполнения
    sp_by_row = {s.row: s for s in inp.spare_parts}
    rows: list[ServiceMonthRow] = []
    labour_total = visits_total = sp_total = 0.0
    hours_cum = 0.0
    for m in range(1, months_total + 1):
        hours_cum = min(inp.target_hours, m * inp.hours_per_month)
        regs = by_month.get(m, [])
        labour_h = 0.0
        labour_rub = 0.0
        sp_fx = 0.0
        for r in regs:
            for op in r.operations:
                labour_h += op.hours * inp.qty
                labour_rub += op.hours * inp.qty * inp.rates_rub_per_hour.get(op.qualification, 0.0)
                for row in op.spare_part_rows:
                    s = sp_by_row.get(row)
                    if s:
                        sp_fx += s.unit_price * s.qty_per_service * inp.qty
        visits = 0
        visit_rub = 0.0
        if regs:
            ov = inp.visit_overrides.get(m, {})
            visits = int(ov.get("visits", 1))
            days = int(ov.get("days", inp.visit_days_default))
            crew = int(ov.get("crew", inp.crew_size))
            rate = max(inp.rates_rub_per_hour.values()) if inp.rates_rub_per_hour else 0.0
            per_visit = crew * (inp.region.travel_rub + days * (inp.region.lodging_rub_per_day + inp.region.per_diem_rub)
                                + inp.region.travel_hours * rate)
            visit_rub = visits * per_visit
        labour_total += labour_rub
        visits_total += visit_rub
        sp_total += sp_fx
        rows.append(ServiceMonthRow(month=m, hours_cum=hours_cum, regulations=[r.name for r in regs],
                                    labour_hours=round(labour_h, 1), labour_rub=round(labour_rub, 2), visits=visits,
                                    visit_rub=round(visit_rub, 2), spare_parts_fx=round(sp_fx, 2),
                                    total_rub_part=round(labour_rub + visit_rub, 2)))

    # если завод дал цену ЗИП за весь комплект (13.8) и строки без цен — используем её
    kit_total = sum(s.total_price or 0.0 for s in inp.spare_parts)
    if sp_total == 0 and kit_total:
        sp_total = kit_total
    reserve = sp_total * inp.unplanned_reserve_pct / 100.0
    rub_part = labour_total + visits_total + inp.commissioning_rub + inp.training_rub
    return ServiceResult(
        rows=rows, executions=executions, labour_rub=round(labour_total, 2), visits_rub=round(visits_total, 2),
        spare_parts_fx=Money(amount=round(sp_total, 2), currency=inp.spare_parts_currency),
        reserve_fx=Money(amount=round(reserve, 2), currency=inp.spare_parts_currency),
        commissioning_rub=inp.commissioning_rub, training_rub=inp.training_rub,
        total_rub_part=round(rub_part, 2),
        total_fx_part=Money(amount=round(sp_total + reserve, 2), currency=inp.spare_parts_currency),
    )
