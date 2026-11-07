"""Юридический блок, этап 2: библиотека DOCX-шаблонов и база примеров."""
from __future__ import annotations

import os
import shutil
import tempfile
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..core.auth import CurrentUser, get_current_user
from ..core.config import get_settings
from ..core.files import file_response
from ..core.roles import Role
from ..db import get_db
from ..models import Template, User
from ..services import legal_templates as svc
from ..services.deal_service import audit

router = APIRouter(prefix="/api/legal/templates", tags=["legal"])
ALL_KINDS = {**{k: v["title"] for k, v in svc.LEGAL_KINDS.items()}, **svc.EXAMPLE_KINDS}


def _reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not (user.can("legal_docs") or user.can("legal_profiles")):
        raise HTTPException(403, "Библиотека шаблонов закрыта для вашей роли")
    return user


def _editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(Role.LAWYER, Role.CD, Role.GD, Role.ADMIN):
        raise HTTPException(403, "Шаблоны загружают юрист, КД, ЗГД и администратор")
    return user


def _names(db: Session) -> dict[str, str]:
    return {u.id: u.display_name for u in db.scalars(select(User)).all()}


def _tpl(db: Session, tid: str) -> Template:
    t = db.get(Template, tid)
    if t is None or t.kind not in ALL_KINDS:
        raise HTTPException(404, "Шаблон не найден")
    return t


