"""Администрирование: настройки с историей, пользователи и роли, ТН ВЭД, курсы, шаблоны, журнал."""
from __future__ import annotations

import csv
import io
import os
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import adapters
from ..core.auth import CurrentUser, require_roles
from ..core.roles import Role
from ..db import get_db
from ..models import AuditLog, FxRate, HsCode, IntegrationLog, Template, User
from ..services import settings_service
from ..services.deal_service import audit
from .common import serialize

router = APIRouter(prefix="/api/admin", tags=["admin"])
ADMIN = require_roles(Role.ADMIN, Role.GD)
FD_ADMIN = require_roles(Role.ADMIN, Role.FD, Role.GD)
SETTINGS_READ = require_roles(Role.ADMIN, Role.FD, Role.GD, Role.CD)
# ключи, которые меняет КД (и ЗГД): комиссии внешняя и менеджера
CD_KEYS = {"external_commission_pct", "manager_commission_pct", "door_to_door_border_pct"}

# ключи, которые может менять ФД (ставки стоимости денег, порог маржи, ΔK/Δt, лимиты)
FD_KEYS = {"margin_threshold_pct", "scenario_dk", "scenario_dt", "use_historical_volatility", "vol_multipliers", "fx_weights",
           "fx_history_years", "key_rate_pct", "pboc_rate_pct", "foreign_rates_pct", "fx_expert_points", "financing_rate_pct",
           "deposit_rate_pct", "conversion_fee_pct", "transfer_fee", "guarantee_rate_pct", "cash_gap_limit_rub",
           "overhead_actual_pct_last_year", "warranty_reserve_pct_by_type", "unplanned_reserve_pct"}


@router.get("/settings")
def settings_all(db: Session = Depends(get_db), user: CurrentUser = Depends(SETTINGS_READ)):
    return settings_service.get_all(db)


@router.get("/settings/meta")
def settings_meta(user: CurrentUser = Depends(SETTINGS_READ)):
    """Названия, описания, единицы, группы и типы полей — для понятного экрана «Настройки»; editable — может ли пользователь менять."""
    m = settings_service.meta()
    for k, it in m["items"].items():
        it["editable"] = _can_set(user, k)
        it["editors"] = "КД или ЗГД" if k in CD_KEYS else "ФД, ЗГД" if k in FD_KEYS else "ЗГД"
    return m


def _can_set(user: CurrentUser, key: str) -> bool:
    return user.has(Role.ADMIN, Role.GD) or (key in FD_KEYS and user.has(Role.FD)) or (key in CD_KEYS and user.has(Role.CD))


class SettingIn(BaseModel):
    value: Any
    comment: str = ""


@router.put("/settings/{key}")
def set_setting(key: str, body: SettingIn, db: Session = Depends(get_db), user: CurrentUser = Depends(SETTINGS_READ)):
    if key not in settings_service.DEFAULTS:
        raise HTTPException(404, "Неизвестный ключ")
    if not _can_set(user, key):
        raise HTTPException(403, "Этот параметр меняет " + ("КД или ЗГД" if key in CD_KEYS else "ФД, ЗГД или администратор" if key in FD_KEYS else "ЗГД или администратор"))
    if key == "fx_expert_points":
        body.value = settings_service.expert_points(body.value)
    if key in ("gd_deal_limit_rub", "overhead_scale") and not user.has(Role.GD, Role.ADMIN):
        raise HTTPException(403, "Лимит и шкалу накладных утверждает ЗГД")
    before = settings_service.get(db, key)
    settings_service.set_value(db, key, body.value, user.id, body.comment)
    audit(db, user.id, None, "setting", key, "update", {"value": before}, {"value": body.value}, body.comment)
    db.commit()
    return {"key": key, "value": body.value}


@router.get("/settings/{key}/history")
def setting_history(key: str, db: Session = Depends(get_db), user: CurrentUser = Depends(SETTINGS_READ)):
    return [serialize(r) for r in settings_service.history(db, key)]


