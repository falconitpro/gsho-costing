"""Блок «Персонал» (D-205): личные данные, оплата труда и сканы документов сотрудника.

Три класса данных — три группы доступа: hr_core (кадры), hr_personal (личные данные), hr_payroll (оплата труда).
Личные данные и оплата хранятся зашифрованными (services/hr_crypto), сканы — зашифрованными файлами в хранилище.
В журнал пишутся только названия полей и вид документа, без значений. Каждый просмотр личных данных, оплаты и
скачивание скана записывается в журнал (кто и когда смотрел).
Область по юрлицам — как в api/hr: сотрудник виден, если у него есть трудовые отношения с работодателем из области.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.clock import today
from ..core.roles import EDIT, READ
from ..db import get_db
from ..models import HrEmployeeDocument, HrEmployeePersonal, HrEmployment, HrPayRecord, User
from ..services import hr_core as hc
from ..services import hr_crypto
from ..services.deal_service import audit
from .hr import _visible_employee

router = APIRouter(prefix="/api/hr", tags=["hr"])

CLASS_GROUP = {"core": "hr_core", "personal": "hr_personal", "payroll": "hr_payroll"}
DOC_KINDS: dict[str, tuple[str, str]] = {
    "passport": ("Паспорт", "personal"),
    "foreign_passport": ("Заграничный паспорт", "personal"),
    "snils": ("СНИЛС", "personal"),
    "inn": ("ИНН", "personal"),
    "education": ("Документ об образовании", "personal"),
    "military": ("Военный билет / приписное", "personal"),
    "driver_license": ("Водительское удостоверение", "personal"),
    "family": ("Свидетельство о браке, рождении детей", "personal"),
    "consent_pd": ("Согласие на обработку персональных данных", "personal"),
    "medical": ("Медицинское заключение (итог, без диагноза)", "personal"),
    "photo": ("Фотография", "personal"),
    "personal_other": ("Другой личный документ", "personal"),
    "labor_contract": ("Трудовой договор", "payroll"),
    "addendum": ("Дополнительное соглашение", "payroll"),
    "bank": ("Банковские реквизиты", "payroll"),
    "payroll_other": ("Другой документ по оплате труда", "payroll"),
    "order": ("Приказ", "core"),
    "application": ("Заявление", "core"),
    "certificate": ("Удостоверение, допуск, обучение", "core"),
    "work_book": ("Трудовая книжка / сведения о трудовой деятельности", "core"),
    "core_other": ("Другой кадровый документ", "core"),
}
MAX_FILES = 20


def _view_log(db: Session, user: CurrentUser, employee_id: str, action: str, after: Optional[dict] = None) -> None:
    """Просмотр — только в журнал (кто и когда смотрел), без события в реестре изменений: данные не менялись."""
    from ..models import AuditLog
    db.add(AuditLog(user_id=user.id, deal_id=None, entity="hr_employee", entity_id=employee_id, action=action, before=None, after=after, comment=""))


def _need(user: CurrentUser, group: str, level: int = READ) -> None:
    if not user.can(group, level):
        what = {"hr_core": "кадровым данным", "hr_personal": "личным данным", "hr_payroll": "данным об оплате труда"}[group]
        raise HTTPException(403, f"Нет доступа к {what} сотрудников" + (" (нужно право редактирования)" if level == EDIT else ""))


# ───────────── личные данные

class Child(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field("", max_length=255)
    birth_date: Optional[date] = None


class Education(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: str = Field("", max_length=64)
    institution: str = Field("", max_length=255)
    year: str = Field("", max_length=8)
    specialty: str = Field("", max_length=255)
    diploma: str = Field("", max_length=64)


class PersonalIn(BaseModel):
    """Все поля необязательные. Неизвестные поля не принимаются (extra=forbid)."""
    model_config = ConfigDict(extra="forbid")
    birth_date: Optional[date] = None
    birth_place: str = Field("", max_length=255)
    gender: Literal["", "m", "f"] = ""
    citizenship: str = Field("", max_length=64)
    passport_series: str = Field("", max_length=16)
    passport_number: str = Field("", max_length=32)
    passport_issued_by: str = Field("", max_length=500)
    passport_issue_date: Optional[date] = None
    passport_division_code: str = Field("", max_length=16)
    foreign_passport_number: str = Field("", max_length=32)
    foreign_passport_valid_to: Optional[date] = None
    snils: str = Field("", max_length=20)
    inn: str = Field("", max_length=16)
    reg_address: str = Field("", max_length=500)
    fact_address: str = Field("", max_length=500)
    personal_phone: str = Field("", max_length=32)
    personal_email: str = Field("", max_length=255)
    marital_status: str = Field("", max_length=64)
    children: list[Child] = Field(default_factory=list, max_length=20)
    emergency_name: str = Field("", max_length=255)
    emergency_relation: str = Field("", max_length=64)
    emergency_phone: str = Field("", max_length=32)
    education: list[Education] = Field(default_factory=list, max_length=10)
    military_category: str = Field("", max_length=32)
    military_rank: str = Field("", max_length=64)
    military_vus: str = Field("", max_length=32)
    military_office: str = Field("", max_length=255)
    driver_license_number: str = Field("", max_length=32)
    driver_license_categories: str = Field("", max_length=32)
    driver_license_valid_to: Optional[date] = None
    notes: str = Field("", max_length=2000)


def _digits(v: str) -> str:
    return "".join(ch for ch in v if ch.isdigit())


def _snils_ok(v: str) -> bool:
    d = _digits(v)
    if len(d) != 11:
        return False
    s = sum(int(d[i]) * (9 - i) for i in range(9))
    c = s % 101 if s > 101 else s
    return (0 if c in (100, 101) else c) == int(d[9:])


def _warnings(p: dict) -> list[str]:
    """Подсказки, а не запрет: у сотрудников из Киргизии и Китая форматы другие."""
    w = []
    if p.get("snils") and not _snils_ok(p["snils"]):
        w.append("СНИЛС: не сходится контрольное число")
    if p.get("inn") and len(_digits(p["inn"])) not in (10, 12):
        w.append("ИНН: для России — 12 цифр")
    if p.get("passport_series") and (p.get("citizenship") or "").lower() in ("", "россия", "рф", "российская федерация") and len(_digits(p["passport_series"])) != 4:
        w.append("Паспорт РФ: серия — 4 цифры")
    return w


def _personal_out(row: Optional[HrEmployeePersonal], names: dict) -> dict:
    if not row or not row.data_enc:
        return {"data": PersonalIn().model_dump(mode="json"), "filled": [], "updated_by": "", "updated_at": None, "warnings": []}
    data = {**PersonalIn().model_dump(mode="json"), **hr_crypto.unseal(row.data_enc, row.kid, f"personal|{row.employee_id}")}
    return {"data": data, "filled": row.fields or [], "updated_by": names.get(row.updated_by, ""), "updated_at": row.updated_at, "warnings": _warnings(data)}


def _user_names(db: Session) -> dict:
    return {u.id: u.display_name for u in db.scalars(select(User))}


@router.get("/employees/{employee_id}/personal")
def personal(employee_id: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _need(user, "hr_core")
    _need(user, "hr_personal")
    e, _ = _visible_employee(db, user, employee_id)
    out = _personal_out(db.get(HrEmployeePersonal, e.id), _user_names(db))
    _view_log(db, user, e.id, "personal_view")
    db.commit()
    return out


@router.put("/employees/{employee_id}/personal")
def personal_save(employee_id: str, body: PersonalIn, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _need(user, "hr_core")
    _need(user, "hr_personal", EDIT)
    e, _ = _visible_employee(db, user, employee_id)
    data = body.model_dump(mode="json")
    for k, v in data.items():
        if isinstance(v, str):
            data[k] = v.strip()
    row = db.get(HrEmployeePersonal, e.id)
    before = set()
    if row and row.data_enc:
        old = hr_crypto.unseal(row.data_enc, row.kid, f"personal|{e.id}")
        changed = sorted(k for k in data if data[k] != old.get(k, PersonalIn.model_fields[k].get_default(call_default_factory=True)))
        before = set(row.fields or [])
    else:
        changed = sorted(k for k, v in data.items() if v not in ("", None, []))
    enc, kid = hr_crypto.seal(data, f"personal|{e.id}")
    filled = sorted(k for k, v in data.items() if v not in ("", None, []))
    if row is None:
        row = HrEmployeePersonal(employee_id=e.id)
        db.add(row)
    row.data_enc, row.kid, row.fields, row.updated_by = enc, kid, filled, user.id
    if changed or before != set(filled):
        audit(db, user.id, None, "hr_employee", e.id, "personal_update", after={"fields": changed})
    db.commit()
    db.refresh(row)
    return _personal_out(row, _user_names(db))


# ───────────── оплата труда

class PayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["salary", "allowance", "bank", "cancel"]
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    # оклад / тариф
    amount: Optional[float] = Field(None, ge=0, le=1e9)
    currency: str = Field("RUB", max_length=3)
    period: Literal["month", "hour", "day", "shift"] = "month"
    # надбавка: название и процент или сумма
    name: str = Field("", max_length=255)
    percent: Optional[float] = Field(None, ge=0, le=1000)
    # банковские реквизиты
    bank_name: str = Field("", max_length=255)
    bik: str = Field("", max_length=16)
    account: str = Field("", max_length=34)
    card: str = Field("", max_length=32)
    cancels_id: Optional[str] = None
    comment: str = Field("", max_length=1000)


PAY_TITLES = {"salary": "Оклад / тариф", "allowance": "Надбавка", "bank": "Банковские реквизиты", "cancel": "Отмена записи"}


def _pay_data(b: PayIn) -> dict:
    if b.kind == "salary":
        if b.amount is None:
            raise HTTPException(422, "Укажите сумму оклада или тарифа")
        return {"amount": b.amount, "currency": b.currency.upper() or "RUB", "period": b.period}
    if b.kind == "allowance":
        if not b.name.strip() or (b.percent is None and b.amount is None):
            raise HTTPException(422, "Надбавка: название и процент или сумма")
        return {"name": b.name.strip(), "percent": b.percent, "amount": b.amount, "currency": b.currency.upper() or "RUB"}
    if b.kind == "bank":
        if not (b.account.strip() or b.card.strip()):
            raise HTTPException(422, "Укажите счёт или номер карты")
        return {"bank_name": b.bank_name.strip(), "bik": b.bik.strip(), "account": b.account.strip(), "card": b.card.strip()}
    return {}


def _pay_rows(db: Session, employment_ids: list[str]) -> list[HrPayRecord]:
    if not employment_ids:
        return []
    return list(db.scalars(select(HrPayRecord).where(HrPayRecord.employment_id.in_(employment_ids))))


def _pay_out(rows: list[HrPayRecord], on: date, names: dict) -> dict:
    dead = {r.cancels_id for r in rows if r.kind == "cancel" and r.cancels_id}
    items = []
    for r in sorted(rows, key=lambda r: (r.effective_from, r.recorded_at), reverse=True):
        items.append({"id": r.id, "employment_id": r.employment_id, "kind": r.kind, "title": PAY_TITLES.get(r.kind, r.kind),
                      "effective_from": r.effective_from, "effective_to": r.effective_to, "cancels_id": r.cancels_id, "cancelled": r.id in dead,
                      "data": hr_crypto.unseal(r.data_enc, r.kid, f"pay|{r.id}") if r.data_enc else {},
                      "comment": r.comment, "recorded_by": names.get(r.recorded_by, ""), "recorded_at": r.recorded_at})
    by_emp: dict[str, dict] = {}
    for it in sorted(items, key=lambda x: (x["effective_from"], x["recorded_at"])):
        if it["cancelled"] or it["kind"] == "cancel" or it["effective_from"] > on or (it["effective_to"] and on > it["effective_to"]):
            continue
        cur = by_emp.setdefault(it["employment_id"], {"salary": None, "allowances": [], "bank": None})
        if it["kind"] == "allowance":
            cur["allowances"].append(it["data"])
        else:
            cur[it["kind"]] = it["data"]
    return {"current": by_emp, "records": items}


@router.get("/employees/{employee_id}/pay")
def pay(employee_id: str, on: Optional[date] = None, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _need(user, "hr_core")
    _need(user, "hr_payroll")
    e, emps = _visible_employee(db, user, employee_id)
    out = _pay_out(_pay_rows(db, [m.id for m in emps]), on or today(), _user_names(db))
    _view_log(db, user, e.id, "pay_view")
    db.commit()
    return out


@router.post("/employments/{employment_id}/pay")
def pay_add(employment_id: str, body: PayIn, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _need(user, "hr_core")
    _need(user, "hr_payroll", EDIT)
    m = db.get(HrEmployment, employment_id)
    if not m or not hc.in_scope(hc.allowed_employers(db, user), m.employer_id):
        raise HTTPException(404, "Не найдено")
    eff = body.effective_from or today()
    if body.effective_to and body.effective_to < eff:
        raise HTTPException(422, "Дата окончания раньше даты начала")
    rows = _pay_rows(db, [m.id])
    cancels = None
    if body.kind == "cancel":
        target = next((r for r in rows if r.id == body.cancels_id), None)
        if not target or target.kind == "cancel":
            raise HTTPException(404, "Запись не найдена")
        if any(r.cancels_id == target.id for r in rows):
            raise HTTPException(409, "Запись уже отменена")
        if not body.comment.strip():
            raise HTTPException(422, "Укажите причину отмены")
        cancels, eff = target.id, target.effective_from
    elif eff < m.start_date or (m.end_date and eff > m.end_date):
        raise HTTPException(422, "Дата вне периода трудовых отношений")
    import uuid
    data = _pay_data(body)
    rid = str(uuid.uuid4())
    enc, kid = hr_crypto.seal(data, f"pay|{rid}") if data else ("", "")   # запись только добавляется — шифруем до вставки
    r = HrPayRecord(id=rid, employment_id=m.id, employee_id=m.employee_id, employer_id=m.employer_id, kind=body.kind, effective_from=eff,
                    effective_to=body.effective_to if body.kind != "cancel" else None, cancels_id=cancels, comment=body.comment.strip(),
                    data_enc=enc, kid=kid, recorded_by=user.id)
    db.add(r)
    db.flush()
    audit(db, user.id, None, "hr_employment", m.id, f"pay_{body.kind}", after={"fields": sorted(data)} if data else None)
    db.commit()
    return pay(m.employee_id, None, user, db)


# ───────────── сканы документов

def _doc_out(d: HrEmployeeDocument, names: dict) -> dict:
    return {"id": d.id, "employee_id": d.employee_id, "employment_id": d.employment_id, "kind": d.kind, "kind_title": DOC_KINDS.get(d.kind, (d.kind, ""))[0],
            "data_class": d.data_class, "title": d.title, "number": d.number, "issued_on": d.issued_on, "valid_until": d.valid_until,
            "file_name": d.file_name, "mime": d.mime, "size": d.size, "archived": d.archived, "comment": d.comment,
            "uploaded_by": names.get(d.uploaded_by, ""), "uploaded_at": d.uploaded_at,
            "expired": bool(d.valid_until and d.valid_until < today())}


@router.get("/document-kinds")
def document_kinds(user: CurrentUser = Depends(get_current_user)):
    _need(user, "hr_core")
    return [{"code": k, "title": t, "data_class": c, "can_read": user.can(CLASS_GROUP[c], READ), "can_edit": user.can(CLASS_GROUP[c], EDIT)}
            for k, (t, c) in DOC_KINDS.items()]


@router.get("/employees/{employee_id}/documents")
def documents(employee_id: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _need(user, "hr_core")
    e, _ = _visible_employee(db, user, employee_id)
    classes = {c for c, g in CLASS_GROUP.items() if user.can(g, READ)}
    rows = db.scalars(select(HrEmployeeDocument).where(HrEmployeeDocument.employee_id == e.id).order_by(HrEmployeeDocument.uploaded_at.desc())).all()
    names = _user_names(db)
    hidden = sum(1 for d in rows if d.data_class not in classes)
    return {"items": [_doc_out(d, names) for d in rows if d.data_class in classes], "hidden": hidden}


@router.post("/employees/{employee_id}/documents", status_code=201)
def document_upload(employee_id: str, files: list[UploadFile] = File(...), kind: str = Form(...), title: str = Form(""), number: str = Form(""),
                    issued_on: Optional[date] = Form(None), valid_until: Optional[date] = Form(None), employment_id: str = Form(""),
                    comment: str = Form(""), user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Скан (или несколько страниц — несколько файлов). Файл проверяется антивирусом на входе (upload_gate), шифруется и кладётся в хранилище."""
    from .. import adapters
    from ..core.files import read_limited, safe_filename
    _need(user, "hr_core")
    if kind not in DOC_KINDS:
        raise HTTPException(422, "Вид документа — из списка")
    cls = DOC_KINDS[kind][1]
    _need(user, CLASS_GROUP[cls], EDIT)
    e, emps = _visible_employee(db, user, employee_id)
    if employment_id and employment_id not in {m.id for m in emps}:
        raise HTTPException(422, "Трудовые отношения не найдены")
    if not files or len(files) > MAX_FILES:
        raise HTTPException(422, f"От 1 до {MAX_FILES} файлов за раз")
    names = _user_names(db)
    out = []
    for i, f in enumerate(files):
        data = read_limited(f)
        name = safe_filename(f.filename, "scan")
        d = HrEmployeeDocument(employee_id=e.id, employment_id=employment_id or None, kind=kind, data_class=cls,
                               title=(title.strip() or DOC_KINDS[kind][0])[:255] + (f" — стр. {i + 1}" if len(files) > 1 else ""),
                               number=number.strip()[:64], issued_on=issued_on, valid_until=valid_until, file_name=name[:255],
                               mime=(f.content_type or "")[:128], size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                               storage_path="", comment=comment.strip(), uploaded_by=user.id)
        db.add(d)
        db.flush()
        sealed, kid = hr_crypto.seal_file(data, f"doc|{d.id}")
        fd, path = tempfile.mkstemp(suffix=".enc")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(sealed)
            # в пути — только id: ни ФИО, ни имени файла в хранилище нет
            stored = adapters.storage().upload(path, f"Персонал/{e.id}", "07_fact", f"{d.id}.enc")
        finally:
            os.unlink(path)
        d.storage_path, d.kid = stored.path, kid
        audit(db, user.id, None, "hr_employee", e.id, "document_upload", after={"kind": kind, "data_class": cls})
        out.append(_doc_out(d, names))
    db.commit()
    return out


