"""Юридический блок, этап 1: API юридических профилей контрагентов и компаний группы, банковские профили."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

import os
import shutil
import tempfile
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from ..core.config import get_settings
from ..core.files import file_response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import Role
from ..db import get_db
from ..models import BankProfile, Client, Factory, LegalEntity, LegalProfile
from ..services import legal_profiles as svc
from ..services.deal_service import audit

router = APIRouter(prefix="/api/legal", tags=["legal"])


def _reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.can("legal_profiles"):
        raise HTTPException(403, "Юридические профили закрыты для вашей роли")
    return user


def _editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.can("legal_profiles", 2):
        raise HTTPException(403, "Юридические профили ведут менеджер, КД, ЗГД, ФД, юрист и администратор")
    return user


def _own_editor(user: CurrentUser) -> None:
    """Карточку БЕРГ-ХАУС (компаний группы) правят только КД, ЗГД, ФД, юрист и администратор."""
    if not user.has(Role.CD, Role.GD, Role.FD, Role.LAWYER, Role.ADMIN):
        raise HTTPException(403, "Карточку компании группы правят КД, ЗГД, ФД, юрист и администратор")


def _profile(db: Session, pid: str) -> LegalProfile:
    p = db.get(LegalProfile, pid)
    if p is None:
        raise HTTPException(404, "Профиль не найден")
    return p


def _owner_exists(db: Session, kind: str, oid: str) -> str:
    model = {"client": Client, "factory": Factory, "own": LegalEntity}.get(kind)
    if model is None:
        raise HTTPException(422, "owner_kind: client | factory | own")
    o = db.get(model, oid)
    if o is None:
        raise HTTPException(404, "Контрагент не найден")
    return getattr(o, "name", "")


class ProfileIn(BaseModel):
    kind: Optional[str] = None
    title: Optional[str] = None
    is_default: Optional[bool] = None
    country: Optional[str] = None
    names: Optional[dict] = None
    opf: Optional[str] = None
    reg_number: Optional[str] = None
    tax_number: Optional[str] = None
    kpp: Optional[str] = None
    legal_address: Optional[str] = None
    actual_address: Optional[str] = None
    postal_address: Optional[str] = None
    director: Optional[str] = None
    signatory_name: Optional[str] = None
    signatory_position: Optional[str] = None
    signatory_basis: Optional[str] = None
    contacts: Optional[list[dict]] = None
    documents: Optional[list[dict]] = None
    powers_of_attorney: Optional[list[dict]] = None
    extra: Optional[dict] = None
    lawyer_comment: Optional[str] = None
    is_active: Optional[bool] = None


class ProfileCreate(ProfileIn):
    owner_kind: str
    owner_id: str


class CheckIn(BaseModel):
    status: str                       # not_checked | checked | issues
    comment: str = ""


class BankIn(BaseModel):
    title: Optional[str] = None
    currency: Optional[str] = None
    country: Optional[str] = None
    beneficiary: Optional[str] = None
    bank_name: Optional[str] = None
    bank_address: Optional[str] = None
    account: Optional[str] = None
    bik: Optional[str] = None
    swift: Optional[str] = None
    corr_account: Optional[str] = None
    corr_bank: Optional[str] = None
    purpose: Optional[str] = None
    status: Optional[str] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None


@router.get("/dictionaries")
def dictionaries(user: CurrentUser = Depends(_reader)):
    return {"owner_kinds": svc.OWNER_KINDS, "profile_kinds": svc.PROFILE_KINDS, "check_status": svc.CHECK_STATUS, "bank_status": svc.BANK_STATUS, "country_fields": svc.COUNTRY_FIELDS}


@router.get("/profiles")
def list_profiles(owner_kind: str, owner_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Профили владельца (первый создаётся автоматически из карточки)."""
    owner_name = _owner_exists(db, owner_kind, owner_id)
    rows = svc.ensure_profiles(db, owner_kind, owner_id)
    db.commit()
    names = svc.user_names(db)
    can_edit = user.can("legal_profiles", 2) and (owner_kind != "own" or user.has(Role.CD, Role.GD, Role.FD, Role.LAWYER, Role.ADMIN))
    return {"owner_kind": owner_kind, "owner_id": owner_id, "owner_name": owner_name, "items": [svc.profile_payload(p, names) for p in rows],
            "can_edit": can_edit, "can_check": user.has(Role.LAWYER, Role.CD, Role.GD), "can_verify_bank": user.has(Role.FD, Role.LAWYER, Role.CD, Role.GD)}


