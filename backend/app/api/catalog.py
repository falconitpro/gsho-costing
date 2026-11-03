"""Каталог оборудования (задача 7), типы машин, шаблоны технических характеристик и глоссарий (задача 4), история цен клиентам (задача 8)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import Role
from ..db import get_db
from ..models import EquipmentExecution, EquipmentModel, Factory, FactoryPriceHistory, Glossary, MachineType, TechParamTemplate
from ..services import catalog_service as cs, tech_params_service as tps
from ..services.deal_service import audit
from .common import get_deal_or_404, serialize

router = APIRouter(prefix="/api", tags=["catalog"])


def _catalog_editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.can("catalog", 2):
        raise HTTPException(403, "Каталог правят КД и ЗГД")
    return user


# ------------------------------------------------------------------ дерево и модели

@router.get("/catalog/tree")
def catalog_tree(include_pending: bool = False, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return cs.tree(db, include_pending=include_pending and user.can("catalog", 2))


@router.get("/catalog/executions/{exec_id}")
def execution(exec_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ex = db.get(EquipmentExecution, exec_id)
    if ex is None:
        raise HTTPException(404)
    d = serialize(ex)
    m = db.get(EquipmentModel, ex.model_id)
    d["model"] = serialize(m) if m else None
    if user.can("factory_price_history"):
        d["factory_prices"] = [serialize(h) for h in db.scalars(select(FactoryPriceHistory).where(FactoryPriceHistory.factory_id == m.factory_id, FactoryPriceHistory.model == m.model)
                                                                 .order_by(FactoryPriceHistory.on.desc()).limit(30)).all()] if m else []
    if user.can("client_price_history") and m:
        from ..models import ClientPriceHistory
        d["client_prices"] = [serialize(h) for h in db.scalars(select(ClientPriceHistory).where(ClientPriceHistory.model == m.model).order_by(ClientPriceHistory.on.desc()).limit(30)).all()]
    return d


class ExecutionDecision(BaseModel):
    approve: bool = True
    name: Optional[str] = None
    brand_name: Optional[str] = None


@router.post("/catalog/executions/{exec_id}/decision")
def execution_decision(exec_id: str, body: ExecutionDecision, db: Session = Depends(get_db), user: CurrentUser = Depends(_catalog_editor)):
    """КД или ЗГД подтверждает (или отклоняет) занесение исполнения в каталог; подтверждение одного закрывает задачу для обоих."""
    ex = db.get(EquipmentExecution, exec_id)
    if ex is None:
        raise HTTPException(404)
    cs.approve_execution(db, ex, user.id, body.approve, body.name, body.brand_name)
    audit(db, user.id, ex.source_deal_id, "equipment_execution", ex.id, "approve" if body.approve else "reject", after=body.model_dump())
    db.commit()
    return serialize(ex)


class ExecutionPatch(BaseModel):
    name: Optional[str] = None
    brand_name: Optional[str] = None
    tech_params: Optional[list[dict]] = None
    status: Optional[str] = None


@router.patch("/catalog/executions/{exec_id}")
def execution_patch(exec_id: str, body: ExecutionPatch, db: Session = Depends(get_db), user: CurrentUser = Depends(_catalog_editor)):
    ex = db.get(EquipmentExecution, exec_id)
    if ex is None:
        raise HTTPException(404)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(ex, k, v)
    if body.tech_params is not None:
        ex.fingerprint = tps.fingerprint(body.tech_params)
    audit(db, user.id, None, "equipment_execution", ex.id, "update", after=body.model_dump(exclude_none=True))
    db.commit()
    return serialize(ex)


class ModelPatch(BaseModel):
    brand_name: Optional[str] = None
    trademark: Optional[str] = None
    machine_type_id: Optional[str] = None
    status: Optional[str] = None
    names: Optional[dict] = None


@router.patch("/catalog/models/{model_id}")
def model_patch(model_id: str, body: ModelPatch, db: Session = Depends(get_db), user: CurrentUser = Depends(_catalog_editor)):
    """Собственное наименование под брендом компании (задача 9), тип машины, статус модели."""
    m = db.get(EquipmentModel, model_id)
    if m is None:
        raise HTTPException(404)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(m, k, v)
    if body.machine_type_id:
        mt = db.get(MachineType, body.machine_type_id)
        if mt:
            m.machine_type = mt.name
    if body.trademark:
        cs.remember_trademark(db.get(Factory, m.factory_id), body.trademark)
    audit(db, user.id, None, "equipment_model", m.id, "update", after=body.model_dump(exclude_none=True))
    db.commit()
    return serialize(m)


class MergeIn(BaseModel):
    into_id: str


@router.post("/catalog/executions/{exec_id}/merge")
def execution_merge(exec_id: str, body: MergeIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_catalog_editor)):
    """Объединение дублей исполнений: ссылки позиций и истории цен переносятся в целевое исполнение, дубль — в архив."""
    from ..models import ClientPriceHistory, Position
    src, dst = db.get(EquipmentExecution, exec_id), db.get(EquipmentExecution, body.into_id)
    if src is None or dst is None or src.id == dst.id:
        raise HTTPException(404 if src is None or dst is None else 422, "Исполнение не найдено" if src is None or dst is None else "Нельзя объединить с самим собой")
    moved = 0
    for model in (Position, FactoryPriceHistory, ClientPriceHistory):
        for row in db.scalars(select(model).where(model.execution_id == src.id)).all():
            row.execution_id, moved = dst.id, moved + 1
    if not dst.brand_name and src.brand_name:
        dst.brand_name = src.brand_name
    src.status = "archived"
    audit(db, user.id, None, "equipment_execution", src.id, "merge", after={"into": dst.id, "moved_refs": moved})
    db.commit()
    return {"merged": src.id, "into": dst.id, "moved_refs": moved}


@router.post("/catalog/models/{model_id}/merge")
def model_merge(model_id: str, body: MergeIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_catalog_editor)):
    """Объединение дублей моделей (например, «XDR-80T» и «XDR80T»): исполнения и история цен завода переходят к целевой модели."""
    from ..models import ClientPriceHistory
    src, dst = db.get(EquipmentModel, model_id), db.get(EquipmentModel, body.into_id)
    if src is None or dst is None or src.id == dst.id:
        raise HTTPException(404 if src is None or dst is None else 422, "Модель не найдена" if src is None or dst is None else "Нельзя объединить с самой собой")
    ex_moved = 0
    for ex in db.scalars(select(EquipmentExecution).where(EquipmentExecution.model_id == src.id)).all():
        ex.model_id, ex_moved = dst.id, ex_moved + 1
    hist = 0
    for h in db.scalars(select(FactoryPriceHistory).where(FactoryPriceHistory.factory_id == src.factory_id, FactoryPriceHistory.model == src.model)).all():
        h.factory_id, h.model, hist = dst.factory_id, dst.model, hist + 1
    for h in db.scalars(select(ClientPriceHistory).where(ClientPriceHistory.factory_id == src.factory_id, ClientPriceHistory.model == src.model)).all():
        h.model = dst.model
    if not dst.brand_name and src.brand_name:
        dst.brand_name = src.brand_name
    src.status = "archived"
    audit(db, user.id, None, "equipment_model", src.id, "merge", after={"into": dst.id, "executions": ex_moved, "factory_prices": hist})
    db.commit()
    return {"merged": src.id, "into": dst.id, "executions": ex_moved, "factory_prices": hist}


@router.get("/catalog/archive")
def catalog_archive(db: Session = Depends(get_db), user: CurrentUser = Depends(_catalog_editor)):
    """Архив каталога (объединённые и архивированные модели/исполнения) — для восстановления."""
    return {"models": [serialize(m) for m in db.scalars(select(EquipmentModel).where(EquipmentModel.status == "archived")).all()],
            "executions": [serialize(e) for e in db.scalars(select(EquipmentExecution).where(EquipmentExecution.status == "archived")).all()]}


@router.get("/catalog/price-reference")
def price_reference(factory_id: str, model: str, execution_id: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Справка при вводе позиции: последние цены и сроки производства завода (менеджер, КД, ЗГД) и цены клиентам (КД, ЗГД)."""
    from ..models import ClientPriceHistory
    out: dict = {"factory_prices": None, "client_prices": None}
    if user.can("factory_price_history"):
        q = select(FactoryPriceHistory).where(FactoryPriceHistory.factory_id == factory_id, FactoryPriceHistory.model == model)
        out["factory_prices"] = [{"on": h.on, "unit_price": h.unit_price, "currency": h.currency, "basis": h.basis, "production_term": h.production_term,
                                  "source": h.source, "deal_id": h.deal_id}
                                 for h in db.scalars(q.order_by(FactoryPriceHistory.on.desc()).limit(5)).all()]
    if user.can("client_price_history"):
        q = select(ClientPriceHistory).where(ClientPriceHistory.model == model)
        if execution_id:
            q = q.where(ClientPriceHistory.execution_id == execution_id)
        out["client_prices"] = [{"on": h.on, "client_id": h.client_id, "deal_number": h.deal_number, "unit_price_rub": h.unit_price_rub, "qty": h.qty,
                                 "outcome": h.outcome, "kp_brand": h.kp_brand}
                                for h in db.scalars(q.order_by(ClientPriceHistory.on.desc()).limit(5)).all()]
    return out


