"""Каталог оборудования (задача 7): производитель → тип машины → модель → исполнение; пополнение из КП с двойным
подтверждением (менеджер → КД/ЗГД); история цен завода и цен клиентам (задачи 7, 8)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.roles import Role
from ..core.workflow import add_business_days
from ..models import (ClientPriceHistory, Deal, DealVersion, EquipmentExecution, EquipmentModel, Factory, MachineType, Position, Task, User,
                      default_trademark)
from .deal_service import audit, notify
from .tech_params_service import fingerprint, row_dicts


# ------------------------------------------------------------------ торговые марки: сопоставление «завод → марки»

def remember_trademark(factory: Optional[Factory], trademark: str) -> None:
    """Новая марка завода попадает в его список марок (сопоставление пополняется само)."""
    tm = (trademark or "").strip()
    if factory is None or not tm:
        return
    marks = list(factory.trademarks or [])
    if tm.lower() not in {m.lower() for m in marks}:
        factory.trademarks = marks + [tm]


def resolve_trademark(db: Session, factory_id: Optional[str], model: str = "", given: str = "") -> str:
    """Торговая марка позиции: указанная явно → марка модели в каталоге → первая марка завода."""
    factory = db.get(Factory, factory_id) if factory_id else None
    if (given or "").strip():
        remember_trademark(factory, given)
        return given.strip()
    if factory_id and model:
        m = db.scalar(select(EquipmentModel).where(EquipmentModel.factory_id == factory_id, EquipmentModel.model == model))
        if m is not None and m.trademark:
            return m.trademark
    return default_trademark(factory)


# ------------------------------------------------------------------ история цен клиентам (задачи 7, 8)

def record_client_prices(db: Session, deal: Deal, user_id: Optional[str]) -> None:
    """При отправке КП: по каждому предложению и позиции — цена клиенту, условия, срок, бренд подачи, версия КП."""
    ver = db.get(DealVersion, deal.approved_version_id) if deal.approved_version_id else None
    res = (ver.calc_result if ver else {}) or {}
    factories = {f.id: f.name for f in db.scalars(select(Factory)).all()}
    for v in deal.variants:
        if not v.in_kp or v.price_rub is None:
            continue
        poss = [p for p in deal.positions if p.id in v.position_ids]
        total_qty = sum(p.qty for p in poss) or 1
        base = ((res.get("variants") or {}).get(v.id) or {}).get("base") or {}
        pos_cost = {pc["position_id"]: pc for pc in base.get("positions", [])}
        for p in poss:
            pc = pos_cost.get(p.id, {})
            share = pc.get("price_rub") if pc.get("price_rub") is not None else v.price_rub * p.qty / total_qty
            term = ((pc.get("delivery_term") or {}).get("text")) or f"{base.get('delivery_days', '')} дн."
            db.add(ClientPriceHistory(
                client_id=deal.client_id, deal_id=deal.id, deal_number=deal.number, version_id=ver.id if ver else None,
                version_no=ver.version_no if ver else deal.current_version_no, kp_no=f"КП-{deal.number[2:]}", variant_id=v.id,
                variant_name="" if v.is_default else v.name, factory_id=p.factory_id, factory_name=factories.get(p.factory_id, ""),
                model=p.model, execution_id=p.execution_id, qty=p.qty, unit_price_rub=round(share / max(p.qty, 1), 2), total_rub=round(share, 2),
                payment_terms=v.client_payments or deal.client_payments or [], delivery_term=term, kp_brand=deal.kp_brand or "factory",
                kp_path=ver.kp_path if ver else "", manager_id=deal.manager_id, on=date.today(), outcome=deal.outcome,
            ))


def update_client_price_outcome(db: Session, deal: Deal) -> None:
    for h in db.scalars(select(ClientPriceHistory).where(ClientPriceHistory.deal_id == deal.id)).all():
        h.outcome = deal.outcome
        if deal.won_variant_id and h.variant_id:
            h.chosen = h.variant_id == deal.won_variant_id


def client_kp_history(db: Session, client_id: str, manager_only: Optional[str] = None) -> list[dict]:
    """История всех КП клиента со всеми версиями (задача 8): группировка по сделке и предложению."""
    q = select(ClientPriceHistory).where(ClientPriceHistory.client_id == client_id).order_by(ClientPriceHistory.on.desc(), ClientPriceHistory.version_no.desc())
    rows = db.scalars(q).all()
    if manager_only:
        rows = [r for r in rows if r.manager_id == manager_only]
    users = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    out: dict[tuple, dict] = {}
    for r in rows:
        key = (r.deal_id, r.variant_id, r.model)
        entry = {"version_no": r.version_no, "on": r.on.isoformat(), "unit_price_rub": r.unit_price_rub, "total_rub": r.total_rub,
                 "payment_terms": r.payment_terms, "delivery_term": r.delivery_term, "kp_brand": r.kp_brand, "kp_path": r.kp_path, "kp_no": r.kp_no}
        if key not in out:
            out[key] = {"deal_id": r.deal_id, "deal_number": r.deal_number, "variant": r.variant_name, "factory": r.factory_name, "model": r.model,
                        "qty": r.qty, "outcome": r.outcome, "chosen": r.chosen, "manager": users.get(r.manager_id or "", ""), "latest": entry, "versions": []}
        out[key]["versions"].append(entry)
    return list(out.values())


# ------------------------------------------------------------------ пополнение каталога (двойное подтверждение)

def propose_catalog_task(db: Session, deal: Deal, user_id: Optional[str]) -> None:
    """После отправки КП — задача менеджеру отметить, что занести в каталог (новые модели / исполнения)."""
    if not deal.positions:
        return
    if db.scalar(select(Task.id).where(Task.deal_id == deal.id, Task.block == "catalog", Task.done_at.is_(None))):
        return
    db.add(Task(deal_id=deal.id, role=Role.MANAGER.value, assignee_id=deal.manager_id, title="Занести оборудование из КП в каталог (подтвердить состав)",
                block="catalog", due=add_business_days(date.today(), 3)))


def catalog_candidates(db: Session, deal: Deal) -> list[dict]:
    """Что предлагается занести: по каждой позиции — новая модель или новое исполнение (или совпадение с существующим)."""
    out = []
    for p in deal.positions:
        model = db.scalar(select(EquipmentModel).where(EquipmentModel.factory_id == p.factory_id, EquipmentModel.model == p.model))
        rows = row_dicts(p)
        fp = fingerprint(rows)
        existing = None
        if model:
            existing = db.scalar(select(EquipmentExecution).where(EquipmentExecution.model_id == model.id, EquipmentExecution.fingerprint == fp,
                                                                   EquipmentExecution.status != "archived"))
        pending = db.scalar(select(EquipmentExecution).where(EquipmentExecution.source_position_id == p.id, EquipmentExecution.status.in_(["pending_manager", "pending"])))
        out.append({"position_id": p.id, "model": p.designation, "machine_type": p.machine_type, "factory_id": p.factory_id, "model_exists": model is not None,
                    "model_status": model.status if model else None, "execution_exists": existing is not None,
                    "existing_execution": {"id": existing.id, "name": existing.name, "status": existing.status} if existing else None,
                    "pending_execution_id": pending.id if pending else None, "params_filled": sum(1 for r in rows if r.get("value_ru")), "params_total": len(rows)})
    return out


def propose(db: Session, deal: Deal, position_ids: list[str], names: dict[str, str], user_id: str) -> list[EquipmentExecution]:
    """Менеджер отметил позиции → исполнения «на проверке» и задача КД (видна и ЗГД) «Подтвердить занесение в каталог»."""
    created: list[EquipmentExecution] = []
    for p in deal.positions:
        if p.id not in position_ids:
            continue
        model = db.scalar(select(EquipmentModel).where(EquipmentModel.factory_id == p.factory_id, EquipmentModel.model == p.model))
        if model is None:
            mt = db.scalar(select(MachineType).where(MachineType.name == p.machine_type)) if p.machine_type else None
            if mt is None and p.machine_type:
                mt = MachineType(name=p.machine_type, status="pending", proposed_by=user_id)
                db.add(mt)
                db.flush()
            model = EquipmentModel(factory_id=p.factory_id, machine_type=p.machine_type or "", machine_type_id=mt.id if mt else None, model=p.model,
                                   trademark=p.trademark or "", tech_params=p.tech_params or {}, logistics_params=p.logistics_params or {},
                                   status="pending", proposed_by=user_id)
            db.add(model)
            db.flush()
        rows = [{k: r.get(k) for k in ("no", "code", "name_ru", "name_zh", "unit", "value_ru", "value_zh", "required", "local_ru")} for r in row_dicts(p)]
        fp = fingerprint(rows)
        dup = db.scalar(select(EquipmentExecution).where(EquipmentExecution.model_id == model.id, EquipmentExecution.fingerprint == fp,
                                                          EquipmentExecution.status != "archived"))
        if dup:
            if dup.source_deal_id != deal.id:
                audit(db, user_id, deal.id, "equipment_execution", dup.id, "history", after={"deal": deal.number})
            continue
        ex = EquipmentExecution(model_id=model.id, name=names.get(p.id) or f"{p.model} — {deal.number}", tech_params=rows,
                               local_options=[{"name": o.name, "qty": o.qty, "install_stage": o.install_stage, "cost": o.cost, "currency": o.currency}
                                              for o in (p.local_option_rows or [])],
                               images=[{"attachment_id": i.attachment_id, "caption": i.caption, "kind": i.kind, "source": i.source} for i in (p.image_rows or [])],
                               fingerprint=fp, status="pending", source_deal_id=deal.id, source_position_id=p.id, proposed_by=user_id)
        db.add(ex)
        created.append(ex)
    db.flush()
    if created:
        task = Task(deal_id=deal.id, role=Role.CD.value, assignee_id=None, title=f"Подтвердить занесение в каталог: {', '.join(e.name for e in created)}",
                    block="catalog_approve", due=add_business_days(date.today(), 2))
        db.add(task)
        for u in db.scalars(select(User).where(User.is_active.is_(True))).all():
            if "cd" in (u.roles or []) or "gd" in (u.roles or []):
                notify(db, deal, u.id, "task_new", ["app"], f"{deal.number}: подтвердить занесение в каталог")
    for t in db.scalars(select(Task).where(Task.deal_id == deal.id, Task.block == "catalog", Task.done_at.is_(None))).all():
        t.done_at = datetime.now(timezone.utc)
    return created


def approve_execution(db: Session, ex: EquipmentExecution, user_id: str, approve: bool, name: Optional[str] = None, brand_name: Optional[str] = None) -> None:
    ex.status = "approved" if approve else "archived"
    ex.approved_by, ex.approved_at = user_id, datetime.now(timezone.utc)
    if name:
        ex.name = name
    if brand_name is not None:
        ex.brand_name = brand_name
    model = db.get(EquipmentModel, ex.model_id)
    if model and approve and model.status == "pending":
        model.status, model.approved_by = "approved", user_id
        mt = db.get(MachineType, model.machine_type_id) if model.machine_type_id else None
        if mt and mt.status == "pending":
            mt.status = "approved"
    if approve and ex.source_position_id:   # позиция-источник привязывается к исполнению: бренд компании, история цен
        src = db.get(Position, ex.source_position_id)
        if src and not src.execution_id:
            src.execution_id = ex.id
    if ex.source_deal_id:
        for t in db.scalars(select(Task).where(Task.deal_id == ex.source_deal_id, Task.block == "catalog_approve", Task.done_at.is_(None))).all():
            still = db.scalar(select(EquipmentExecution.id).where(EquipmentExecution.source_deal_id == ex.source_deal_id, EquipmentExecution.status == "pending",
                                                                  EquipmentExecution.id != ex.id))
            if not still:
                t.done_at = datetime.now(timezone.utc)


def tree(db: Session, include_pending: bool = False) -> list[dict]:
    """Производитель → тип → модель → исполнения (для выбора в позиции и экрана каталога)."""
    types = {t.id: t for t in db.scalars(select(MachineType)).all()}
    execs: dict[str, list[EquipmentExecution]] = {}
    for e in db.scalars(select(EquipmentExecution).order_by(EquipmentExecution.name)).all():
        if e.status == "approved" or (include_pending and e.status != "archived"):
            execs.setdefault(e.model_id, []).append(e)
    out = []
    for f in db.scalars(select(Factory).order_by(Factory.name)).all():
        models = db.scalars(select(EquipmentModel).where(EquipmentModel.factory_id == f.id).order_by(EquipmentModel.model)).all()
        by_type: dict[str, dict] = {}
        for m in models:
            if m.status == "archived" or (m.status == "pending" and not include_pending):
                continue
            tname = (types[m.machine_type_id].name if m.machine_type_id in types else m.machine_type) or "—"
            by_type.setdefault(tname, {"machine_type": tname, "machine_type_id": m.machine_type_id, "models": []})["models"].append({
                "id": m.id, "model": m.model, "trademark": m.trademark or default_trademark(f), "brand_name": m.brand_name, "status": m.status,
                "executions": [{"id": e.id, "name": e.name, "brand_name": e.brand_name, "status": e.status, "params": len(e.tech_params or []),
                                "images": len(e.images or []), "source_deal_id": e.source_deal_id} for e in execs.get(m.id, [])]})
        if by_type:
            out.append({"factory_id": f.id, "factory": f.name, "trademarks": f.trademarks or [], "types": list(by_type.values())})
    return out


# ------------------------------------------------------------------ добавление оборудования прямо в каталог

LIST_ALIASES = {
    "factory": ("завод", "производитель", "изготовитель", "manufacturer", "factory", "工厂", "制造商"),
    "machine_type": ("тип машины", "тип", "type", "设备类型", "类型"),
    "model": ("модель", "model", "型号"),
    "trademark": ("торговая марка", "марка", "бренд", "brand", "trademark", "品牌", "商标"),
    "execution": ("исполнение", "комплектация", "вариант", "configuration", "配置"),
    "brand_name": ("наименование под брендом", "под брендом компании"),
    "unit_price": ("цена", "стоимость", "price", "价格", "单价"),
    "currency": ("валюта", "currency", "币种"),
    "basis": ("базис", "incoterms", "условия поставки"),
    "production_days": ("срок производства", "производство, дн", "production", "生产周期"),
}


def guess_list_mapping(rows: list[list]) -> dict:
    """Список моделей (прайс, перечень от завода): строка заголовков и столбцы по названиям."""
    from .tech_params_service import _norm
    best, best_row, best_cols = 0, 0, {}
    for i, row in enumerate(rows[:30]):
        cols: dict[str, int] = {}
        for j, cell in enumerate(row):
            v = _norm(cell)
            if not v:
                continue
            hits = [(len(a), key) for key, aliases in LIST_ALIASES.items() for a in map(_norm, aliases) if v == a or v.startswith(a)]
            for _, key in sorted(hits, reverse=True):
                if key not in cols:
                    cols[key] = j
                    break
        if len(cols) > best:
            best, best_row, best_cols = len(cols), i, cols
    return {"header_row": best_row, "first_row": best_row + 1 if best else 0, "columns": best_cols}


def _num(v) -> Optional[float]:
    from .rfq_import import _parse_number
    return _parse_number(v) if v not in (None, "") else None


def add_equipment(db: Session, data: dict, user, editor: bool) -> tuple[EquipmentExecution, bool]:
    """Новая запись каталога вручную или из файла. Менеджер — «на проверке» (подтверждает КД или ЗГД);
    КД/ЗГД — сразу в каталоге. Совпадающее исполнение (тот же набор параметров) не дублируется.
    data: factory_id | factory_name, machine_type, trademark, model, execution_name, brand_name, tech_params (строки таблицы),
          logistics_params, unit_price, currency, basis, production_term {value, unit}, comment.
    Возвращает (исполнение, создано ли новое)."""
    from ..models import FactoryPriceHistory
    status = "approved" if editor else "pending"
    model_name = (data.get("model") or "").strip()
    if not model_name:
        raise ValueError("Укажите модель")
    factory = db.get(Factory, data["factory_id"]) if data.get("factory_id") else None
    if factory is None:
        fname = (data.get("factory_name") or "").strip()
        if not fname:
            raise ValueError("Укажите завод")
        factory = db.scalar(select(Factory).where(Factory.name == fname))
        if factory is None:
            factory = Factory(name=fname, address=data.get("factory_address") or "")
            db.add(factory)
            db.flush()
    mt_name = (data.get("machine_type") or "").strip()
    mt = db.scalar(select(MachineType).where(MachineType.name == mt_name)) if mt_name else None
    if mt is None and mt_name:
        mt = MachineType(name=mt_name, status=status, proposed_by=user.id)
        db.add(mt)
        db.flush()
    model = db.scalar(select(EquipmentModel).where(EquipmentModel.factory_id == factory.id, EquipmentModel.model == model_name))
    if model is None:
        model = EquipmentModel(factory_id=factory.id, machine_type=mt_name, machine_type_id=mt.id if mt else None, model=model_name,
                               logistics_params=data.get("logistics_params") or {}, status=status, proposed_by=user.id)
        db.add(model)
        db.flush()
    elif model.status == "archived":
        model.status = status
    tm = (data.get("trademark") or "").strip()
    if tm:
        model.trademark = model.trademark or tm
        remember_trademark(factory, tm)
    elif not model.trademark:
        model.trademark = default_trademark(factory)
    if editor and data.get("brand_name"):
        model.brand_name = model.brand_name or data["brand_name"]
    rows = [r for r in (data.get("tech_params") or []) if (r.get("name_ru") or "").strip()]
    for i, r in enumerate(rows, 1):
        r.setdefault("no", i)
    fp = fingerprint(rows)
    ex = db.scalar(select(EquipmentExecution).where(EquipmentExecution.model_id == model.id, EquipmentExecution.fingerprint == fp,
                                                    EquipmentExecution.status != "archived"))
    created = ex is None
    if created:
        ex = EquipmentExecution(model_id=model.id, name=(data.get("execution_name") or "").strip() or f"{model_name} базовое",
                                brand_name=(data.get("brand_name") or "") if editor else "", tech_params=rows, local_options=[], images=[],
                                fingerprint=fp, status=status, proposed_by=user.id)
        if editor:
            ex.approved_by, ex.approved_at = user.id, datetime.now(timezone.utc)
        db.add(ex)
        db.flush()
    price = _num(data.get("unit_price"))
    if price:
        term = data.get("production_term") or {}
        if not term and _num(data.get("production_days")):
            term = {"value": int(_num(data.get("production_days"))), "unit": "calendar_days"}
        db.add(FactoryPriceHistory(factory_id=factory.id, model=model_name, execution_id=ex.id, unit_price=price,
                                   currency=(data.get("currency") or "CNY")[:3].upper(), basis=data.get("basis") or "",
                                   production_term=term, on=date.today(), source="price_list"))
    audit(db, user.id, None, "equipment_execution", ex.id, "catalog_add" if created else "catalog_add_existing",
          after={"factory": factory.name, "trademark": model.trademark, "model": model_name, "status": ex.status, "params": len(rows), "comment": data.get("comment") or ""})
    return ex, created
