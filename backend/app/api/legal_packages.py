"""Юридический блок, этап 3: мастер закупочного пакета, договорный комплект ЗИП, реестр пакетов."""
from __future__ import annotations

import io
import os
import shutil
import tempfile
import zipfile
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .. import adapters
from ..core.auth import CurrentUser, get_current_user
from ..core.files import file_response
from ..core.roles import Role
from ..core.workflow import DealStatus
from ..db import get_db
from ..models import Attachment, Deal, Factory, FeedEntry, LegalEntity, LegalPackage, LegalZipSet, Template
from ..services import legal_packages as svc
from ..services import legal_profiles as lp
from ..services.deal_service import audit, ensure_folders
from ..services.legal_templates import LEGAL_KINDS, LANGUAGES
from .common import get_deal_or_404

router = APIRouter(prefix="/api", tags=["legal"])
LEGAL_STATUSES = (DealStatus.WON, DealStatus.CONTRACT, DealStatus.EXECUTION, DealStatus.CLOSED)


def _deal(db: Session, deal_id: str, user: CurrentUser) -> Deal:
    deal = get_deal_or_404(db, deal_id, user)
    if not user.can("legal_docs"):
        raise HTTPException(403, "Юридические документы закрыты для вашей роли")
    return deal


def _can_build(deal: Deal, user: CurrentUser) -> None:
    if not (user.has(Role.LAWYER, Role.CD, Role.GD) or (user.has(Role.MANAGER) and user.is_manager_of(deal.manager_id))):
        raise HTTPException(403, "Пакет формируют менеджер сделки, юрист, КД или ЗГД")
    if deal.status not in LEGAL_STATUSES:
        raise HTTPException(409, "Юридические документы формируются после выигрыша сделки (стадия контракта)")


class ChoiceIn(BaseModel):
    factory_id: str
    own_entity_id: Optional[str] = None
    own_profile_id: Optional[str] = None
    own_bank_id: Optional[str] = None
    cp_profile_id: Optional[str] = None
    cp_bank_id: Optional[str] = None
    language: str = ""
    templates: dict[str, Optional[str]] = Field(default_factory=dict)
    contract_number: str = ""
    contract_date: Optional[date] = None
    place: str = ""
    notes: str = ""


def _choice(body: ChoiceIn, user: CurrentUser) -> svc.Choice:
    if body.language and body.language not in LANGUAGES:
        raise HTTPException(422, "Неизвестный язык документов")
    return svc.Choice(**body.model_dump(exclude={"notes"}))


def _templates_for(db: Session, ctx: dict, override: dict[str, Optional[str]]) -> dict[str, Optional[Template]]:
    """Шаблон на каждый документ: явный выбор (id, "" — встроенный) или действующий по виду/стране/языку."""
    country = ctx["factory"]["country"]
    kinds = {"purchase_contract": svc.CONTRACT_KIND_BY_COUNTRY.get(country, "purchase_contract_other"), "spec_equipment": "spec_equipment", "spec_zip": "spec_zip"}
    out: dict[str, Optional[Template]] = {}
    for doc, kind in kinds.items():
        if doc in override:
            tid = override[doc]
            if not tid:
                out[doc] = None
                continue
            t = db.get(Template, tid)
            if t is None or t.is_example or not t.is_active:
                raise HTTPException(422, f"Шаблон для «{svc.DOC_TITLES[doc]}» не найден или в архиве")
            out[doc] = t
        else:
            out[doc] = svc.active_template(db, kind, ctx["language"])
    return out


def _check_entity_choice(db: Session, body: ChoiceIn, user: CurrentUser) -> None:
    """Покупатель по умолчанию — компания РФ; другая компания группы — только ФД, ЗГД, КД (решение владельца)."""
    if not body.own_entity_id:
        return
    e = db.get(LegalEntity, body.own_entity_id)
    if e is None:
        raise HTTPException(404, "Компания группы не найдена")
    if not e.sells_to_clients and not user.has(Role.FD, Role.GD, Role.CD):
        raise HTTPException(403, "Выбрать другую компанию группы покупателем могут ФД, ЗГД или КД")


