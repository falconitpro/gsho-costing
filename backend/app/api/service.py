"""Блок сервисной службы: регламенты, расчёт сервиса до наработки, завершение блока."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..calc.service import MaintenanceRegulation, RegionNorms, ServiceInput, SparePartRow, calc_service
from ..core.auth import CurrentUser, require_roles
from ..core.roles import Role
from ..core.workflow import DealState, DealStatus, block_completed
from ..db import get_db
from ..models import EquipmentModel, Position
from ..services import settings_service
from ..services.deal_service import apply_transition, audit, complete_tasks, sla
from .common import get_deal_or_404

router = APIRouter(prefix="/api/deals/{deal_id}/service", tags=["service"])
SERVICE = require_roles(Role.SERVICE_HEAD, Role.GD)


class ServiceCalcIn(BaseModel):
    regulations: list[MaintenanceRegulation]
    hours_per_month: float | None = None
    crew_size: int = 2
    visit_days_default: int = 3
    visit_overrides: dict[int, dict] = {}
    commissioning_rub: float = 0.0
    training_rub: float = 0.0
    region_key: str = "default"
    warranty_reserve_pct: float | None = None   # гарантийные случаи по позиции, % от цены завода; None — по настройке типа машины
    save_regulations_to_catalog: bool = True
    show_in_kp: bool = False


@router.get("/positions/{pos_id}/defaults")
def defaults(deal_id: str, pos_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(SERVICE)):
    """Исходные данные: регламенты из каталога модели, интенсивность по типу, лист ЗИП, нормативы региона."""
    deal = get_deal_or_404(db, deal_id, user)
    pos = db.get(Position, pos_id)
    if pos is None or pos.deal_id != deal.id:
        raise HTTPException(404)
    s = settings_service.get_all(db)
    cat = db.scalar(select(EquipmentModel).where(EquipmentModel.factory_id == pos.factory_id, EquipmentModel.model == pos.model))
    return {
        "machine_type": pos.machine_type, "model": pos.model, "qty": pos.qty, "target_hours": deal.target_hours or 10000,
        "hours_per_month": (cat.default_hours_per_month if cat and cat.default_hours_per_month else None)
                           or s["hours_per_month_by_type"].get(pos.machine_type, s["hours_per_month_by_type"]["default"]),
        "regulations": cat.maintenance_regulations if cat else [],
        "spare_parts": (pos.service_calc or {}).get("spare_parts", []),
        "rates": s["labour_rates_rub_per_hour"], "region_norms": s["region_norms"].get(deal.region, s["region_norms"]["default"]),
        "unplanned_reserve_pct": s["unplanned_reserve_pct"], "training_rfq": (pos.tech_params or {}).get("14.8", {}).get("value"),
        # гарантийные случаи: % от цены завода (статья 13 себестоимости) — по позиции или по типу машины из настроек
        "warranty": _warranty(pos, s),
    }


def _warranty(pos: Position, s: dict) -> dict:
    wr = s.get("warranty_reserve_pct_by_type") or {}
    by_type = wr.get(pos.machine_type, wr.get("default", 2.5))
    pct = pos.warranty_reserve_pct if pos.warranty_reserve_pct is not None else by_type
    base = (pos.unit_price or 0) * (pos.qty or 1) + (pos.options_price or 0)
    return {"pct": pct, "by_type_pct": by_type, "overridden": pos.warranty_reserve_pct is not None,
            "factory_price": base, "currency": pos.currency, "amount": round(base * pct / 100.0, 2)}


@router.post("/positions/{pos_id}/calc")
def calc(deal_id: str, pos_id: str, body: ServiceCalcIn, db: Session = Depends(get_db), user: CurrentUser = Depends(SERVICE)):
    deal = get_deal_or_404(db, deal_id, user)
    if not deal.service_flag:
        raise HTTPException(409, "Флаг «Сервис до наработки» не активен")
    if deal.status not in (DealStatus.LOGISTICS_CALC, DealStatus.SERVICE_CALC, DealStatus.REWORK):
        raise HTTPException(409, "Блок сервиса редактируется только на этапе расчёта или доработки")
    pos = db.get(Position, pos_id)
    if pos is None or pos.deal_id != deal.id:
        raise HTTPException(404)
    d = defaults(deal_id, pos_id, db, user)
    region = d["region_norms"]
    inp = ServiceInput(
        machine_type=pos.machine_type, model=pos.model, qty=pos.qty, target_hours=d["target_hours"],
        hours_per_month=body.hours_per_month or d["hours_per_month"], regulations=body.regulations,
        spare_parts=[SparePartRow(**{k: (v if k != "qty_per_service" or v else 1.0) for k, v in sp.items() if k in SparePartRow.model_fields})
                     for sp in d["spare_parts"] if sp.get("filled", True)],   # только строки, заполненные заводом
        rates_rub_per_hour=d["rates"], region=RegionNorms(**{k: v for k, v in region.items() if k in RegionNorms.model_fields}),
        crew_size=body.crew_size, visit_days_default=body.visit_days_default, visit_overrides=body.visit_overrides,
        commissioning_rub=body.commissioning_rub, training_rub=body.training_rub, unplanned_reserve_pct=d["unplanned_reserve_pct"],
    )
    res = calc_service(inp)
    if "warranty_reserve_pct" in body.model_fields_set:
        pos.warranty_reserve_pct = body.warranty_reserve_pct
    pos.service_calc = {**(pos.service_calc or {}), "input": inp.model_dump(mode="json"), "result": res.model_dump(mode="json"),
                        "show_in_kp": body.show_in_kp}
    if body.save_regulations_to_catalog:
        cat = db.scalar(select(EquipmentModel).where(EquipmentModel.factory_id == pos.factory_id, EquipmentModel.model == pos.model))
        if cat:
            cat.maintenance_regulations = [r.model_dump() for r in body.regulations]
            cat.default_hours_per_month = inp.hours_per_month
    deal.service_head_id = deal.service_head_id or user.id
    audit(db, user.id, deal.id, "position", pos.id, "service_calc", after={"total_rub_part": res.total_rub_part, "total_fx_part": res.total_fx_part.model_dump()})
    db.commit()
    return res.model_dump(mode="json")


@router.post("/complete")
def complete(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(SERVICE)):
    deal = get_deal_or_404(db, deal_id, user)
    missing = [p.designation for p in deal.positions if not (p.service_calc or {}).get("result")]
    if missing:
        raise HTTPException(422, {"message": "Сервис не рассчитан для позиций", "positions": missing})
    st = DealState(status=deal.status, service_flag=deal.service_flag, logistics_done=deal.logistics_done, service_done=deal.service_done)
    tr = block_completed(st, "service", date.today(), sla(db))
    deal.service_done = True
    complete_tasks(db, deal, block="service")
    apply_transition(db, deal, tr, user.id)
    db.commit()
    return {"status": deal.status.value}
