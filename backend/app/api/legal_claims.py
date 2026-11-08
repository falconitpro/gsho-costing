"""Претензии по договорам: реестр, создание из просроченных обязательств, подпись КД или ЗГД, отправка, ответ, суд / закрытие, печатная форма."""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
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
from ..models import Contract, ContractClaim
from ..services import legal_claims as svc
from ..services import legal_contracts as cs
from ..services.deal_service import audit
from .legal_contracts import _can_see, _get as _get_contract

router = APIRouter(prefix="/api/legal", tags=["legal"])
WORK_ROLES = (Role.LAWYER, Role.GD, Role.CD, Role.FD)


def _claim(db: Session, cid: str, user: CurrentUser) -> tuple[ContractClaim, Contract]:
    cl = db.get(ContractClaim, cid)
    c = db.get(Contract, cl.contract_id) if cl else None
    if cl is None or c is None or not _can_see(db, user, c):
        raise HTTPException(404, "Претензия не найдена")
    return cl, c


def _can_work(user: CurrentUser, c: Contract) -> bool:
    return user.has(*WORK_ROLES) or c.curator_id == user.id or c.created_by == user.id


def _out(db: Session, user: CurrentUser, cl: ContractClaim, c: Contract) -> dict:
    return {**svc.payload(db, cl), "can_work": _can_work(user, c), "can_sign": user.has(Role.CD, Role.GD)}


def _dir(cid: str) -> str:
    d = os.path.join(get_settings().storage_local_root, "_legal", "claims", cid)
    os.makedirs(d, exist_ok=True)
    return d


def _add_file(cl: ContractClaim, kind: str, src: str, name: str, user_id: Optional[str]) -> None:
    fid = uuid.uuid4().hex
    ext = os.path.splitext(name)[1].lower()
    shutil.copyfile(src, os.path.join(_dir(cl.id), f"{fid}{ext}"))
    cl.files = list(cl.files or []) + [{"id": fid, "kind": kind, "name": name, "size": os.path.getsize(src), "uploaded_by": user_id, "uploaded_at": datetime.now(timezone.utc).isoformat()}]


FILE_KINDS = {"form": "Печатная форма", "signed_scan": "Подписанный скан", "post_receipt": "Почтовая квитанция / опись", "answer": "Ответ на претензию", "incoming": "Входящая претензия", "other": "Прочее"}


