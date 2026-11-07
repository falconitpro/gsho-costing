"""Доверенности компаний группы: конструктор, реестр, печать (DOCX/PDF), копии и сканы, статусы, отзыв. Оформляют и отзывают юрист, ЗГД и ФД."""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..core.auth import CurrentUser, get_current_user
from ..core.config import get_settings
from ..core.files import file_response
from ..core.roles import Role
from ..db import get_db
from ..models import LegalEntity, LegalProfile, PowerOfAttorney, Template, User
from ..services import legal_poa as svc
from ..services import legal_profiles as lp
from ..services.deal_service import audit, notify

router = APIRouter(prefix="/api/legal/poa", tags=["legal"])


def _reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not (user.can("legal_profiles") or user.can("legal_docs")):
        raise HTTPException(403, "Реестр доверенностей закрыт для вашей роли")
    return user


def _issuer(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(Role.LAWYER, Role.GD, Role.FD):
        raise HTTPException(403, "Оформляет и отзывает доверенности юрист, ЗГД или ФД")
    return user


def _sensitive(user: CurrentUser) -> bool:
    return bool(user.roles & {Role.LAWYER, Role.GD, Role.FD, Role.ADMIN})


def _names(db: Session) -> dict[str, str]:
    return svc.user_names(db)


def _entities(db: Session) -> dict[str, str]:
    return {e.id: e.name for e in db.scalars(select(LegalEntity)).all()}


def _poa(db: Session, pid: str) -> PowerOfAttorney:
    p = db.get(PowerOfAttorney, pid)
    if p is None:
        raise HTTPException(404, "Доверенность не найдена")
    return p


def _files_dir(pid: str) -> str:
    d = os.path.join(get_settings().storage_local_root, "_legal", "poa", pid)
    os.makedirs(d, exist_ok=True)
    return d


def _add_file(poa: PowerOfAttorney, kind: str, src_path: str, name: str, user_id: str) -> dict:
    fid = uuid.uuid4().hex
    ext = os.path.splitext(name)[1].lower() or ".bin"
    dst = os.path.join(_files_dir(poa.id), f"{fid}{ext}")
    shutil.copyfile(src_path, dst)
    f = {"id": fid, "kind": kind, "name": name, "size": os.path.getsize(dst), "uploaded_by": user_id, "uploaded_at": datetime.now(timezone.utc).isoformat()}
    poa.files = list(poa.files or []) + [f]
    return f


def _hist(poa: PowerOfAttorney, user_id: Optional[str], action: str, comment: str = "") -> None:
    poa.history = list(poa.history or []) + [{"at": datetime.now(timezone.utc).isoformat(), "by": user_id, "action": action, "comment": comment}]


class PoaIn(BaseModel):
    entity_id: Optional[str] = None
    profile_id: Optional[str] = None
    kind: Optional[str] = None
    place: Optional[str] = None
    valid_to: Optional[date] = None
    attorney_user_id: Optional[str] = None
    attorney: Optional[dict] = None
    powers: Optional[list[dict]] = None
    limits: Optional[dict] = None
    notes: Optional[str] = None


class IssueIn(BaseModel):
    issued_on: Optional[date] = None
    template_id: Optional[str] = None      # шаблон power_of_attorney из библиотеки; пусто — встроенная форма


class RevokeIn(BaseModel):
    reason: str
    revoked_on: Optional[date] = None
    notify_attorney: bool = True


@router.get("/catalog")
def poa_catalog(user: CurrentUser = Depends(_reader)):
    return {"powers": svc.catalog(), "kinds": svc.KINDS, "statuses": svc.STATUSES, "file_kinds": svc.FILE_KINDS}


@router.get("")
def registry(entity_id: str = "", status: str = "", q: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Реестр доверенностей по всем компаниям группы (или одной): статусы, сроки, истекающие; паспортные данные — только юрист/ЗГД/ФД/админ."""
    ents = _entities(db)
    names = _names(db)
    sens = _sensitive(user)
    stmt = select(PowerOfAttorney).order_by(PowerOfAttorney.created_at.desc())
    if entity_id:
        stmt = stmt.where(PowerOfAttorney.entity_id == entity_id)
    rows = db.scalars(stmt).all()
    changed = any(svc.refresh_status(p) for p in rows)
    if changed:
        db.commit()
    out = []
    for p in rows:
        if status and p.status != status:
            continue
        d = svc.payload(p, names, ents.get(p.entity_id, ""), sens)
        if q and q.lower() not in f"{p.number} {(p.attorney or {}).get('full_name', '')} {(p.attorney or {}).get('position', '')} {ents.get(p.entity_id, '')}".lower():
            continue
        out.append(d)
    all_rows = [svc.payload(p, names, "", False) for p in rows]
    summary = {"total": len(all_rows), **{k: sum(1 for x in all_rows if x["status"] == k) for k in svc.STATUSES}, "expiring": sum(1 for x in all_rows if x["expiring"])}
    return {"items": out, "summary": summary, "statuses": svc.STATUSES, "kinds": svc.KINDS, "entities": [{"id": k, "name": v} for k, v in ents.items()],
            "can_issue": user.has(Role.LAWYER, Role.GD, Role.FD), "sensitive_visible": sens,
            "employees": [{"id": u.id, "display_name": u.display_name, "position": (u.contacts or {}).get("position", "")} for u in db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.display_name)).all()],
            "templates": [{"id": t.id, "kind": t.kind, "title": t.title, "version": t.version} for t in db.scalars(select(Template).where(Template.kind.in_(("power_of_attorney", "poa_revocation")), Template.is_active.is_(True), Template.is_example.is_(False))).all()]}


@router.post("", status_code=201)
def create_poa(body: PoaIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_issuer)):
    """Черновик доверенности от компании группы: доверитель — из юридического профиля компании (снимок), представитель — сотрудник или вручную."""
    if not body.entity_id:
        raise HTTPException(422, "Укажите компанию группы — доверителя")
    ent = db.get(LegalEntity, body.entity_id)
    if ent is None:
        raise HTTPException(404, "Компания группы не найдена")
    profs = lp.ensure_profiles(db, "own", ent.id)
    prof = next((p for p in profs if p.id == body.profile_id), None) or next((p for p in profs if p.is_default and p.is_active), profs[0] if profs else None)
    if prof is None:
        raise HTTPException(409, "У компании нет юридического профиля — заполните карточку БЕРГ-ХАУС")
    if body.kind and body.kind not in svc.KINDS:
        raise HTTPException(422, "Вид: general | special | single")
    attorney = dict(body.attorney or {})
    if body.attorney_user_id:
        u = db.get(User, body.attorney_user_id)
        if u is None:
            raise HTTPException(404, "Сотрудник не найден")
        attorney.setdefault("full_name", u.display_name)
        attorney.setdefault("position", (u.contacts or {}).get("position", ""))
        attorney.setdefault("phone", (u.contacts or {}).get("mobile", ""))
        attorney.setdefault("email", u.email)
    poa = PowerOfAttorney(entity_id=ent.id, profile_id=prof.id, kind=body.kind or "special", place=(body.place or "").strip(), valid_to=body.valid_to, principal=svc.principal_snapshot(prof),
                          attorney_user_id=body.attorney_user_id, attorney=attorney, powers=body.powers or [], limits=body.limits or {}, notes=(body.notes or "").strip(), created_by=user.id, status="draft")
    _hist(poa, user.id, "created", "черновик")
    db.add(poa)
    db.flush()
    audit(db, user.id, None, "poa", poa.id, "create", after={"entity": ent.name, "attorney": attorney.get("full_name", "")})
    db.commit()
    return svc.payload(poa, _names(db), ent.name, True)


@router.get("/{pid}")
def get_poa(pid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    p = _poa(db, pid)
    if svc.refresh_status(p):
        db.commit()
    return svc.payload(p, _names(db), _entities(db).get(p.entity_id, ""), _sensitive(user))


@router.put("/{pid}")
def update_poa(pid: str, body: PoaIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_issuer)):
    """Правка черновика (оформленная доверенность не меняется — только отзыв и новая)."""
    p = _poa(db, pid)
    if p.status != "draft":
        raise HTTPException(409, "Оформленная доверенность не редактируется — отзовите её и оформите новую")
    data = body.model_dump(exclude_none=True, exclude={"entity_id"})
    if "kind" in data and data["kind"] not in svc.KINDS:
        raise HTTPException(422, "Вид: general | special | single")
    if "profile_id" in data:
        prof = db.get(LegalProfile, data["profile_id"])
        if prof is None or prof.owner_id != p.entity_id:
            raise HTTPException(422, "Профиль не принадлежит компании-доверителю")
        p.principal = svc.principal_snapshot(prof)
    if "attorney_user_id" in data and data["attorney_user_id"]:
        u = db.get(User, data["attorney_user_id"])
        if u is None:
            raise HTTPException(404, "Сотрудник не найден")
        att = dict(data.get("attorney") or p.attorney or {})
        att.setdefault("full_name", u.display_name)
        data["attorney"] = att
    for k, v in data.items():
        setattr(p, k, v.strip() if isinstance(v, str) else v)
    _hist(p, user.id, "edited")
    db.commit()
    return svc.payload(p, _names(db), _entities(db).get(p.entity_id, ""), True)


def _validate_for_issue(p: PowerOfAttorney) -> list[str]:
    errs = []
    pr, a = p.principal or {}, p.attorney or {}
    for k, t in (("name", "наименование доверителя"), ("inn", "ИНН доверителя"), ("ogrn", "ОГРН доверителя"), ("address", "адрес доверителя"), ("signatory", "подписант доверителя")):
        if not pr.get(k):
            errs.append(f"Карточка компании: не заполнено {t}")
    if not a.get("full_name"):
        errs.append("Не указан представитель (ФИО)")
    pas = a.get("passport") or {}
    if not (pas.get("series") or pas.get("number")):
        errs.append("Не указаны паспортные данные представителя")
    if not svc.resolve_powers(p.powers or [], p.limits or {}):
        errs.append("Не выбрано ни одного полномочия")
    if not p.valid_to:
        errs.append("Не указан срок действия")
    return errs


@router.post("/{pid}/check")
def check_poa(pid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    p = _poa(db, pid)
    errs = _validate_for_issue(p)
    return {"ok": not errs, "errors": errs}


def _render(db: Session, p: PowerOfAttorney, template_id: Optional[str], out_dir: str, draft: bool) -> str:
    from ..services.docx_fill import fill_docx
    from ..services.legal_packages import active_template
    name = f"Доверенность {p.number or 'проект'} — {(p.attorney or {}).get('full_name', '')}.docx".replace("/", "-")
    path = os.path.join(out_dir, name)
    t = db.get(Template, template_id) if template_id else active_template(db, "power_of_attorney", "ru")
    if t is not None and not t.is_example and os.path.isfile(t.file_path):
        res = fill_docx(t.file_path, path, svc.template_data(p))
        if res.unresolved:
            raise HTTPException(422, {"message": "В шаблоне доверенности остались незаполненные переменные", "errors": res.unresolved})
    else:
        svc.build_poa_docx(p, path, draft=draft)
    return path


@router.get("/{pid}/preview")
def preview_poa(pid: str, fmt: str = "docx", db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Печатная форма черновика (без номера) — посмотреть перед оформлением."""
    p = _poa(db, pid)
    if not _sensitive(user):
        raise HTTPException(403, "Текст доверенности с паспортными данными доступен юристу, ЗГД, ФД и администратору")
    d = tempfile.mkdtemp()
    path = _render(db, p, None, d, draft=(p.status == "draft"))
    if fmt == "pdf":
        pdf = svc.to_pdf(path, d)
        if not pdf:
            shutil.rmtree(d, ignore_errors=True)
            raise HTTPException(501, "PDF недоступен: на сервере нет LibreOffice")
        return file_response(pdf, os.path.basename(pdf), inline=True, background=BackgroundTask(shutil.rmtree, d, True))
    return file_response(path, os.path.basename(path), inline=False, background=BackgroundTask(shutil.rmtree, d, True))


@router.post("/{pid}/issue")
def issue_poa(pid: str, body: IssueIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_issuer)):
    """Оформление: проверка полноты, номер по компании и году, дата выдачи, печатная форма DOCX (+PDF) сохраняется как копия; статус «действует»."""
    p = _poa(db, pid)
    if p.status != "draft":
        raise HTTPException(409, "Доверенность уже оформлена")
    errs = _validate_for_issue(p)
    if errs:
        raise HTTPException(422, {"message": "Доверенность не оформлена — заполните данные", "errors": errs})
    ent = db.get(LegalEntity, p.entity_id)
    on = body.issued_on or date.today()
    if p.valid_to and p.valid_to <= on:
        raise HTTPException(422, "Срок действия должен быть позже даты выдачи")
    p.number, p.seq = svc.next_number(db, ent, on)
    p.issued_on = on
    p.status = "active"
    d = tempfile.mkdtemp()
    try:
        path = _render(db, p, body.template_id, d, draft=False)
        _add_file(p, "issued", path, os.path.basename(path), user.id)
        pdf = svc.to_pdf(path, d)
        if pdf:
            _add_file(p, "issued", pdf, os.path.basename(pdf), user.id)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    _hist(p, user.id, "issued", f"№ {p.number} от {on:%d.%m.%Y}, до {p.valid_to:%d.%m.%Y}")
    audit(db, user.id, None, "poa", p.id, "issue", after={"number": p.number, "issued_on": on.isoformat(), "valid_to": p.valid_to.isoformat(), "attorney": (p.attorney or {}).get("full_name", "")})
    if p.attorney_user_id and p.attorney_user_id != user.id:
        notify(db, None, p.attorney_user_id, "poa", None, f"На вас оформлена доверенность № {p.number} от {on:%d.%m.%Y} ({ent.name}) сроком до {p.valid_to:%d.%m.%Y}")
    db.commit()
    return svc.payload(p, _names(db), ent.name, True)


@router.post("/{pid}/files", status_code=201)
async def upload_poa_file(pid: str, file: UploadFile = File(...), kind: str = Form("signed_scan"), db: Session = Depends(get_db), user: CurrentUser = Depends(_issuer)):
    """Копии: подписанный скан, нотариальная копия, прочее."""
    p = _poa(db, pid)
    if kind not in svc.FILE_KINDS:
        raise HTTPException(422, "Вид файла: " + ", ".join(svc.FILE_KINDS))
    name = os.path.basename(file.filename or "file")
    if os.path.splitext(name)[1].lower() not in (".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".docx", ".zip"):
        raise HTTPException(422, "Файл: PDF, JPG, PNG, TIFF, DOCX или ZIP")
    d = tempfile.mkdtemp()
    try:
        tmp = os.path.join(d, name)
        with open(tmp, "wb") as f:
            f.write(await file.read())
        _add_file(p, kind, tmp, name, user.id)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    _hist(p, user.id, "file", f"{svc.FILE_KINDS[kind]}: {name}")
    audit(db, user.id, None, "poa", p.id, "file", after={"kind": kind, "file": name})
    db.commit()
    return svc.payload(p, _names(db), _entities(db).get(p.entity_id, ""), True)


@router.get("/{pid}/files/{fid}")
def download_poa_file(pid: str, fid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    p = _poa(db, pid)
    f = next((x for x in (p.files or []) if x["id"] == fid), None)
    if f is None:
        raise HTTPException(404, "Файл не найден")
    if not _sensitive(user) and f.get("kind") in ("draft", "issued"):
        raise HTTPException(403, "Текст доверенности с паспортными данными доступен юристу, ЗГД, ФД и администратору")
    d = _files_dir(p.id)
    path = next((os.path.join(d, x) for x in os.listdir(d) if x.startswith(fid)), None)
    if not path:
        raise HTTPException(404, "Файл не найден на сервере")
    return file_response(path, f["name"], inline=True)


@router.delete("/{pid}/files/{fid}")
def delete_poa_file(pid: str, fid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_issuer)):
    p = _poa(db, pid)
    f = next((x for x in (p.files or []) if x["id"] == fid), None)
    if f is None:
        raise HTTPException(404)
    if f.get("kind") == "issued":
        raise HTTPException(409, "Оформленный экземпляр не удаляется")
    p.files = [x for x in p.files if x["id"] != fid]
    d = _files_dir(p.id)
    for x in os.listdir(d):
        if x.startswith(fid):
            os.remove(os.path.join(d, x))
    _hist(p, user.id, "file_removed", f["name"])
    db.commit()
    return svc.payload(p, _names(db), _entities(db).get(p.entity_id, ""), True)


@router.post("/{pid}/revoke")
def revoke_poa(pid: str, body: RevokeIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_issuer)):
    """Отзыв действующей доверенности: причина, дата, уведомление об отмене (DOCX) как копия, уведомление представителю-сотруднику."""
    p = _poa(db, pid)
    if p.status not in ("active", "expired"):
        raise HTTPException(409, "Отозвать можно действующую (или истёкшую) доверенность")
    if len(body.reason.strip()) < 3:
        raise HTTPException(422, "Укажите причину отзыва")
    on = body.revoked_on or date.today()
    p.status = "revoked"
    p.revoked_at = datetime.now(timezone.utc)
    p.revoked_by = user.id
    p.revoke_reason = body.reason.strip()
    d = tempfile.mkdtemp()
    try:
        from ..services.docx_fill import fill_docx
        from ..services.legal_packages import active_template
        name = f"Уведомление об отмене доверенности {p.number}.docx".replace("/", "-")
        path = os.path.join(d, name)
        t = active_template(db, "poa_revocation", "ru")
        if t is not None and os.path.isfile(t.file_path):
            data = svc.template_data(p)
            data["poa"]["revoked_on"] = on.strftime("%d.%m.%Y")
            data["poa"]["revoke_reason"] = p.revoke_reason
            fill_docx(t.file_path, path, data)
        else:
            svc.build_revocation_docx(p, path, p.revoke_reason, on)
        _add_file(p, "revocation", path, name, user.id)
        pdf = svc.to_pdf(path, d)
        if pdf:
            _add_file(p, "revocation", pdf, os.path.basename(pdf), user.id)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    _hist(p, user.id, "revoked", f"с {on:%d.%m.%Y}: {p.revoke_reason}")
    audit(db, user.id, None, "poa", p.id, "revoke", after={"number": p.number, "reason": p.revoke_reason, "on": on.isoformat()})
    ent = _entities(db).get(p.entity_id, "")
    if body.notify_attorney and p.attorney_user_id:
        notify(db, None, p.attorney_user_id, "poa", None, f"Доверенность № {p.number} ({ent}) отозвана с {on:%d.%m.%Y}: {p.revoke_reason}. Оригинал подлежит возврату")
    for u in db.scalars(select(User).where(User.is_active.is_(True))).all():
        if ({"lawyer", "gd", "fd"} & set(u.roles or [])) and u.id != user.id:
            notify(db, None, u.id, "poa", None, f"Отозвана доверенность № {p.number} ({(p.attorney or {}).get('full_name', '')}, {ent}) — {p.revoke_reason}")
    db.commit()
    return svc.payload(p, _names(db), ent, True)


@router.post("/{pid}/cancel")
def cancel_poa(pid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_issuer)):
    """Аннулировать черновик."""
    p = _poa(db, pid)
    if p.status != "draft":
        raise HTTPException(409, "Аннулировать можно только черновик; оформленную — отозвать")
    p.status = "cancelled"
    _hist(p, user.id, "cancelled")
    audit(db, user.id, None, "poa", p.id, "cancel")
    db.commit()
    return svc.payload(p, _names(db), _entities(db).get(p.entity_id, ""), True)