# ------------------------------------------------------------------ пользователи

class UserIn(BaseModel):
    email: str
    display_name: str
    roles: list[str]
    external_id: Optional[str] = None
    observer_scope: dict = {}
    visa_limit_rub: Optional[float] = None
    wecom_user_id: Optional[str] = None
    notify_channels: list[str] = ["outlook", "wecom", "app"]
    is_active: bool = True


@router.get("/users")
def users(db: Session = Depends(get_db), user: CurrentUser = Depends(ADMIN)):
    return [serialize(u) for u in db.scalars(select(User).order_by(User.display_name)).all()]


@router.post("/users", status_code=201)
def create_user(body: UserIn, db: Session = Depends(get_db), user: CurrentUser = Depends(ADMIN)):
    try:
        for r in body.roles:
            Role(r)
    except ValueError:
        raise HTTPException(422, "Неизвестная роль")
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(409, "Сотрудник с таким e-mail уже есть")
    u = User(**body.model_dump(exclude={"external_id"}), external_id=body.external_id or body.email)
    db.add(u)
    db.flush()
    audit(db, user.id, None, "user", u.id, "create", after=body.model_dump())
    db.commit()
    return serialize(u)


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: dict, db: Session = Depends(get_db), user: CurrentUser = Depends(ADMIN)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404)
    before = serialize(u)
    if "roles" in body:
        try:
            body["roles"] = [Role(r).value for r in body["roles"]]
        except ValueError:
            raise HTTPException(422, "Неизвестная роль")
        if u.id == user.id and not ({"admin", "gd"} & set(body["roles"])):
            raise HTTPException(409, "Нельзя снять с себя роль администратора / ЗГД")
    if body.get("is_active") is False and u.id == user.id:
        raise HTTPException(409, "Нельзя отключить свою учётную запись")
    for k, v in body.items():
        if k in UserIn.model_fields:
            setattr(u, k, v)
    audit(db, user.id, None, "user", u.id, "update", {k: before.get(k) for k in body}, body)
    db.commit()
    return serialize(u)


# ------------------------------------------------------------------ ТН ВЭД

@router.post("/hs/upload")
async def hs_upload(file: UploadFile = File(...), valid_from: Optional[date] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(ADMIN)):
    """CSV: code;description;duty_pct;vat_pct;util_fee;licence;certification. Старые записи закрываются датой."""
    import re as _re
    valid_from = valid_from or date.today()
    raw = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw), delimiter=";")
    if not reader.fieldnames or "code" not in reader.fieldnames or "description" not in reader.fieldnames:
        raise HTTPException(422, "Нужны столбцы code;description;duty_pct;vat_pct;util_fee;licence;certification (разделитель «;»)")
    yes = ("1", "да", "true", "yes", "+")
    n, errors = 0, []
    for i, row in enumerate(reader, 2):
        code = _re.sub(r"\D", "", row.get("code") or "")
        try:
            duty = float(str(row.get("duty_pct") or 0).replace(",", "."))
            vat = float(str(row.get("vat_pct") or 22).replace(",", "."))
        except ValueError:
            errors.append(f"строка {i}: ставка не число"); continue
        if not code:
            errors.append(f"строка {i}: нет кода"); continue
        for old in db.scalars(select(HsCode).where(HsCode.code == code, HsCode.valid_to.is_(None))).all():
            old.valid_to = valid_from
        db.add(HsCode(code=code, description=row.get("description") or "", duty_pct=duty, vat_pct=vat,
                      util_fee=str(row.get("util_fee") or "").strip().lower() in yes,
                      licence_required=str(row.get("licence") or "").strip().lower() in yes,
                      certification=[c.strip() for c in (row.get("certification") or "").split(",") if c.strip()],
                      valid_from=valid_from, version_file=file.filename or ""))
        n += 1
    audit(db, user.id, None, "hs_codes", (file.filename or "")[:128], "upload", after={"rows": n, "errors": len(errors), "valid_from": str(valid_from)})
    db.commit()
    return {"imported": n, "errors": errors[:50]}