# ------------------------------------------------------------------ реестр
@router.get("/claims")
def registry(direction: str = "", status: str = "", q: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    rows = db.scalars(select(ContractClaim).order_by(ContractClaim.created_at.desc())).all()
    names = cs._names(db)
    items = []
    for cl in rows:
        c = db.get(Contract, cl.contract_id)
        if c is None or not _can_see(db, user, c):
            continue
        if (direction and cl.direction != direction) or (status and cl.status != status):
            continue
        x = svc.payload(db, cl, names, full=False)
        if q and q.lower() not in f"{x['number']} {x['contract_number']} {x['cp_name']}".lower():
            continue
        items.append(x)
    opened = [x for x in items if x["status"] in svc.OPEN]
    rub = lambda xs: round(sum(cs.to_rub(db, x["total"], x["currency"]) or 0 for x in xs), 2)   # noqa: E731
    return {"items": items, "summary": {"open": len(opened), "signing": sum(1 for x in items if x["status"] == "signing"), "draft": sum(1 for x in items if x["status"] == "draft"),
                                        "answer_overdue": sum(1 for x in items if x["answer_overdue"]),
                                        "outgoing_rub": rub([x for x in opened if x["direction"] == "outgoing"]), "incoming_rub": rub([x for x in opened if x["direction"] == "incoming"])}}


@router.get("/contracts/{cid}/claims")
def contract_claims(cid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    c = _get_contract(db, cid, user)
    rows = db.scalars(select(ContractClaim).where(ContractClaim.contract_id == c.id).order_by(ContractClaim.created_at.desc())).all()
    names = cs._names(db)
    return {"items": [svc.payload(db, x, names, full=False) for x in rows], "available_out": svc.lines_for(db, c, None, "cp"), "available_in": svc.lines_for(db, c, None, "us")}


class ClaimIn(BaseModel):
    direction: str = "outgoing"
    keys: Optional[list[str]] = None
    lines: Optional[list[dict]] = None
    number: str = ""
    issued_on: Optional[date] = None
    demand: str = ""
    answer_days: Optional[int] = None


def _norm_lines(lines: list[dict]) -> list[dict]:
    out = []
    for x in lines or []:
        if x.get("kind") not in svc.LINE_KINDS or not str(x.get("title") or "").strip():
            raise HTTPException(422, "Строка претензии: вид (долг / исполнение / неустойка) и описание")
        amt = x.get("amount")
        if amt not in (None, "") and float(amt) < 0:
            raise HTTPException(422, "Сумма не может быть отрицательной")
        out.append({**x, "amount": None if amt in (None, "") else float(amt), "title": str(x["title"]).strip()})
    return out


@router.post("/contracts/{cid}/claims", status_code=201)
def create_claim(cid: str, body: ClaimIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Исходящая претензия из просроченных обязательств контрагента (долг / исполнение + неустойка) или регистрация входящей."""
    c = _get_contract(db, cid, user)
    if c.status not in cs.LOCKED:
        raise HTTPException(409, "Претензия оформляется по подписанному договору")
    if not _can_work(user, c):
        raise HTTPException(403, "Претензии готовят юрист, ФД, КД, ЗГД или куратор договора")
    if body.direction not in ("outgoing", "incoming"):
        raise HTTPException(422, "direction: outgoing | incoming")
    lines = _norm_lines(body.lines) if body.lines is not None else None
    cl = svc.create(db, c, user.id, body.direction, body.keys, "manual", lines, body.number.strip(), body.issued_on)
    if not cl.lines:
        db.rollback()
        raise HTTPException(422, "Нет просроченных обязательств для претензии — добавьте строки вручную")
    cl.demand = body.demand.strip()
    if body.answer_days:
        cl.answer_days = body.answer_days
        if cl.direction == "incoming" and cl.sent_on:
            cl.answer_due = cl.sent_on + timedelta(days=cl.answer_days)
    audit(db, user.id, c.deal_id, "claim", cl.id, "create", after={"number": cl.number, "direction": cl.direction, **svc.totals(cl)})
    db.commit()
    return _out(db, user, cl, c)


@router.get("/claims/{clid}")
def get_claim(clid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    cl, c = _claim(db, clid, user)
    return _out(db, user, cl, c)


class ClaimEdit(BaseModel):
    lines: Optional[list[dict]] = None
    demand: Optional[str] = None
    answer_days: Optional[int] = None
    number: Optional[str] = None
    track: Optional[str] = None


@router.put("/claims/{clid}")
def edit_claim(clid: str, body: ClaimEdit, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    cl, c = _claim(db, clid, user)
    if not _can_work(user, c):
        raise HTTPException(403)
    data = body.model_dump(exclude_unset=True)
    editable = cl.status in ("draft",) if cl.direction == "outgoing" else cl.status in ("received",)
    if any(k in data for k in ("lines", "demand", "answer_days")) and not editable:
        raise HTTPException(409, "Претензия уже на подписи или отправлена — верните её в черновик")
    if "lines" in data:
        cl.lines = _norm_lines(data["lines"] or [])
    if "demand" in data:
        cl.demand = (data["demand"] or "").strip()
    if data.get("answer_days"):
        cl.answer_days = int(data["answer_days"])
        if cl.direction == "incoming" and cl.sent_on:
            cl.answer_due = cl.sent_on + timedelta(days=cl.answer_days)
    if "number" in data and cl.direction == "incoming":
        cl.number = (data["number"] or "").strip() or cl.number
    if "track" in data:
        cl.track = (data["track"] or "").strip()
    svc.hist(cl, user.id, "edited", ", ".join(data.keys()))
    db.commit()
    return _out(db, user, cl, c)


@router.post("/claims/{clid}/refresh")
def refresh_claim(clid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Пересчитать строки черновика на сегодня (просрочка и неустойка растут каждый день)."""
    cl, c = _claim(db, clid, user)
    if cl.status != "draft" or not _can_work(user, c):
        raise HTTPException(409, "Пересчитывается черновик")
    keys = list(dict.fromkeys(x.get("key") for x in cl.lines or [] if x.get("key")))
    manual = [x for x in cl.lines or [] if not x.get("key")]
    cl.lines = svc.lines_for(db, c, keys or None, "cp") + manual
    svc.hist(cl, user.id, "refreshed", f"расчёт на {date.today():%d.%m.%Y}")
    db.commit()
    return _out(db, user, cl, c)


class StepIn(BaseModel):
    comment: str = ""
    sent_on: Optional[date] = None
    sent_via: str = ""
    track: str = ""
    result: str = ""
    amount: Optional[float] = None
    answer_on: Optional[date] = None
    status: str = ""


@router.post("/claims/{clid}/to-sign")
def to_sign(clid: str, body: StepIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    cl, c = _claim(db, clid, user)
    if cl.direction != "outgoing" or cl.status != "draft":
        raise HTTPException(409, "На подпись передаётся черновик исходящей претензии")
    if not _can_work(user, c):
        raise HTTPException(403)
    if not cl.lines:
        raise HTTPException(422, "В претензии нет требований")
    cl.status = "signing"
    svc.hist(cl, user.id, "to_sign", body.comment.strip())
    cs.notify_users(db, c, [u.id for u in cs.role_users(db, "cd", "gd")], f"Претензия {cl.number} ({cs.cp_display(db, c)}, {svc._money(svc.totals(cl)['total'])} {cl.currency}) ждёт подписи КД или ЗГД", "claim")
    db.commit()
    return _out(db, user, cl, c)


@router.post("/claims/{clid}/sign")
def sign(clid: str, body: StepIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Подписывает КД или ЗГД: печатная форма с подписантом сохраняется в файлы претензии (DOCX и PDF)."""
    cl, c = _claim(db, clid, user)
    if not user.has(Role.CD, Role.GD):
        raise HTTPException(403, "Претензию подписывает КД или ЗГД")
    if cl.status not in ("signing", "draft") or cl.direction != "outgoing":
        raise HTTPException(409, "Подписывается претензия на подписи")
    cl.signer_id, cl.signer_role = user.id, ("gd" if user.has(Role.GD) else "cd")
    cl.signed_at, cl.issued_on, cl.status = datetime.now(timezone.utc), date.today(), "signed"
    d = tempfile.mkdtemp()
    try:
        path = _render(db, cl, d, draft=False)
        _add_file(cl, "form", path, os.path.basename(path), user.id)
        from ..services.legal_poa import to_pdf
        pdf = to_pdf(path, d)
        if pdf:
            _add_file(cl, "form", pdf, os.path.basename(pdf), user.id)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    svc.hist(cl, user.id, "signed", f"{svc.SIGNER_TITLES[cl.signer_role]} {user.display_name}" + (f" · {body.comment.strip()}" if body.comment.strip() else ""))
    cs.notify_users(db, c, [x for x in [c.curator_id, cl.created_by] if x and x != user.id], f"Претензия {cl.number} подписана — отправьте контрагенту и отметьте отправку", "claim")
    audit(db, user.id, c.deal_id, "claim", cl.id, "sign")
    db.commit()
    return _out(db, user, cl, c)


@router.post("/claims/{clid}/send")
def send(clid: str, body: StepIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    cl, c = _claim(db, clid, user)
    if cl.direction != "outgoing" or cl.status != "signed":
        raise HTTPException(409, "Отправляется подписанная претензия")
    if not _can_work(user, c):
        raise HTTPException(403)
    if body.sent_via and body.sent_via not in svc.SENT_VIA:
        raise HTTPException(422, "Способ: " + ", ".join(svc.SENT_VIA))
    cl.sent_on = body.sent_on or date.today()
    cl.sent_via, cl.track = body.sent_via or "post", body.track.strip()
    cl.answer_due = cl.sent_on + timedelta(days=cl.answer_days)
    cl.status = "sent"
    svc.mark_penalties(c, cl, "claimed", user.id)
    svc.hist(cl, user.id, "sent", f"{svc.SENT_VIA.get(cl.sent_via, '')} {cl.sent_on:%d.%m.%Y}" + (f", трек {cl.track}" if cl.track else "") + f"; ответ до {cl.answer_due:%d.%m.%Y}")
    cs.hist(c, user.id, "claim", f"претензия {cl.number} отправлена, ответ до {cl.answer_due:%d.%m.%Y}")
    db.commit()
    return _out(db, user, cl, c)


@router.post("/claims/{clid}/answer")
def answer(clid: str, body: StepIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Ответ: на исходящую — результат контрагента; на входящую — наше решение. Удовлетворена → неустойки «признаны» (частично — пропорционально)."""
    cl, c = _claim(db, clid, user)
    if cl.status not in ("sent", "received", "signed"):
        raise HTTPException(409, "Ответ фиксируется по отправленной или полученной претензии")
    if not _can_work(user, c) or (cl.direction == "incoming" and not user.has(*WORK_ROLES)):
        raise HTTPException(403, "Ответ на входящую претензию фиксируют юрист, ФД, КД или ЗГД")
    if body.result not in svc.RESULTS:
        raise HTTPException(422, "Результат: " + ", ".join(svc.RESULTS))
    t = svc.totals(cl)
    if body.result == "partial" and (body.amount is None or body.amount <= 0):
        raise HTTPException(422, "Укажите признанную сумму")
    cl.result, cl.result_amount = body.result, (body.amount if body.result == "partial" else t["total"] if body.result == "satisfied" else 0.0)
    cl.answer_on, cl.answer_text, cl.status = body.answer_on or date.today(), body.comment.strip(), "answered"
    if body.result in ("satisfied", "partial"):
        share = 1.0 if body.result == "satisfied" or not t["total"] else min(1.0, float(body.amount or 0) / t["total"])
        svc.mark_penalties(c, cl, "recognized", user.id, share)
    svc.hist(cl, user.id, "answer", svc.RESULTS[body.result] + (f" — {svc._money(cl.result_amount)} {cl.currency}" if body.result == "partial" else "") + (f": {cl.answer_text}" if cl.answer_text else ""))
    cs.hist(c, user.id, "claim", f"претензия {cl.number}: {svc.RESULTS[body.result]}")
    db.commit()
    return _out(db, user, cl, c)


@router.post("/claims/{clid}/status")
def set_status(clid: str, body: StepIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """В суд, закрыть, отменить; вернуть в черновик с подписи."""
    cl, c = _claim(db, clid, user)
    if not _can_work(user, c):
        raise HTTPException(403)
    to = body.status
    allowed = {"draft": {"cancelled"}, "signing": {"draft", "cancelled"}, "signed": {"cancelled"}, "sent": {"court", "closed"}, "received": {"court", "closed", "cancelled"},
               "answered": {"court", "closed"}, "court": {"closed"}}
    if to not in allowed.get(cl.status, set()):
        raise HTTPException(409, f"Из «{svc.statuses(cl).get(cl.status)}» нельзя перейти в «{svc.statuses(cl).get(to, to)}»")
    if to in ("court", "cancelled") and len(body.comment.strip()) < 3:
        raise HTTPException(422, "Укажите причину")
    if to == "court" and not user.has(Role.LAWYER, Role.GD, Role.CD):
        raise HTTPException(403, "Решение о суде принимают юрист, КД или ЗГД")
    before = cl.status
    cl.status = to
    svc.hist(cl, user.id, "status", f"{svc.statuses(cl).get(before)} → {svc.statuses(cl).get(to)}" + (f": {body.comment.strip()}" if body.comment.strip() else ""))
    db.commit()
    return _out(db, user, cl, c)


def _render(db: Session, cl: ContractClaim, out_dir: str, draft: bool) -> str:
    from ..services.docx_fill import fill_docx
    from ..services.legal_packages import active_template
    path = os.path.join(out_dir, f"Претензия {cl.number}.docx".replace("/", "-"))
    t = active_template(db, "claim", "ru")
    if t is not None and not t.is_example and os.path.isfile(t.file_path):
        data = svc.template_data(db, cl)
        data.pop("_demands", None)
        res = fill_docx(t.file_path, path, data)
        if res.unresolved:
            raise HTTPException(422, {"message": "В шаблоне претензии остались незаполненные переменные", "errors": res.unresolved})
    else:
        svc.build_docx(db, cl, path, draft)
    return path


@router.get("/claims/{clid}/print")
def print_claim(clid: str, fmt: str = "docx", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Печатная форма: шаблон «Претензия по договору» из библиотеки, если загружен, иначе встроенная форма в фирменном стиле."""
    cl, c = _claim(db, clid, user)
    d = tempfile.mkdtemp()
    path = _render(db, cl, d, draft=cl.status in ("draft", "signing"))
    if fmt == "pdf":
        from ..services.legal_poa import to_pdf
        pdf = to_pdf(path, d)
        if not pdf:
            shutil.rmtree(d, ignore_errors=True)
            raise HTTPException(501, "PDF недоступен: на сервере нет LibreOffice — скачайте DOCX")
        return file_response(pdf, os.path.basename(pdf), inline=True, background=BackgroundTask(shutil.rmtree, d, True))
    return file_response(path, os.path.basename(path), inline=False, background=BackgroundTask(shutil.rmtree, d, True))


@router.post("/claims/{clid}/files", status_code=201)
async def upload(clid: str, file: UploadFile = File(...), kind: str = Form("signed_scan"), db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    cl, c = _claim(db, clid, user)
    if not _can_work(user, c):
        raise HTTPException(403)
    if kind not in FILE_KINDS:
        raise HTTPException(422, "Вид файла: " + ", ".join(FILE_KINDS))
    name = os.path.basename(file.filename or "file")
    if os.path.splitext(name)[1].lower() not in (".pdf", ".jpg", ".jpeg", ".png", ".docx", ".doc", ".zip", ".sig", ".xml", ".p7s"):
        raise HTTPException(422, "Файл: PDF, JPG, PNG, DOCX, ZIP или файлы ЭДО")
    d = tempfile.mkdtemp()
    try:
        tmp = os.path.join(d, name)
        with open(tmp, "wb") as f:
            f.write(await file.read())
        _add_file(cl, kind, tmp, name, user.id)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    svc.hist(cl, user.id, "file", f"{FILE_KINDS[kind]}: {name}")
    db.commit()
    return _out(db, user, cl, c)


@router.get("/claims/{clid}/files/{fid}")
def download(clid: str, fid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    cl, _ = _claim(db, clid, user)
    f = next((x for x in cl.files or [] if x["id"] == fid), None)
    if f is None:
        raise HTTPException(404)
    d = _dir(cl.id)
    path = next((os.path.join(d, x) for x in os.listdir(d) if x.startswith(fid)), None)
    if not path:
        raise HTTPException(404, "Файл не найден на сервере")
    return file_response(path, f["name"], inline=True)