@router.get("/deals/{deal_id}/legal/purchase/options")
def purchase_options(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Данные для мастера: поставщики из позиций, компании группы с профилями и счетами, профили поставщика, шаблоны, языки, состояние ЗИП."""
    deal = _deal(db, deal_id, user)
    suppliers = svc.suppliers_in_deal(db, deal)
    entities = []
    for e in db.scalars(select(LegalEntity).where(LegalEntity.is_active.is_(True)).order_by(LegalEntity.sells_to_clients.desc(), LegalEntity.name)).all():
        profs = lp.ensure_profiles(db, "own", e.id)
        entities.append({"id": e.id, "name": e.name, "code": e.code, "default": e.sells_to_clients, "currency": e.currency,
                         "profiles": [{"id": p.id, "title": p.title, "is_default": p.is_default, "summary": lp.profile_summary(p), "check_status": p.check_status,
                                       "banks": [{"id": b.id, "title": b.title, "currency": b.currency, "bank_name": b.bank_name, "account": b.account, "verified": b.verified} for b in p.banks if b.status == "active"]} for p in profs if p.is_active]})
    sup_profiles = {}
    for s in suppliers:
        profs = lp.ensure_profiles(db, "factory", s["id"])
        zs = svc.zip_set_for(db, deal, s["id"], create=False)
        sup_profiles[s["id"]] = {"profiles": [{"id": p.id, "title": p.title, "is_default": p.is_default, "summary": lp.profile_summary(p), "check_status": p.check_status,
                                               "banks": [{"id": b.id, "title": b.title, "currency": b.currency, "bank_name": b.bank_name, "account": b.account, "swift": b.swift, "verified": b.verified} for b in p.banks if b.status == "active"]} for p in profs if p.is_active],
                                 "default_language": svc.DEFAULT_LANGUAGE.get(s["country"], "ru-en"), "contract_kind": svc.CONTRACT_KIND_BY_COUNTRY.get(s["country"], "purchase_contract_other"),
                                 "zip_set": {"confirmed": bool(zs and zs.confirmed_at), "lines": len(zs.lines) if zs else 0, "selected": sum(1 for l in (zs.lines if zs else []) if l.get("selected"))}}
    db.commit()
    templates = [{"id": t.id, "kind": t.kind, "title": t.title, "version": t.version, "language": t.language, "country": t.country, "unknown": (t.variables or {}).get("unknown", [])}
                 for t in db.scalars(select(Template).where(Template.kind.in_(list(LEGAL_KINDS)), Template.is_active.is_(True), Template.is_example.is_(False)).order_by(Template.kind, Template.version.desc())).all()]
    return {"suppliers": suppliers, "entities": entities, "supplier_profiles": sup_profiles, "templates": templates, "languages": LANGUAGES, "doc_titles": svc.DOC_TITLES,
            "can_choose_entity": user.has(Role.FD, Role.GD, Role.CD), "can_build": (user.has(Role.LAWYER, Role.CD, Role.GD) or (user.has(Role.MANAGER) and user.is_manager_of(deal.manager_id))) and deal.status in LEGAL_STATUSES,
            "deal_version": {"id": deal.approved_version_id}, "contract_number": deal.factory_contract_number or "", "contract_date": deal.factory_contract_signed_at}


# ------------------------------------------------------------------ договорный комплект ЗИП

class ZipLinesIn(BaseModel):
    lines: list[dict]
    pricing_mode: Optional[str] = None


@router.get("/deals/{deal_id}/legal/zip-set/{factory_id}")
def get_zip_set(deal_id: str, factory_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = _deal(db, deal_id, user)
    if db.get(Factory, factory_id) is None:
        raise HTTPException(404, "Поставщик не найден")
    zs = svc.zip_set_for(db, deal, factory_id)
    db.commit()
    names = svc.user_names(db)
    return {"id": zs.id, "factory_id": factory_id, "lines": zs.lines, "pricing_mode": zs.pricing_mode, "confirmed_at": zs.confirmed_at, "confirmed_by_name": names.get(zs.confirmed_by or "", ""),
            "rows": svc.zip_rows(zs), "can_edit": user.has(Role.MANAGER, Role.SERVICE_HEAD, Role.LAWYER, Role.CD, Role.GD)}


@router.put("/deals/{deal_id}/legal/zip-set/{factory_id}")
def put_zip_set(deal_id: str, factory_id: str, body: ZipLinesIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Состав договорного ЗИП: выбор строк, количество для договора, цены, «включено в стоимость», ручные строки. Подтверждение снимается — нужно подтвердить заново."""
    import uuid
    deal = _deal(db, deal_id, user)
    if not user.has(Role.MANAGER, Role.SERVICE_HEAD, Role.LAWYER, Role.CD, Role.GD):
        raise HTTPException(403, "Договорный ЗИП ведут менеджер, сервис, юрист, КД и ЗГД")
    zs = svc.zip_set_for(db, deal, factory_id)
    lines = []
    for l in body.lines:
        if not (str(l.get("article") or "").strip() or str(l.get("name") or "").strip()):
            continue
        row = {k: l.get(k) for k in ("position_id", "position", "article", "name", "name_zh", "assembly", "unit", "qty", "unit_price", "currency", "interval_hours", "kind", "lead_days", "included", "selected", "source", "comment")}
        row["id"] = l.get("id") or uuid.uuid4().hex
        row["qty"] = float(row.get("qty") or 0)
        row["unit"] = row.get("unit") or "шт"
        row["currency"] = (row.get("currency") or "CNY").upper()[:3]
        row["kind"] = row.get("kind") if row.get("kind") in ("supply", "to") else "supply"
        row["source"] = row.get("source") or "manual"
        row["selected"] = bool(row.get("selected"))
        row["included"] = bool(row.get("included"))
        lines.append(row)
    if body.pricing_mode:
        if body.pricing_mode not in ("included", "separate"):
            raise HTTPException(422, "pricing_mode: included | separate")
        zs.pricing_mode = body.pricing_mode
    zs.lines = lines
    zs.confirmed_at, zs.confirmed_by = None, None
    zs.history = list(zs.history or []) + [{"at": datetime.now(timezone.utc).isoformat(), "by": user.id, "action": "edit", "lines": len(lines), "selected": sum(1 for l in lines if l["selected"])}]
    audit(db, user.id, deal.id, "legal_zip_set", zs.id, "edit", after={"lines": len(lines), "selected": sum(1 for l in lines if l["selected"])})
    db.commit()
    return get_zip_set(deal_id, factory_id, db, user)


@router.post("/deals/{deal_id}/legal/zip-set/{factory_id}/confirm")
def confirm_zip_set(deal_id: str, factory_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = _deal(db, deal_id, user)
    if not user.has(Role.MANAGER, Role.SERVICE_HEAD, Role.LAWYER, Role.CD, Role.GD):
        raise HTTPException(403)
    zs = svc.zip_set_for(db, deal, factory_id)
    for l in zs.lines:
        if l.get("selected") and l.get("unit_price") in (None, "") and zs.pricing_mode == "separate":
            raise HTTPException(422, f"Строка «{l.get('article') or l.get('name')}»: ЗИП оплачивается отдельно — нужна цена")
    zs.confirmed_at, zs.confirmed_by = datetime.now(timezone.utc), user.id
    zs.history = list(zs.history or []) + [{"at": zs.confirmed_at.isoformat(), "by": user.id, "action": "confirm", "selected": sum(1 for l in zs.lines if l.get("selected"))}]
    audit(db, user.id, deal.id, "legal_zip_set", zs.id, "confirm")
    db.commit()
    return get_zip_set(deal_id, factory_id, db, user)


@router.post("/deals/{deal_id}/legal/zip-set/{factory_id}/reset")
def reset_zip_set(deal_id: str, factory_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Заново предзаполнить из сервисного ЗИП и RFQ (ручные строки сохраняются)."""
    deal = _deal(db, deal_id, user)
    if not user.has(Role.MANAGER, Role.SERVICE_HEAD, Role.LAWYER, Role.CD, Role.GD):
        raise HTTPException(403)
    zs = svc.zip_set_for(db, deal, factory_id)
    manual = [l for l in zs.lines if l.get("source") == "manual"]
    zs.lines = svc.initial_zip_lines(db, deal, factory_id) + manual
    zs.confirmed_at, zs.confirmed_by = None, None
    db.commit()
    return get_zip_set(deal_id, factory_id, db, user)


# ------------------------------------------------------------------ предпросмотр и формирование

@router.post("/deals/{deal_id}/legal/purchase/preview")
def purchase_preview(deal_id: str, body: ChoiceIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Собирает данные пакета без формирования файлов: стороны, позиции, ЗИП, итоги, источники значений, проверки."""
    deal = _deal(db, deal_id, user)
    _check_entity_choice(db, body, user)
    try:
        ctx = svc.build_context(db, deal, _choice(body, user))
    except ValueError as e:
        raise HTTPException(404, str(e))
    templates = _templates_for(db, ctx, body.templates)
    checks = svc.check_purchase(ctx, templates)
    db.commit()
    return {"context": ctx, "checks": checks, "templates": {k: ({"id": t.id, "title": t.title, "version": t.version, "language": t.language} if t else None) for k, t in templates.items()}}


@router.post("/deals/{deal_id}/legal/purchase/build", status_code=201)
def purchase_build(deal_id: str, body: ChoiceIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Формирует пакет: договор + приложения № 1 и № 2 (DOCX), сохраняет в папку сделки «08 Документы клиента и юридические», создаёт версию пакета.
    При блокирующих ошибках пакет не формируется (422 со списком)."""
    deal = _deal(db, deal_id, user)
    _can_build(deal, user)
    _check_entity_choice(db, body, user)
    try:
        ctx = svc.build_context(db, deal, _choice(body, user))
    except ValueError as e:
        raise HTTPException(404, str(e))
    templates = _templates_for(db, ctx, body.templates)
    checks = svc.check_purchase(ctx, templates)
    if checks["errors"]:
        raise HTTPException(422, {"message": "Пакет не сформирован — есть блокирующие ошибки", "errors": checks["errors"], "warnings": checks["warnings"]})
    prev = db.scalars(select(LegalPackage).where(LegalPackage.deal_id == deal.id, LegalPackage.kind == "purchase", LegalPackage.counterparty_id == ctx["factory"]["id"]).order_by(LegalPackage.version_no.desc())).all()
    version_no = (prev[0].version_no + 1) if prev else 1
    author = user.display_name
    d = tempfile.mkdtemp()
    try:
        docs = svc.render_documents(db, ctx, templates, d, author, version_no)
        unresolved = [f"{x['title']}: {', '.join(x['unresolved'])}" for x in docs if x["unresolved"]]
        if unresolved:
            raise HTTPException(422, {"message": "В шаблоне остались незаполненные переменные", "errors": unresolved, "warnings": checks["warnings"]})
        ensure_folders(db, deal)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        files = []
        for x in docs:
            name = os.path.basename(x["path"])
            stem, ext = os.path.splitext(name)
            stored = adapters.storage().upload(x["path"], deal.storage_folder, "08_legal", f"{stem} [v{version_no} {stamp}]{ext}")
            att = Attachment(deal_id=deal.id, folder="08_legal", entity="legal_package", entity_id="", category="contract_draft", comment=f"{x['title']} · пакет v{version_no} · {ctx['factory']['short_name']}",
                             file_name=name, storage_path=stored.path, storage_url=stored.url, sha256=stored.sha256, size=stored.size, uploaded_by=user.id)
            db.add(att)
            db.flush()
            files.append({"kind": x["kind"], "title": x["title"], "file_name": name, "attachment_id": att.id, "size": stored.size, "format": "docx", "template": x["template"]})
    finally:
        shutil.rmtree(d, ignore_errors=True)
    pkg = LegalPackage(deal_id=deal.id, kind="purchase", counterparty_kind="factory", counterparty_id=ctx["factory"]["id"], version_no=version_no,
                       deal_version_id=ctx["deal_version"]["id"], deal_version_no=ctx["deal_version"]["no"], own_entity_id=(ctx["own_entity"] or {}).get("id"),
                       own_profile_id=(ctx["buyer"] or {}).get("profile_id"), own_bank_id=((ctx["buyer"] or {}).get("bank") or {}).get("id"),
                       cp_profile_id=(ctx["seller"] or {}).get("profile_id"), cp_bank_id=((ctx["seller"] or {}).get("bank") or {}).get("id"), language=ctx["language"],
                       templates={k: ({"id": t.id, "version": t.version, "title": t.title} if t else None) for k, t in templates.items()}, rules_version="",
                       snapshot={**ctx, "zip_set_lines": (svc.zip_set_for(db, deal, ctx["factory"]["id"], create=False).lines if ctx["zip_set"]["id"] else [])}, checks=checks, files=files,
                       status="draft", stage_code="factory_contract", notes=body.notes.strip(), created_by=user.id,
                       approvals=[{"at": datetime.now(timezone.utc).isoformat(), "by": user.id, "from": "", "to": "draft", "comment": "сформирован"}])
    db.add(pkg)
    db.flush()
    for att_f in files:
        a = db.get(Attachment, att_f["attachment_id"])
        a.entity_id = pkg.id
    for p in prev:
        if p.status not in ("signed", "archived", "superseded"):
            p.status, p.superseded_by = "superseded", pkg.id
            p.approvals = list(p.approvals or []) + [{"at": datetime.now(timezone.utc).isoformat(), "by": user.id, "from": p.status, "to": "superseded", "comment": f"заменён версией v{version_no}"}]
    db.add(FeedEntry(deal_id=deal.id, kind="system", subject=f"Юридический пакет (закупка у {ctx['factory']['short_name']}) v{version_no} сформирован: договор, приложения № 1 и № 2 — черновик",
                     body="; ".join(checks["warnings"][:5]), user_id=user.id))
    audit(db, user.id, deal.id, "legal_package", pkg.id, "build", after={"factory": ctx["factory"]["short_name"], "version": version_no, "files": [f["file_name"] for f in files], "warnings": len(checks["warnings"])})
    db.commit()
    return svc.package_payload(pkg, svc.user_names(db))


# ------------------------------------------------------------------ реестр пакетов, файлы, статусы

@router.get("/deals/{deal_id}/legal/packages")
def deal_packages(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = _deal(db, deal_id, user)
    names = svc.user_names(db)
    rows = db.scalars(select(LegalPackage).where(LegalPackage.deal_id == deal.id).order_by(LegalPackage.created_at.desc())).all()
    return {"items": [svc.package_payload(p, names) for p in rows], "statuses": svc.STATUS_TITLES}


@router.get("/legal/packages")
def all_packages(status: str = "", kind: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Реестр договоров и пакетов по всем доступным сделкам."""
    from .common import can_see_deal
    if not user.can("legal_docs"):
        raise HTTPException(403)
    names = svc.user_names(db)
    out = []
    for p in db.scalars(select(LegalPackage).order_by(LegalPackage.created_at.desc())).all():
        deal = db.get(Deal, p.deal_id)
        if deal is None or not can_see_deal(user, deal):
            continue
        if (status and p.status != status) or (kind and p.kind != kind):
            continue
        out.append({**svc.package_payload(p, names), "deal_number": deal.number, "client_name": deal.client.name if deal.client else ""})
    return {"items": out, "statuses": svc.STATUS_TITLES}


def _pkg(db: Session, pid: str, user: CurrentUser) -> tuple[LegalPackage, Deal]:
    p = db.get(LegalPackage, pid)
    if p is None:
        raise HTTPException(404, "Пакет не найден")
    deal = _deal(db, p.deal_id, user)
    return p, deal


@router.get("/legal/packages/{pid}")
def package_detail(pid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    p, _ = _pkg(db, pid, user)
    return {**svc.package_payload(p, svc.user_names(db)), "snapshot": p.snapshot}


def _download_attachment(db: Session, att_id: str) -> tuple[str, str, str]:
    att = db.get(Attachment, att_id)
    if att is None:
        raise HTTPException(404, "Файл не найден")
    d = tempfile.mkdtemp()
    local = os.path.join(d, att.file_name)
    adapters.storage().download(att.storage_path, local)
    return local, d, att.file_name


@router.get("/legal/packages/{pid}/files/{kind}")
def package_file(pid: str, kind: str, fmt: str = "docx", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Один документ пакета: DOCX (редактируемый) или PDF для просмотра (LibreOffice)."""
    p, _ = _pkg(db, pid, user)
    f = next((x for x in p.files if x["kind"] == kind), None)
    if f is None:
        raise HTTPException(404, "Документ не найден")
    local, d, name = _download_attachment(db, f["attachment_id"])
    if fmt == "pdf":
        from ..services.legal_card import to_pdf
        pdf = to_pdf(local, d)
        if not pdf:
            shutil.rmtree(d, ignore_errors=True)
            raise HTTPException(501, "PDF недоступен: на сервере нет LibreOffice")
        return file_response(pdf, os.path.splitext(name)[0] + ".pdf", inline=True, background=BackgroundTask(shutil.rmtree, d, True))
    return file_response(local, name, inline=False, background=BackgroundTask(shutil.rmtree, d, True))


@router.get("/legal/packages/{pid}/zip")
def package_zip(pid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Все документы пакета одним ZIP-архивом."""
    p, deal = _pkg(db, pid, user)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in p.files:
            local, d, name = _download_attachment(db, f["attachment_id"])
            z.write(local, name)
            shutil.rmtree(d, ignore_errors=True)
    buf.seek(0)
    cp = (p.snapshot or {}).get("factory", {}).get("short_name") or "пакет"
    fname = f"Пакет документов — {deal.number} — {cp} — v{p.version_no}.zip".replace("/", "-")
    from urllib.parse import quote
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}", "X-Content-Type-Options": "nosniff"})


class StatusIn(BaseModel):
    status: str
    comment: str = ""


@router.post("/legal/packages/{pid}/status")
def package_status(pid: str, body: StatusIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Согласование пакета: черновик → проверка юриста → коммерческое согласование → утверждён → подписан; возврат в черновик; архив."""
    p, deal = _pkg(db, pid, user)
    if body.status not in svc.TRANSITIONS.get(p.status, []):
        raise HTTPException(409, f"Из статуса «{svc.STATUS_TITLES.get(p.status)}» нельзя перейти в «{svc.STATUS_TITLES.get(body.status, body.status)}»")
    allowed = svc.TRANSITION_ROLES.get(body.status, set())
    is_mgr = user.has(Role.MANAGER) and user.is_manager_of(deal.manager_id)
    if not (any(user.has(Role(r)) for r in allowed if r != "manager") or ("manager" in allowed and is_mgr)):
        raise HTTPException(403, f"Статус «{svc.STATUS_TITLES.get(body.status)}» ставят: {', '.join(allowed)}")
    if body.status == "signed" and not deal.factory_contract_signed_at:
        raise HTTPException(409, "Сначала отметьте подписание договора с заводом в реквизитах контракта (номер и дата)")
    if body.status == "draft" and len(body.comment.strip()) < 3:
        raise HTTPException(422, "Укажите причину возврата в черновик")
    before = p.status
    p.status = body.status
    p.approvals = list(p.approvals or []) + [{"at": datetime.now(timezone.utc).isoformat(), "by": user.id, "from": before, "to": body.status, "comment": body.comment.strip()}]
    db.add(FeedEntry(deal_id=deal.id, kind="system", subject=f"Юридический пакет v{p.version_no}: {svc.STATUS_TITLES.get(before)} → {svc.STATUS_TITLES.get(body.status)}", body=body.comment.strip(), user_id=user.id))
    audit(db, user.id, deal.id, "legal_package", p.id, "status", {"status": before}, {"status": body.status, "comment": body.comment})
    db.commit()
    return svc.package_payload(p, svc.user_names(db))