def _doc_scoped(db: Session, user: CurrentUser, doc_id: str, level: int) -> HrEmployeeDocument:
    _need(user, "hr_core")
    d = db.get(HrEmployeeDocument, doc_id)
    if not d:
        raise HTTPException(404, "Документ не найден")
    _visible_employee(db, user, d.employee_id)   # чужое юрлицо — 404
    _need(user, CLASS_GROUP[d.data_class], level)
    return d


@router.get("/documents/{doc_id}/file")
def document_file(doc_id: str, download: bool = False, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    from starlette.background import BackgroundTask

    from .. import adapters
    from ..core.files import file_response
    d = _doc_scoped(db, user, doc_id, READ)
    fd, enc_path = tempfile.mkstemp(suffix=".enc")
    os.close(fd)
    try:
        adapters.storage().download(d.storage_path, enc_path)
        with open(enc_path, "rb") as fh:
            data = hr_crypto.unseal_file(fh.read(), d.kid, f"doc|{d.id}")
    finally:
        os.unlink(enc_path)
    fd, path = tempfile.mkstemp(suffix=os.path.splitext(d.file_name)[1])
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    _view_log(db, user, d.employee_id, "document_download" if download else "document_view", {"kind": d.kind})
    db.commit()
    return file_response(path, d.file_name, inline=not download, background=BackgroundTask(os.unlink, path))


class DocPatch(BaseModel):
    archived: Optional[bool] = None
    number: Optional[str] = Field(None, max_length=64)
    issued_on: Optional[date] = None
    valid_until: Optional[date] = None
    comment: Optional[str] = Field(None, max_length=2000)


@router.patch("/documents/{doc_id}")
def document_update(doc_id: str, body: DocPatch, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Сканы не удаляются: устаревший снимается с актуальных (archived) и остаётся в истории."""
    d = _doc_scoped(db, user, doc_id, EDIT)
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(d, k, v.strip() if isinstance(v, str) else v)
    audit(db, user.id, None, "hr_employee", d.employee_id, "document_update", after={"kind": d.kind, "fields": sorted(data)})
    db.commit()
    return _doc_out(d, _user_names(db))