@router.post("/profiles", status_code=201)
def create_profile(body: ProfileCreate, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    _owner_exists(db, body.owner_kind, body.owner_id)
    if body.owner_kind == "own":
        _own_editor(user)
    existing = svc.ensure_profiles(db, body.owner_kind, body.owner_id)
    data = body.model_dump(exclude_none=True, exclude={"owner_kind", "owner_id"})
    if data.get("kind") and data["kind"] not in svc.PROFILE_KINDS:
        raise HTTPException(422, "Неизвестный тип контрагента")
    p = LegalProfile(owner_kind=body.owner_kind, owner_id=body.owner_id, kind=data.pop("kind", "own" if body.owner_kind == "own" else "other"),
                     is_default=not existing, names=data.pop("names", svc._names()))
    for k, v in data.items():
        setattr(p, k, v)
    if p.is_default:
        for o in existing:
            o.is_default = False
    p.history = [{"at": datetime.now(timezone.utc).isoformat(), "by": user.id, "action": "created", "fields": {}}]
    db.add(p)
    db.flush()
    audit(db, user.id, None, "legal_profile", p.id, "create", after={"owner_kind": body.owner_kind, "owner_id": body.owner_id, "title": p.title})
    db.commit()
    return svc.profile_payload(p, svc.user_names(db))


@router.put("/profiles/{pid}")
def update_profile(pid: str, body: ProfileIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    p = _profile(db, pid)
    if p.owner_kind == "own":
        _own_editor(user)
    data = body.model_dump(exclude_none=True)
    if data.get("kind") and data["kind"] not in svc.PROFILE_KINDS:
        raise HTTPException(422, "Неизвестный тип контрагента")
    if data.get("is_default"):
        for o in svc.ensure_profiles(db, p.owner_kind, p.owner_id):
            if o.id != p.id:
                o.is_default = False
    if "documents" in data:
        import uuid as _uuid
        old_files = {d.get("id"): d.get("file") for d in (p.documents or []) if d.get("file")}
        for d in data["documents"]:
            d.setdefault("id", _uuid.uuid4().hex)
            if d["id"] in old_files and not d.get("file"):
                d["file"] = old_files[d["id"]]          # файл сохраняется при правке строки из формы
    changed = svc.apply_changes(p, data, svc.PROFILE_FIELDS, user.id)
    if changed:
        # правка реквизитов снимает отметку «проверен» — юрист проверит заново
        if p.check_status == "checked" and any(k not in ("title", "is_default", "lawyer_comment", "contacts") for k in changed):
            p.check_status, p.checked_at, p.checked_by = "not_checked", None, None
        audit(db, user.id, None, "legal_profile", p.id, "update", before={k: v[0] for k, v in changed.items()}, after={k: v[1] for k, v in changed.items()})
    db.commit()
    return svc.profile_payload(p, svc.user_names(db))


def _opf(v) -> str:
    """ОПФ из ответа реестра: DaData отдаёт {code, full, short, type} — берём краткую форму."""
    if isinstance(v, dict):
        return v.get("short") or v.get("full") or ""
    return str(v or "")


class FillByInnIn(BaseModel):
    inn: str
    kpp: str = ""          # выбрать филиал по КПП, если у организации их несколько


@router.post("/profiles/{pid}/fill-by-inn")
def fill_by_inn(pid: str, body: FillByInnIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Заполнить российский профиль из ЕГРЮЛ/ЕГРИП по ИНН (как в карточке клиента): наименования, ОГРН, КПП, адрес, руководитель.
    Возвращает заполненный профиль и список филиалов, если они есть — можно вызвать повторно с нужным КПП."""
    from ..adapters.company_registry import inn_valid
    from .references import _registry_lookup
    p = _profile(db, pid)
    if p.owner_kind == "own":
        _own_editor(user)
    inn = body.inn.strip()
    if not inn_valid(inn):
        raise HTTPException(422, "ИНН неверный: проверьте контрольные цифры")
    found = _registry_lookup(db, inn, user)
    if not found:
        raise HTTPException(404, "Организация с таким ИНН в реестре не найдена — заполните реквизиты вручную")
    is_branch = lambda f: (f.get("raw") or {}).get("branch_type") == "BRANCH"   # noqa: E731
    if body.kpp.strip():
        main = next((f for f in found if (f.get("kpp") or "") == body.kpp.strip()), None)
        if main is None:
            raise HTTPException(404, "Подразделение с таким КПП не найдено")
    else:
        main = next((f for f in found if not is_branch(f)), found[0])
    names = dict(p.names or {})
    names["ru"] = {"full": main.get("full_name") or main.get("name") or "", "short": main.get("name") or ""}
    data = {"names": names, "country": "RU", "tax_number": inn, "kpp": main.get("kpp") or "", "reg_number": main.get("ogrn") or "", "legal_address": main.get("address") or "",
            "director": main.get("director") or p.director, "opf": _opf((main.get("raw") or {}).get("opf")) or p.opf}
    if not p.signatory_name and main.get("director"):
        data.update(signatory_name=main["director"], signatory_position=p.signatory_position or "Генеральный директор", signatory_basis=p.signatory_basis or "Устава")
    if p.kind not in ("ru_customer", "own", "intermediary", "other"):
        data["kind"] = "ru_customer"
    changed = svc.apply_changes(p, data, svc.PROFILE_FIELDS, user.id, action="registry")
    if changed:
        p.check_status, p.checked_at, p.checked_by = "not_checked", None, None
    p.extra = {**(p.extra or {}), "registry_status": main.get("status") or "", "registry_checked_on": datetime.now(timezone.utc).date().isoformat(), "okved": main.get("okved") or (p.extra or {}).get("okved", "")}
    audit(db, user.id, None, "legal_profile", p.id, "fill_by_inn", after={"inn": inn, "kpp": main.get("kpp"), "source": main.get("source")})
    db.commit()
    branches = [{"kpp": f.get("kpp", ""), "name": f.get("name", ""), "address": f.get("address", "")} for f in found if f is not main and is_branch(f)]
    return {"profile": svc.profile_payload(p, svc.user_names(db)), "registry_status": main.get("status") or "", "branches": branches}


@router.post("/profiles/{pid}/check")
def check_profile(pid: str, body: CheckIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Отметка проверки юристом (или КД/ЗГД): проверен / есть замечания; комментарий юриста."""
    if not user.has(Role.LAWYER, Role.CD, Role.GD):
        raise HTTPException(403, "Проверку профиля отмечает юрист, КД или ЗГД")
    if body.status not in svc.CHECK_STATUS:
        raise HTTPException(422, "status: not_checked | checked | issues")
    p = _profile(db, pid)
    before = p.check_status
    p.check_status, p.checked_at, p.checked_by = body.status, datetime.now(timezone.utc), user.id
    if body.comment.strip():
        p.lawyer_comment = body.comment.strip()
    p.history = list(p.history or []) + [{"at": p.checked_at.isoformat(), "by": user.id, "action": "check", "fields": {"check_status": [before, body.status]}}]
    audit(db, user.id, None, "legal_profile", p.id, "check", before={"status": before}, after={"status": body.status, "comment": body.comment})
    db.commit()
    return svc.profile_payload(p, svc.user_names(db))


@router.post("/profiles/{pid}/banks", status_code=201)
def add_bank(pid: str, body: BankIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    p = _profile(db, pid)
    if p.owner_kind == "own":
        _own_editor(user)
    data = body.model_dump(exclude_none=True)
    if data.get("status") and data["status"] not in svc.BANK_STATUS:
        raise HTTPException(422, "status: active | archived")
    if not (data.get("account") or data.get("bank_name")):
        raise HTTPException(422, "Укажите банк или счёт")
    b = BankProfile(profile_id=p.id, **{k: v for k, v in data.items() if k in svc.BANK_FIELDS})
    b.currency = (b.currency or "RUB").upper()[:3]
    b.history = [{"at": datetime.now(timezone.utc).isoformat(), "by": user.id, "action": "created", "fields": {}}]
    db.add(b)
    db.flush()
    audit(db, user.id, None, "bank_profile", b.id, "create", after={"profile": p.id, "bank": b.bank_name, "account": b.account})
    db.commit()
    return svc.bank_payload(b, svc.user_names(db))


def _bank(db: Session, bid: str) -> tuple[BankProfile, LegalProfile]:
    b = db.get(BankProfile, bid)
    if b is None:
        raise HTTPException(404, "Банковский профиль не найден")
    return b, _profile(db, b.profile_id)


@router.put("/banks/{bid}")
def update_bank(bid: str, body: BankIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    b, p = _bank(db, bid)
    if p.owner_kind == "own":
        _own_editor(user)
    data = body.model_dump(exclude_none=True)
    if data.get("status") and data["status"] not in svc.BANK_STATUS:
        raise HTTPException(422, "status: active | archived")
    if "currency" in data:
        data["currency"] = data["currency"].upper()[:3]
    changed = svc.apply_changes(b, data, svc.BANK_FIELDS, user.id)
    if changed and any(k in ("account", "bank_name", "bik", "swift", "corr_account", "beneficiary") for k in changed):
        b.verified, b.verified_at, b.verified_by = False, None, None       # изменились реквизиты — проверка заново
    if changed:
        audit(db, user.id, None, "bank_profile", b.id, "update", before={k: v[0] for k, v in changed.items()}, after={k: v[1] for k, v in changed.items()})
    db.commit()
    return svc.bank_payload(b, svc.user_names(db))


@router.post("/banks/{bid}/archive")
def archive_bank(bid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    b, p = _bank(db, bid)
    if p.owner_kind == "own":
        _own_editor(user)
    svc.apply_changes(b, {"status": "archived", "valid_to": b.valid_to or date.today()}, svc.BANK_FIELDS, user.id, action="archived")
    audit(db, user.id, None, "bank_profile", b.id, "archive")
    db.commit()
    return svc.bank_payload(b, svc.user_names(db))


@router.post("/banks/{bid}/verify")
def verify_bank(bid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Отметка «реквизиты проверены» — ФД, юрист, КД или ЗГД (защита от подмены реквизитов)."""
    if not user.has(Role.FD, Role.LAWYER, Role.CD, Role.GD):
        raise HTTPException(403, "Банковские реквизиты подтверждают ФД, юрист, КД или ЗГД")
    b, _ = _bank(db, bid)
    b.verified, b.verified_at, b.verified_by = True, datetime.now(timezone.utc), user.id
    b.history = list(b.history or []) + [{"at": b.verified_at.isoformat(), "by": user.id, "action": "verified", "fields": {}}]
    audit(db, user.id, None, "bank_profile", b.id, "verify")
    db.commit()
    return svc.bank_payload(b, svc.user_names(db))


@router.get("/own")
def own_companies(db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Карточка БЕРГ-ХАУС: компании группы с юридическими профилями и банковскими профилями."""
    names = svc.user_names(db)
    out = []
    for e in db.scalars(select(LegalEntity).order_by(LegalEntity.sells_to_clients.desc(), LegalEntity.name)).all():
        rows = svc.ensure_profiles(db, "own", e.id)
        out.append({"id": e.id, "code": e.code, "name": e.name, "country": e.country, "currency": e.currency, "sells_to_clients": e.sells_to_clients, "is_active": e.is_active,
                    "profiles": [svc.profile_payload(p, names) for p in rows]})
    db.commit()
    return {"items": out, "can_edit": user.has(Role.CD, Role.GD, Role.FD, Role.LAWYER, Role.ADMIN)}


# ------------------------------------------------------------------ автозаполнение по ИНН, сканы документов, карточка компании

def _docs_dir(pid: str) -> str:
    d = os.path.join(get_settings().storage_local_root, "_legal", "profiles", pid)
    os.makedirs(d, exist_ok=True)
    return d


DOC_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".docx", ".doc", ".zip"}


@router.post("/profiles/{pid}/documents", status_code=201)
async def upload_profile_document(pid: str, file: UploadFile = File(...), title: str = Form(""), number: str = Form(""), issued_on: str = Form(""), valid_until: str = Form(""),
                                  doc_id: str = Form(""), db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Скан документа компании (устав, свидетельство, лицензия, доверенность, карточка предприятия…): файл хранится при профиле.
    doc_id — приложить файл к уже существующей строке документов; иначе создаётся новая строка."""
    p = _profile(db, pid)
    if p.owner_kind == "own":
        _own_editor(user)
    name = os.path.basename(file.filename or "scan")
    ext = os.path.splitext(name)[1].lower()
    if ext not in DOC_EXT:
        raise HTTPException(422, "Допустимые файлы: PDF, JPG, PNG, TIFF, DOCX, ZIP")
    fid = uuid.uuid4().hex
    path = os.path.join(_docs_dir(p.id), f"{fid}{ext}")
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    docs = list(p.documents or [])
    row = next((d for d in docs if d.get("id") == doc_id), None) if doc_id else None
    if row is None:
        row = {"id": uuid.uuid4().hex, "title": title.strip() or os.path.splitext(name)[0], "number": number.strip(), "issued_on": issued_on, "valid_until": valid_until}
        docs.append(row)
    row["file"] = {"id": fid, "name": name, "size": os.path.getsize(path), "uploaded_by": user.id, "uploaded_at": datetime.now(timezone.utc).isoformat()}
    p.documents = docs
    p.history = list(p.history or []) + [{"at": datetime.now(timezone.utc).isoformat(), "by": user.id, "action": "document", "fields": {"documents": ["", f"{row['title']}: {name}"]}}]
    audit(db, user.id, None, "legal_profile", p.id, "document_upload", after={"title": row["title"], "file": name})
    db.commit()
    return svc.profile_payload(p, svc.user_names(db))


@router.get("/profiles/{pid}/documents/{fid}")
def download_profile_document(pid: str, fid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    p = _profile(db, pid)
    row = next((d for d in (p.documents or []) if (d.get("file") or {}).get("id") == fid), None)
    if row is None:
        raise HTTPException(404, "Файл не найден")
    d = _docs_dir(p.id)
    path = next((os.path.join(d, f) for f in os.listdir(d) if f.startswith(fid)), None)
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "Файл не найден на сервере")
    return file_response(path, row["file"]["name"], inline=True)


@router.delete("/profiles/{pid}/documents/{fid}")
def delete_profile_document(pid: str, fid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Убрать файл из строки документа (сама строка остаётся); файл на диске удаляется только КД/ЗГД/админом, иначе отвязывается."""
    p = _profile(db, pid)
    if p.owner_kind == "own":
        _own_editor(user)
    docs = list(p.documents or [])
    row = next((d for d in docs if (d.get("file") or {}).get("id") == fid), None)
    if row is None:
        raise HTTPException(404, "Файл не найден")
    name = row["file"]["name"]
    row.pop("file", None)
    p.documents = docs
    if user.has(Role.CD, Role.GD, Role.ADMIN):
        d = _docs_dir(p.id)
        for f in os.listdir(d):
            if f.startswith(fid):
                os.remove(os.path.join(d, f))
    audit(db, user.id, None, "legal_profile", p.id, "document_remove", before={"file": name})
    db.commit()
    return svc.profile_payload(p, svc.user_names(db))


@router.get("/profiles/{pid}/card")
def company_card(pid: str, fmt: str = "docx", db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Карточка компании для отправки контрагенту: реквизиты, действующие банковские счета, подписант, контакты — DOCX или PDF."""
    from ..services.legal_card import build_company_card, to_pdf
    p = _profile(db, pid)
    d = tempfile.mkdtemp()
    ru = (p.names or {}).get("ru") or {}
    stem = ("Карточка компании — " + (ru.get("short") or ru.get("full") or p.title or "профиль")).replace("/", "-").replace('"', "")[:120]
    path = os.path.join(d, stem + ".docx")
    build_company_card(p, list(p.banks), path)
    if fmt == "pdf":
        pdf = to_pdf(path, d)
        if not pdf:
            shutil.rmtree(d, ignore_errors=True)
            raise HTTPException(501, "PDF недоступен: на сервере нет LibreOffice")
        path = pdf
    audit(db, user.id, None, "legal_profile", p.id, "card_export", after={"format": fmt})
    db.commit()
    return file_response(path, os.path.basename(path), inline=(fmt == "pdf"), background=BackgroundTask(shutil.rmtree, d, True))