# ------------------------------------------------------------------ добавление оборудования прямо в каталог

def _catalog_contributor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(Role.MANAGER, Role.CD, Role.GD):
        raise HTTPException(403, "Добавлять оборудование в каталог могут менеджер, КД и ЗГД")
    return user


class EquipmentIn(BaseModel):
    factory_id: Optional[str] = None
    factory_name: str = ""
    factory_address: str = ""
    machine_type: str = ""
    trademark: str = ""
    model: str
    execution_name: str = ""
    brand_name: str = ""
    tech_params: list[dict] = Field(default_factory=list)
    logistics_params: dict = Field(default_factory=dict)
    unit_price: Optional[float] = None
    currency: str = "CNY"
    basis: str = ""
    production_term: dict = Field(default_factory=dict)
    comment: str = ""


def _added(ex: EquipmentExecution, created: bool, editor: bool) -> dict:
    return {**serialize(ex), "created": created,
            "message": ("Добавлено в каталог" if editor else "Отправлено на подтверждение КД/ЗГД") if created else "Такое исполнение уже есть в каталоге — добавлена запись истории цен"}


@router.post("/catalog/equipment", status_code=201)
def catalog_add(body: EquipmentIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_catalog_contributor)):
    """Добавить оборудование вручную: завод → тип → модель → исполнение с техническими характеристиками и ценой.
    Менеджер — на подтверждение КД/ЗГД; КД и ЗГД — сразу в каталог."""
    editor = user.can("catalog", 2)
    try:
        ex, created = cs.add_equipment(db, body.model_dump(), user, editor)
    except ValueError as e:
        raise HTTPException(422, str(e))
    db.commit()
    return _added(ex, created, editor)