@router.get("")
def list_templates(db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    names = _names(db)
    rows = db.scalars(select(Template).where(Template.kind.in_(list(ALL_KINDS))).order_by(Template.kind, Template.version.desc())).all()
    return {"kinds": [{"code": k, **v} for k, v in svc.LEGAL_KINDS.items()], "example_kinds": svc.EXAMPLE_KINDS, "languages": svc.LANGUAGES,
            "items": [svc.template_payload(t, names) for t in rows], "catalog": svc.catalog(),
            "can_edit": user.has(Role.LAWYER, Role.CD, Role.GD, Role.ADMIN)}


@router.post("", status_code=201)
async def upload_template(kind: str = Form(...), title: str = Form(""), language: str = Form(""), country: str = Form(""), notes: str = Form(""),
                          file: UploadFile = File(...), db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Загрузка шаблона (DOCX с плейсхолдерами {{…}}) или примера (любой DOCX/PDF). У шаблонов находятся переменные и сверяются с каталогом."""
    if kind not in ALL_KINDS:
        raise HTTPException(422, "Неизвестный вид шаблона")
    is_example = kind in svc.EXAMPLE_KINDS
    name = os.path.basename(file.filename or "template.docx")
    ext = os.path.splitext(name)[1].lower()
    if not is_example and ext != ".docx":
        raise HTTPException(422, "Шаблон должен быть файлом DOCX")
    if is_example and ext not in (".docx", ".pdf", ".doc"):
        raise HTTPException(422, "Пример: DOCX, DOC или PDF")
    lang = language or svc.LEGAL_KINDS.get(kind, {}).get("language", "ru")
    if lang not in svc.LANGUAGES:
        raise HTTPException(422, "Язык: " + ", ".join(svc.LANGUAGES))
    ver = (db.scalar(select(Template.version).where(Template.kind == kind, Template.language == lang).order_by(Template.version.desc())) or 0) + 1
    d = os.path.join(get_settings().storage_local_root, "_templates", "legal")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{kind}_{lang}_v{ver}_{name}".replace(" ", "_"))
    with open(path, "wb") as f:
        f.write(await file.read())
    variables: dict = {}
    if not is_example:
        try:
            variables = svc.analyze_template(path)
        except Exception as e:  # noqa: BLE001
            os.remove(path)
            raise HTTPException(422, f"Файл не читается как DOCX: {e}")
        if not variables["variables"] and not variables["loops"]:
            os.remove(path)
            raise HTTPException(422, "В файле нет плейсхолдеров {{…}} — это не шаблон. Для базы примеров выберите вид «Пример …»")
        # новая версия того же вида и языка становится действующей, прежние — в архив
        for old in db.scalars(select(Template).where(Template.kind == kind, Template.language == lang, Template.is_active.is_(True))).all():
            old.is_active = False
    t = Template(kind=kind, version=ver, file_path=path, uploaded_by=user.id, title=title.strip() or ALL_KINDS[kind], language=lang,
                 country=(country or svc.LEGAL_KINDS.get(kind, {}).get("country", "")).upper()[:8], deal_kind=svc.LEGAL_KINDS.get(kind, {}).get("deal_kind", ""),
                 variables=variables, notes=notes.strip(), is_example=is_example, is_active=True)
    db.add(t)
    db.flush()
    audit(db, user.id, None, "legal_template", t.id, "upload", after={"kind": kind, "version": ver, "file": name, "language": lang, "unknown": variables.get("unknown", [])})
    db.commit()
    return svc.template_payload(t, _names(db))


class TemplateIn(BaseModel):
    title: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None
    country: Optional[str] = None


@router.put("/{tid}")
def update_template(tid: str, body: TemplateIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    t = _tpl(db, tid)
    data = body.model_dump(exclude_none=True)
    if data.get("is_active") and not t.is_example:
        for old in db.scalars(select(Template).where(Template.kind == t.kind, Template.language == t.language, Template.is_active.is_(True), Template.id != t.id)).all():
            old.is_active = False
    for k, v in data.items():
        setattr(t, k, v.strip() if isinstance(v, str) else v)
    audit(db, user.id, None, "legal_template", t.id, "update", after=data)
    db.commit()
    return svc.template_payload(t, _names(db))


@router.get("/{tid}/file")
def download_template(tid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    t = _tpl(db, tid)
    if not os.path.isfile(t.file_path):
        raise HTTPException(404, "Файл шаблона не найден на сервере")
    return file_response(t.file_path, os.path.basename(t.file_path), inline=False)


@router.post("/{tid}/rescan")
def rescan_template(tid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    t = _tpl(db, tid)
    if t.is_example:
        raise HTTPException(422, "Это пример, а не шаблон")
    t.variables = svc.analyze_template(t.file_path)
    db.commit()
    return svc.template_payload(t, _names(db))


@router.get("/{tid}/test-render")
def test_render(tid: str, fmt: str = "docx", db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Тестовое формирование: шаблон заполняется образцовыми данными; в ответе — файл (DOCX или PDF через LibreOffice).
    Незаполненные переменные остаются в тексте как {{…}} — их видно в документе и в заголовке X-Unresolved."""
    from ..services.docx_fill import fill_docx
    t = _tpl(db, tid)
    if t.is_example:
        raise HTTPException(422, "Это пример, а не шаблон")
    d = tempfile.mkdtemp()
    out = os.path.join(d, f"test_{t.kind}_{t.language}.docx")
    res = fill_docx(t.file_path, out, svc.sample_data(t.kind))
    if fmt == "pdf":
        exe = shutil.which("soffice") or shutil.which("libreoffice")
        if not exe:
            shutil.rmtree(d, ignore_errors=True)
            raise HTTPException(501, "PDF недоступен: на сервере нет LibreOffice")
        import subprocess
        subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", d, out], check=False, timeout=120, capture_output=True)
        pdf = out[:-5] + ".pdf"
        if not os.path.isfile(pdf):
            shutil.rmtree(d, ignore_errors=True)
            raise HTTPException(502, "LibreOffice не смог сформировать PDF")
        out = pdf
    resp = file_response(out, os.path.basename(out), inline=(fmt == "pdf"), background=BackgroundTask(shutil.rmtree, d, True))
    resp.headers["X-Unresolved"] = ",".join(res.unresolved)[:900]
    resp.headers["X-Replaced"] = str(res.replaced)
    return resp


@router.get("/{tid}/test-check")
def test_check(tid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Проверка без скачивания: сколько переменных подставлено, какие остались, как раскрылись циклы."""
    from ..services.docx_fill import fill_docx
    t = _tpl(db, tid)
    if t.is_example:
        raise HTTPException(422, "Это пример, а не шаблон")
    d = tempfile.mkdtemp()
    try:
        res = fill_docx(t.file_path, os.path.join(d, "t.docx"), svc.sample_data(t.kind))
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return {"replaced": res.replaced, "unresolved": res.unresolved, "loops": res.loops, "leftovers": res.leftovers, "ok": not res.unresolved and not res.leftovers}