class HsIn(BaseModel):
    code: str
    description: str
    duty_pct: float = 0.0
    vat_pct: float = 22.0
    util_fee: bool = False
    licence_required: bool = False
    certification: list[str] = []
    valid_from: Optional[date] = None


@router.get("/hs")
def hs_list(q: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(ADMIN)):
    """Действующие коды справочника ТН ВЭД (поиск по началу кода или описанию)."""
    qry = select(HsCode).where(HsCode.valid_to.is_(None)).order_by(HsCode.code)
    rows = [h for h in db.scalars(qry).all() if not q or h.code.startswith(q.replace(" ", "")) or q.lower() in (h.description or "").lower()]
    return {"total": len(rows), "rows": [serialize(h) for h in rows[:500]]}


@router.post("/hs", status_code=201)
def hs_upsert(body: HsIn, db: Session = Depends(get_db), user: CurrentUser = Depends(ADMIN)):
    """Новый код или новые ставки по коду: прежняя запись закрывается датой, история сохраняется."""
    import re as _re
    code = _re.sub(r"\D", "", body.code)
    if len(code) not in (4, 6, 8, 10):
        raise HTTPException(422, "Код ТН ВЭД — 4, 6, 8 или 10 цифр")
    vf = body.valid_from or date.today()
    before = None
    for old in db.scalars(select(HsCode).where(HsCode.code == code, HsCode.valid_to.is_(None))).all():
        before = serialize(old)
        old.valid_to = vf
    row = HsCode(code=code, description=body.description, duty_pct=body.duty_pct, vat_pct=body.vat_pct, util_fee=body.util_fee,
                 licence_required=body.licence_required, certification=[c.strip() for c in body.certification if c.strip()], valid_from=vf,
                 version_file="вручную")
    db.add(row)
    audit(db, user.id, None, "hs_codes", code, "upsert", before, body.model_dump(mode="json"))
    db.commit()
    return serialize(row)