def _tmp_upload(file: UploadFile) -> str:
    import os
    import shutil
    import tempfile
    d = tempfile.mkdtemp()
    path = os.path.join(d, file.filename or "table.xlsx")
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return path


@router.post("/catalog/equipment/import/preview")
async def catalog_import_preview(file: UploadFile = File(...), db: Session = Depends(get_db), user: CurrentUser = Depends(_catalog_contributor)):
    """Предпросмотр файла: опросник RFQ одной машины (kind=rfq — данные для формы добавления) или список моделей (kind=list)."""
    import os
    import shutil
    from ..services.rfq_import import parse_rfq
    path = _tmp_upload(file)
    try:
        parsed = None
        if (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
            try:
                parsed = parse_rfq(path).apply_l_codes(tps.l_codes(db))
            except Exception:
                parsed = None
        if parsed is not None and len(parsed.fields) >= 20 and parsed.position.get("model"):
            pos = parsed.position
            rows = tps.rfq_rows(db, parsed, pos.get("machine_type") or "")
            return {"kind": "rfq", "item": {
                "factory_name": parsed.header.get("factory_name") or "", "factory_address": parsed.header.get("factory_address") or "",
                "machine_type": pos.get("machine_type") or "", "model": str(pos.get("model") or ""), "unit_price": pos.get("unit_price"),
                "currency": pos.get("currency") or "CNY", "basis": pos.get("basis") or "",
                "production_term": {"value": int(pos["production_days"]), "unit": "calendar_days"} if pos.get("production_days") else {},
                "tech_params": rows, "logistics_params": parsed.logistics_params},
                "messages": [{"severity": e.severity, "message": e.message} for e in parsed.errors],
                "filled": sum(1 for r in rows if r["value_ru"]), "total": len(rows)}
        sheets = tps.read_table(path, file.filename or "")
        return {"kind": "list", "sheets": [{"name": sh["name"], "rows": sh["rows"][:200], "total_rows": len(sh["rows"]),
                                            "mapping": cs.guess_list_mapping(sh["rows"])} for sh in sheets],
                "columns": [{"key": k, "title": t} for k, t in (("factory", "Завод"), ("machine_type", "Тип машины"), ("model", "Модель"),
                            ("execution", "Исполнение"), ("brand_name", "Наименование под брендом"), ("unit_price", "Цена"),
                            ("currency", "Валюта"), ("basis", "Базис"), ("production_days", "Срок производства, дн"))]}
    except Exception as e:
        raise HTTPException(422, f"Не удалось прочитать файл: {e}")
    finally:
        shutil.rmtree(os.path.dirname(path), ignore_errors=True)


@router.post("/catalog/equipment/import/apply")
async def catalog_import_apply(file: UploadFile = File(...), mapping: str = Form(...), default_factory_id: str = Form(""),
                               db: Session = Depends(get_db), user: CurrentUser = Depends(_catalog_contributor)):
    """Список моделей из таблицы: по строке — модель/исполнение каталога (и цена в истории цен завода, если указана)."""
    import json
    import os
    import shutil
    editor = user.can("catalog", 2)
    mp = json.loads(mapping)
    cols = {k: int(v) for k, v in (mp.get("columns") or {}).items() if v not in (None, "")}
    if "model" not in cols:
        raise HTTPException(422, "Укажите столбец «Модель»")
    if "factory" not in cols and not default_factory_id:
        raise HTTPException(422, "Укажите столбец «Завод» или выберите завод для всего списка")
    path = _tmp_upload(file)
    try:
        sheets = tps.read_table(path, file.filename or "")
        sheet = next((x for x in sheets if x["name"] == mp.get("sheet")), sheets[0])
        sel = set(mp.get("selected_rows") or []) or None
        added, existing, errors = [], 0, []
        for i in range(int(mp.get("first_row") or 0), len(sheet["rows"])):
            if sel is not None and i not in sel:
                continue
            row = sheet["rows"][i]
            cell = lambda k: (str(row[cols[k]]).strip() if k in cols and cols[k] < len(row) and row[cols[k]] not in (None, "") else "")
            if not cell("model"):
                continue
            try:
                ex, created = cs.add_equipment(db, {"factory_id": None if cell("factory") else default_factory_id, "factory_name": cell("factory"),
                                                    "machine_type": cell("machine_type"), "model": cell("model"), "trademark": cell("trademark"), "execution_name": cell("execution"),
                                                    "brand_name": cell("brand_name"), "unit_price": cell("unit_price") or None,
                                                    "currency": cell("currency") or "CNY", "basis": cell("basis"),
                                                    "production_days": cell("production_days") or None}, user, editor)
                if created:
                    added.append({"id": ex.id, "model": cell("model"), "status": ex.status})
                else:
                    existing += 1
            except ValueError as e:
                errors.append({"row": i, "message": str(e)})
        db.commit()
        return {"added": added, "existing": existing, "errors": errors,
                "message": f"Добавлено {len(added)}" + ("" if editor else " (на подтверждение КД/ЗГД)") + (f", уже были в каталоге: {existing}" if existing else "")}
    finally:
        shutil.rmtree(os.path.dirname(path), ignore_errors=True)


# ------------------------------------------------------------------ типы машин

class MachineTypeIn(BaseModel):
    name: str
    name_zh: str = ""


@router.get("/refs/machine-types")
def machine_types(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return [serialize(t) for t in db.scalars(select(MachineType).order_by(MachineType.name)).all()]


@router.post("/refs/machine-types", status_code=201)
def create_machine_type(body: MachineTypeIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Тип заводится по ходу работы: КД/ЗГД — сразу подтверждённый, менеджер — на подтверждение."""
    if db.scalar(select(MachineType).where(MachineType.name == body.name)):
        raise HTTPException(409, "Такой тип уже есть")
    t = MachineType(name=body.name, name_zh=body.name_zh, status="approved" if user.can("catalog", 2) else "pending", proposed_by=user.id)
    db.add(t)
    db.commit()
    return serialize(t)


@router.post("/refs/machine-types/{type_id}/approve")
def approve_machine_type(type_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_catalog_editor)):
    t = db.get(MachineType, type_id)
    if t is None:
        raise HTTPException(404)
    t.status = "approved"
    db.commit()
    return serialize(t)


# ------------------------------------------------------------------ шаблоны технических характеристик (обязательность задают КД/ЗГД)

class TemplateRowIn(BaseModel):
    id: Optional[str] = None
    no: int
    code: str = ""
    name_ru: str
    name_zh: str = ""
    unit: str = ""
    required: bool = False
    section: str = ""


@router.get("/refs/tech-param-templates")
def templates(machine_type_id: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if machine_type_id:
        rows = db.scalars(select(TechParamTemplate).where(TechParamTemplate.machine_type_id == machine_type_id).order_by(TechParamTemplate.no)).all()
        if rows:
            return {"machine_type_id": machine_type_id, "rows": [serialize(r) for r in rows], "inherited": False}
    rows = tps.ensure_general_template(db)
    db.commit()
    return {"machine_type_id": None, "rows": [serialize(r) for r in rows], "inherited": bool(machine_type_id)}


@router.put("/refs/tech-param-templates")
def save_template(rows: list[TemplateRowIn], machine_type_id: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(_catalog_editor)):
    mt_id = machine_type_id or None
    existing = db.scalars(select(TechParamTemplate).where(TechParamTemplate.machine_type_id == mt_id)).all()
    by_id = {r.id: r for r in existing}
    keep = set()
    for r in rows:
        row = by_id.get(r.id) if r.id else None
        if row is None:
            row = TechParamTemplate(machine_type_id=mt_id, **r.model_dump(exclude={"id"}))
            db.add(row)
            db.flush()
        else:
            for k, v in r.model_dump(exclude={"id"}).items():
                setattr(row, k, v)
        keep.add(row.id)
    for row in existing:
        if row.id not in keep:
            db.delete(row)
    audit(db, user.id, None, "tech_param_template", mt_id or "general", "save", after={"rows": len(rows)})
    db.commit()
    return {"rows": len(rows)}


# ------------------------------------------------------------------ глоссарий

class GlossaryIn(BaseModel):
    term_ru: str
    term_zh: str
    kind: str = "param"
    status: str = "confirmed"


@router.get("/refs/glossary")
def glossary(q: str = "", status: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if not user.can("glossary"):
        raise HTTPException(403)
    stmt = select(Glossary).order_by(Glossary.status.desc(), Glossary.term_ru)
    if q:
        stmt = stmt.where((Glossary.term_ru.ilike(f"%{q}%")) | (Glossary.term_zh.ilike(f"%{q}%")))
    if status:
        stmt = stmt.where(Glossary.status == status)
    return [serialize(g) for g in db.scalars(stmt.limit(500)).all()]


@router.post("/refs/glossary", status_code=201)
def glossary_upsert(body: GlossaryIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if not user.can("glossary", 2):
        raise HTTPException(403)
    g = db.scalar(select(Glossary).where(Glossary.term_ru == body.term_ru, Glossary.kind == body.kind))
    if g is None:
        g = Glossary(**body.model_dump(), changed_by=user.id)
        db.add(g)
    else:
        g.term_zh, g.status, g.changed_by = body.term_zh, body.status, user.id
    db.commit()
    return serialize(g)


@router.post("/refs/glossary/{gid}/confirm")
def glossary_confirm(gid: str, term_zh: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Подтверждение машинного перевода человеком — после этого термин используется при переводе."""
    if not user.can("glossary", 2):
        raise HTTPException(403)
    g = db.get(Glossary, gid)
    if g is None:
        raise HTTPException(404)
    if term_zh:
        g.term_zh = term_zh
    g.status, g.changed_by = "confirmed", user.id
    db.commit()
    return serialize(g)


@router.delete("/refs/glossary/{gid}", status_code=204)
def glossary_delete(gid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if not user.can("glossary", 2):
        raise HTTPException(403)
    g = db.get(Glossary, gid)
    if g:
        db.delete(g)
        db.commit()


# ------------------------------------------------------------------ пополнение каталога из КП (сделка)

@router.get("/deals/{deal_id}/catalog/candidates")
def candidates(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    return cs.catalog_candidates(db, deal)


class ProposeIn(BaseModel):
    position_ids: list[str]
    names: dict[str, str] = Field(default_factory=dict)


@router.post("/deals/{deal_id}/catalog/propose")
def propose(deal_id: str, body: ProposeIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Менеджер подтверждает состав для занесения → исполнения «на проверке» и задача КД/ЗГД."""
    deal = get_deal_or_404(db, deal_id, user)
    if not (user.has(Role.GD, Role.CD) or (user.has(Role.MANAGER) and deal.manager_id == user.id)):
        raise HTTPException(403)
    created = cs.propose(db, deal, body.position_ids, body.names, user.id)
    audit(db, user.id, deal.id, "catalog", deal.id, "propose", after={"executions": [e.id for e in created]})
    db.commit()
    return {"created": [serialize(e) for e in created]}


@router.get("/catalog/pending")
def pending(db: Session = Depends(get_db), user: CurrentUser = Depends(_catalog_editor)):
    """Очередь на подтверждение для КД/ЗГД."""
    rows = db.scalars(select(EquipmentExecution).where(EquipmentExecution.status == "pending").order_by(EquipmentExecution.created_at)).all()
    out = []
    for e in rows:
        m = db.get(EquipmentModel, e.model_id)
        out.append({**serialize(e), "model": m.model if m else "", "factory_id": m.factory_id if m else None})
    return out