@router.post("/{pid}/renew", status_code=201)
def renew_poa(pid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_issuer)):
    """Новая доверенность на основе существующей (те же представитель, полномочия и ограничения) — черновик для продления."""
    p = _poa(db, pid)
    prof = db.get(LegalProfile, p.profile_id) if p.profile_id else None
    n = PowerOfAttorney(entity_id=p.entity_id, profile_id=p.profile_id, kind=p.kind, place=p.place, valid_to=None, principal=svc.principal_snapshot(prof) if prof else dict(p.principal or {}),
                        attorney_user_id=p.attorney_user_id, attorney=dict(p.attorney or {}), powers=list(p.powers or []), limits=dict(p.limits or {}), notes=f"на основе № {p.number}" if p.number else "",
                        created_by=user.id, status="draft")
    _hist(n, user.id, "created", f"копия доверенности № {p.number or '—'} для продления")
    db.add(n)
    db.flush()
    audit(db, user.id, None, "poa", n.id, "renew_from", after={"source": p.id, "number": p.number})
    db.commit()
    return svc.payload(n, _names(db), _entities(db).get(n.entity_id, ""), True)


@router.post("/{pid}/use-as-basis")
def use_as_basis(pid: str, profile_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_issuer)):
    """Подставить доверенность как основание полномочий подписанта в юридический профиль (подписант = представитель, основание = «доверенности № … от …»)."""
    p = _poa(db, pid)
    if p.status != "active":
        raise HTTPException(409, "Основанием может быть только действующая доверенность")
    prof = db.get(LegalProfile, profile_id)
    if prof is None or prof.owner_kind != "own" or prof.owner_id != p.entity_id:
        raise HTTPException(422, "Профиль должен принадлежать компании-доверителю")
    a = p.attorney or {}
    lp.apply_changes(prof, {"signatory_name": a.get("full_name", ""), "signatory_position": a.get("position", "") or prof.signatory_position,
                            "signatory_basis": f"доверенности № {p.number} от {p.issued_on:%d.%m.%Y}"}, lp.PROFILE_FIELDS, user.id, action="poa_basis")
    audit(db, user.id, None, "legal_profile", prof.id, "poa_basis", after={"poa": p.number})
    db.commit()
    return lp.profile_payload(prof, _names(db))