@router.get("/hs/export")
def hs_export(db: Session = Depends(get_db), user: CurrentUser = Depends(ADMIN)):
    """Действующий справочник в формате загрузки (CSV, разделитель «;») — образец файла и резервная копия."""
    from fastapi.responses import Response
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["code", "description", "duty_pct", "vat_pct", "util_fee", "licence", "certification"])
    for h in db.scalars(select(HsCode).where(HsCode.valid_to.is_(None)).order_by(HsCode.code)).all():
        w.writerow([h.code, h.description, h.duty_pct, h.vat_pct, "да" if h.util_fee else "нет", "да" if h.licence_required else "нет", ", ".join(h.certification or [])])
    return Response(("\ufeff" + buf.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=hs_codes.csv"})


# ------------------------------------------------------------------ курсы

@router.post("/fx/load")
def fx_load(on: Optional[date] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(FD_ADMIN)):
    """Ручной запуск загрузки курсов ЦБ на дату (ежедневно запускается планировщиком)."""
    on = on or date.today()
    try:
        rates = adapters.fx_source().daily(on)
        for cur, rate in rates.items():
            if cur == "RUB":
                continue
            if not db.scalar(select(FxRate).where(FxRate.on == on, FxRate.currency == cur, FxRate.source == "cbr")):
                db.add(FxRate(on=on, currency=cur, rate=rate, source="cbr"))
        db.add(IntegrationLog(system="cbr", operation="daily", ok=True, details={"on": str(on), "rates": rates}))
        db.commit()
        return {"on": str(on), "rates": rates}
    except Exception as e:
        db.add(IntegrationLog(system="cbr", operation="daily", ok=False, details={"on": str(on), "error": str(e)[:300]}))
        db.commit()
        raise HTTPException(502, f"Сервис ЦБ недоступен: {e}")


@router.post("/fx/history")
def fx_history_load(currency: str = "CNY", start: date = date(2014, 1, 1), db: Session = Depends(get_db), user: CurrentUser = Depends(FD_ADMIN)):
    pts = adapters.fx_source().history(currency, start, date.today())
    existing = {r.on for r in db.scalars(select(FxRate).where(FxRate.currency == currency, FxRate.source == "cbr")).all()}
    n = 0
    for d, r in pts:
        if d not in existing:
            db.add(FxRate(on=d, currency=currency, rate=r, source="cbr"))
            n += 1
    db.commit()
    return {"loaded": n}


class ManualFxIn(BaseModel):
    on: date
    currency: str
    rate: float
    comment: str


@router.post("/fx/manual", status_code=201)
def fx_manual(body: ManualFxIn, db: Session = Depends(get_db), user: CurrentUser = Depends(ADMIN)):
    row = FxRate(on=body.on, currency=body.currency, rate=body.rate, source="manual", comment=body.comment)
    db.add(row)
    audit(db, user.id, None, "fx_rate", f"{body.currency}@{body.on}", "manual", after=body.model_dump(mode="json"))
    db.commit()
    return serialize(row)


@router.get("/fx/summary")
def fx_summary(db: Session = Depends(get_db), user: CurrentUser = Depends(FD_ADMIN)):
    """Сколько курсов загружено по валютам и источникам, первая и последняя дата — для проверки истории ЦБ."""
    from sqlalchemy import func
    rows = db.execute(select(FxRate.currency, FxRate.source, func.count(FxRate.id), func.min(FxRate.on), func.max(FxRate.on))
                      .group_by(FxRate.currency, FxRate.source)).all()
    return [{"currency": c, "source": s_, "count": n, "first": str(a), "last": str(b)} for c, s_, n, a, b in rows]


@router.get("/fx")
def fx_list(currency: str = "CNY", days: int = 60, db: Session = Depends(get_db), user: CurrentUser = Depends(FD_ADMIN)):
    rows = db.scalars(select(FxRate).where(FxRate.currency == currency).order_by(FxRate.on.desc()).limit(days)).all()
    return [serialize(r) for r in rows]


# ------------------------------------------------------------------ шаблоны, журналы

TEMPLATE_KINDS = {"rfq": "Шаблон RFQ (опросник заводу)", "kp_docx": "Шаблон КП (DOCX, пока не используется генератором)",
                  "logo": "Логотип для КП", "logo_dark": "Логотип на тёмный фон"}


@router.get("/templates")
def templates_list(db: Session = Depends(get_db), user: CurrentUser = Depends(ADMIN)):
    """Загруженные шаблоны по видам (действующий отмечен); без загруженного — встроенный шаблон системы."""
    rows = db.scalars(select(Template).order_by(Template.kind, Template.version.desc())).all()
    out = []
    for t in rows:
        d = serialize(t)
        d["file_name"] = os.path.basename(t.file_path or "")
        d["file_exists"] = bool(t.file_path) and os.path.isfile(t.file_path)
        out.append(d)
    return {"kinds": TEMPLATE_KINDS, "templates": out}


@router.post("/templates/{kind}", status_code=201)
async def upload_template(kind: str, file: UploadFile = File(...), db: Session = Depends(get_db), user: CurrentUser = Depends(ADMIN)):
    if kind not in TEMPLATE_KINDS:
        raise HTTPException(400, "Вид шаблона: " + ", ".join(TEMPLATE_KINDS))
    from ..core.config import get_settings
    ver = (db.scalar(select(Template.version).where(Template.kind == kind).order_by(Template.version.desc())) or 0) + 1
    d = os.path.join(get_settings().storage_local_root, "_templates")   # постоянный том хранилища, а не временная папка
    os.makedirs(d, exist_ok=True)
    name = os.path.basename(file.filename or kind).replace(" ", "_")
    path = os.path.join(d, f"{kind}_v{ver}_{name}")
    with open(path, "wb") as f:
        f.write(await file.read())
    if kind == "rfq":   # проверка: файл должен разбираться как опросник и содержать обязательные поля
        from ..services.rfq_import import parse_rfq
        parsed = parse_rfq(path)
        missing = [e.message for e in parsed.errors if e.severity == "error" and ("нет обязательного поля" in e.message or "заголовков" in e.message)]
        if missing or not parsed.fields:
            os.remove(path)
            raise HTTPException(422, {"message": "Файл не похож на шаблон RFQ", "errors": missing or ["не найдены поля опросника"]})
    for old in db.scalars(select(Template).where(Template.kind == kind, Template.is_active.is_(True))).all():
        old.is_active = False
    t = Template(kind=kind, version=ver, file_path=path, uploaded_by=user.id)
    db.add(t)
    audit(db, user.id, None, "template", kind, "upload", after={"version": ver, "file": name})
    db.commit()
    return serialize(t)


@router.post("/templates/{kind}/reset")
def reset_template(kind: str, db: Session = Depends(get_db), user: CurrentUser = Depends(ADMIN)):
    """Вернуть встроенный шаблон системы: загруженные версии остаются в истории, но перестают действовать."""
    n = 0
    for t in db.scalars(select(Template).where(Template.kind == kind, Template.is_active.is_(True))).all():
        t.is_active = False
        n += 1
    audit(db, user.id, None, "template", kind, "reset")
    db.commit()
    return {"deactivated": n}


@router.post("/templates/{template_id}/activate")
def activate_template(template_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(ADMIN)):
    t = db.get(Template, template_id)
    if t is None or not t.file_path or not os.path.isfile(t.file_path):
        raise HTTPException(404, "Файл шаблона не найден")
    for old in db.scalars(select(Template).where(Template.kind == t.kind, Template.is_active.is_(True))).all():
        old.is_active = False
    t.is_active = True
    audit(db, user.id, None, "template", t.kind, "activate", after={"version": t.version})
    db.commit()
    return serialize(t)


@router.get("/audit")
def audit_all(limit: int = 200, entity: str = "", user_id: str = "", q: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(ADMIN)):
    """Журнал действий: кто, когда, что было / стало (только чтение); фильтры по объекту, сотруднику и тексту."""
    qry = select(AuditLog).order_by(AuditLog.at.desc())
    if entity:
        qry = qry.where(AuditLog.entity == entity)
    if user_id:
        qry = qry.where(AuditLog.user_id == user_id)
    rows = db.scalars(qry.limit(min(max(limit, 1), 1000) if not q else 2000)).all()
    names = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    from ..models import Deal
    deals = {d.id: d.number for d in db.scalars(select(Deal)).all()}
    out = []
    for r in rows:
        d = serialize(r)
        d["user_name"] = names.get(r.user_id, "система" if not r.user_id else r.user_id)
        d["deal_number"] = deals.get(r.deal_id) if getattr(r, "deal_id", None) else None
        if q and q.lower() not in str(d).lower():
            continue
        out.append(d)
    return out[:limit]


@router.get("/audit/entities")
def audit_entities(db: Session = Depends(get_db), user: CurrentUser = Depends(ADMIN)):
    return sorted({e for e in db.scalars(select(AuditLog.entity).distinct()).all() if e})


@router.get("/integrations/log")
def integrations_log(limit: int = 100, db: Session = Depends(get_db), user: CurrentUser = Depends(ADMIN)):
    return [serialize(r) for r in db.scalars(select(IntegrationLog).order_by(IntegrationLog.at.desc()).limit(limit)).all()]


# ------------------------------------------------------------------ уровни доступа (задача 11)

def _access_admin(user: CurrentUser = Depends(ADMIN)) -> CurrentUser:
    if not user.can("access_admin", 2):
        raise HTTPException(403, "Уровни доступа меняют администратор и ЗГД")
    return user


@router.get("/access")
def access_matrix(db: Session = Depends(get_db), user: CurrentUser = Depends(_access_admin)):
    """Матрица «роль × группа полей» (стартовые и действующие уровни) и исключения для сотрудников."""
    from ..models import AccessUserOverride
    from ..services import access_service
    data = access_service.matrix(db)
    rows = db.scalars(select(AccessUserOverride).order_by(AccessUserOverride.created_at.desc())).all()
    users = {u.id: u for u in db.scalars(select(User)).all()}
    data["overrides"] = [{**serialize(o), "user_name": users[o.user_id].display_name if o.user_id in users else o.user_id,
                          "user_roles": users[o.user_id].roles if o.user_id in users else []} for o in rows]
    data["users"] = [{"id": u.id, "display_name": u.display_name, "roles": u.roles, "is_active": u.is_active} for u in users.values()]
    return data


class AccessRuleIn(BaseModel):
    level: int


@router.put("/access/{role}/{group}")
def access_set_rule(role: str, group: str, body: AccessRuleIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_access_admin)):
    from ..services import access_service
    try:
        before = access_service.rules(db).get(role, {}).get(group)
        access_service.set_rule(db, role, group, body.level, user.id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    audit(db, user.id, None, "access_rule", f"{role}/{group}", "update", {"level": before}, {"level": body.level})
    db.commit()
    return {"role": role, "group": group, "level": body.level}


class AccessOverrideIn(BaseModel):
    user_id: str
    group: str
    level: int
    valid_to: Optional[str] = None     # ISO-дата/время; пусто = бессрочно
    comment: str = ""


@router.post("/access/overrides", status_code=201)
def access_set_override(body: AccessOverrideIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_access_admin)):
    from datetime import datetime
    from ..services import access_service
    u = db.get(User, body.user_id)
    if u is None:
        raise HTTPException(404, "Пользователь не найден")
    valid_to = None
    if body.valid_to:
        try:
            valid_to = datetime.fromisoformat(body.valid_to)
        except ValueError:
            raise HTTPException(422, "Срок действия: дата в формате ГГГГ-ММ-ДД")
    try:
        row = access_service.set_override(db, u, body.group, body.level, valid_to, body.comment, user.id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    db.flush()
    audit(db, user.id, None, "access_override", row.id, "set", after=body.model_dump())
    db.commit()
    return serialize(row)


@router.delete("/access/overrides/{override_id}", status_code=204)
def access_delete_override(override_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_access_admin)):
    from ..models import AccessUserOverride
    row = db.get(AccessUserOverride, override_id)
    if row is None:
        return
    audit(db, user.id, None, "access_override", row.id, "delete", before=serialize(row))
    db.delete(row)
    db.commit()


@router.get("/access/preview")
def access_preview(role: Optional[str] = None, user_id: Optional[str] = None, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(_access_admin)):
    """Как видит роль X / сотрудник Y: итоговые уровни по группам и закрытые ключи."""
    from ..core.roles import LEVEL_TITLES, FIELD_GROUPS, denied_fields_for, effective_perms
    from ..services import access_service
    if user_id:
        u = db.get(User, user_id)
        if u is None:
            raise HTTPException(404)
        perms = access_service.perms_for(db, u)
        who = u.display_name
    elif role:
        perms = effective_perms({Role(role)}, access_service.rules(db))
        who = role
    else:
        raise HTTPException(422, "Укажите роль или сотрудника")
    return {"who": who, "perms": [{"group": g, "title": FIELD_GROUPS[g][0], "level": lvl, "level_title": LEVEL_TITLES[lvl]} for g, lvl in perms.items()],
            "denied_fields": sorted(denied_fields_for(perms))}
