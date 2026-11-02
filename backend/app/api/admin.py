"""Администрирование: настройки с историей, пользователи и роли, ТН ВЭД, курсы, шаблоны, журнал."""
from __future__ import annotations

import csv
import io
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

# ключи, которые может менять ФД (ставки стоимости денег, порог маржи, ΔK/Δt, лимиты)
FD_KEYS = {"margin_threshold_pct", "scenario_dk", "scenario_dt", "use_historical_volatility", "vol_multipliers", "fx_weights",
           "fx_history_years", "key_rate_pct", "pboc_rate_pct", "foreign_rates_pct", "fx_expert_points", "financing_rate_pct",
           "deposit_rate_pct", "conversion_fee_pct", "transfer_fee_rub", "guarantee_rate_pct", "cash_gap_limit_rub",
           "overhead_actual_pct_last_year", "warranty_reserve_pct_by_type", "unplanned_reserve_pct"}


@router.get("/settings")
def settings_all(db: Session = Depends(get_db), user: CurrentUser = Depends(FD_ADMIN)):
    return settings_service.get_all(db)


class SettingIn(BaseModel):
    value: Any
    comment: str = ""


@router.put("/settings/{key}")
def set_setting(key: str, body: SettingIn, db: Session = Depends(get_db), user: CurrentUser = Depends(FD_ADMIN)):
    if key not in settings_service.DEFAULTS:
        raise HTTPException(404, "Неизвестный ключ")
    if not user.has(Role.ADMIN, Role.GD) and key not in FD_KEYS:
        raise HTTPException(403, "Этот параметр меняет администратор")
    if key in ("gd_deal_limit_rub", "overhead_scale") and not user.has(Role.GD, Role.ADMIN):
        raise HTTPException(403, "Лимит и шкалу накладных утверждает ГД")
    before = settings_service.get(db, key)
    settings_service.set_value(db, key, body.value, user.id, body.comment)
    audit(db, user.id, None, "setting", key, "update", {"value": before}, {"value": body.value}, body.comment)
    db.commit()
    return {"key": key, "value": body.value}


@router.get("/settings/{key}/history")
def setting_history(key: str, db: Session = Depends(get_db), user: CurrentUser = Depends(FD_ADMIN)):
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
    for r in body.roles:
        Role(r)
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
    for k, v in body.items():
        if k in UserIn.model_fields:
            setattr(u, k, v)
    audit(db, user.id, None, "user", u.id, "update", {k: before.get(k) for k in body}, body)
    db.commit()
    return serialize(u)


# ------------------------------------------------------------------ ТН ВЭД

@router.post("/hs/upload")
async def hs_upload(file: UploadFile = File(...), valid_from: date = date.today(), db: Session = Depends(get_db), user: CurrentUser = Depends(ADMIN)):
    """CSV: code;description;duty_pct;vat_pct;util_fee;licence;certification. Старые записи закрываются датой."""
    raw = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw), delimiter=";")
    n = 0
    for row in reader:
        for old in db.scalars(select(HsCode).where(HsCode.code == row["code"], HsCode.valid_to.is_(None))).all():
            old.valid_to = valid_from
        db.add(HsCode(code=row["code"], description=row["description"], duty_pct=float(row.get("duty_pct") or 0),
                      vat_pct=float(row.get("vat_pct") or 20), util_fee=row.get("util_fee", "").lower() in ("1", "да", "true"),
                      licence_required=row.get("licence", "").lower() in ("1", "да", "true"),
                      certification=[c.strip() for c in (row.get("certification") or "").split(",") if c.strip()],
                      valid_from=valid_from, version_file=file.filename or ""))
        n += 1
    audit(db, user.id, None, "hs_codes", file.filename or "", "upload", after={"rows": n, "valid_from": str(valid_from)})
    db.commit()
    return {"imported": n}


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


@router.get("/fx")
def fx_list(currency: str = "CNY", days: int = 60, db: Session = Depends(get_db), user: CurrentUser = Depends(FD_ADMIN)):
    rows = db.scalars(select(FxRate).where(FxRate.currency == currency).order_by(FxRate.on.desc()).limit(days)).all()
    return [serialize(r) for r in rows]


# ------------------------------------------------------------------ шаблоны, журналы

@router.post("/templates/{kind}", status_code=201)
async def upload_template(kind: str, file: UploadFile = File(...), db: Session = Depends(get_db), user: CurrentUser = Depends(ADMIN)):
    if kind not in ("rfq", "kp_docx", "excel", "mail", "wecom", "logo", "logo_dark"):
        raise HTTPException(400, "kind: rfq | kp_docx | excel | mail | wecom | logo | logo_dark")
    import os, tempfile
    d = tempfile.mkdtemp()
    path = os.path.join(d, file.filename or kind)
    with open(path, "wb") as f:
        f.write(await file.read())
    ver = (db.scalar(select(Template.version).where(Template.kind == kind).order_by(Template.version.desc())) or 0) + 1
    for old in db.scalars(select(Template).where(Template.kind == kind, Template.is_active.is_(True))).all():
        old.is_active = False
    t = Template(kind=kind, version=ver, file_path=path, uploaded_by=user.id)
    db.add(t)
    db.commit()
    return serialize(t)


@router.get("/audit")
def audit_all(limit: int = 200, db: Session = Depends(get_db), user: CurrentUser = Depends(ADMIN)):
    return [serialize(r) for r in db.scalars(select(AuditLog).order_by(AuditLog.at.desc()).limit(limit)).all()]


@router.get("/integrations/log")
def integrations_log(limit: int = 100, db: Session = Depends(get_db), user: CurrentUser = Depends(ADMIN)):
    return [serialize(r) for r in db.scalars(select(IntegrationLog).order_by(IntegrationLog.at.desc()).limit(limit)).all()]


# ------------------------------------------------------------------ уровни доступа (задача 11)

def _access_admin(user: CurrentUser = Depends(ADMIN)) -> CurrentUser:
    if not user.can("access_admin", 2):
        raise HTTPException(403, "Уровни доступа меняют администратор и ГД")
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
            raise HTTPException(422, "valid_to: дата в формате ГГГГ-ММ-ДД")
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
        raise HTTPException(422, "Укажите role или user_id")
    return {"who": who, "perms": [{"group": g, "title": FIELD_GROUPS[g][0], "level": lvl, "level_title": LEVEL_TITLES[lvl]} for g, lvl in perms.items()],
            "denied_fields": sorted(denied_fields_for(perms))}
