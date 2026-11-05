"""Запчасти, шаг 5: запросы цен поставщикам по строкам заявок, ответы, сравнение и выбор поставщика (ТЗ запчастей, раздел 7)."""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .. import adapters
from ..core.auth import CurrentUser, get_current_user
from ..core.roles import Role
from ..db import get_db
from ..models import Factory, IntegrationLog, Part, PartRequest, PartRequestLine, SupplierRfq, SupplierRfqLine, SupplyRoute
from ..services.deal_service import audit
from ..services.moysklad_import import read_rows
from ..services.parts_supplier import AVAILABILITY, CARD_KEY, LANG_TITLE, build_rfq_xlsx, parse_rfq_answer, rfq_status, supplier_language
from .common import serialize
from .parts_requests import _editable, _payload, _recalc, _req_or_404

router = APIRouter(prefix="/api/parts", tags=["parts-rfq"])
EDIT_ROLES = (Role.MANAGER, Role.CD, Role.GD, Role.PURCHASING)
STATUS = {"draft": "не отправлен", "sent": "отправлен, ждём ответ", "partial": "ответ частичный", "answered": "ответ получен", "closed": "закрыт"}


def _editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(*EDIT_ROLES):
        raise HTTPException(403, "Запросы поставщикам ведут менеджеры, закупки, КД и ЗГД")
    return user


def _next_number(db: Session) -> str:
    prefix = f"ЗП-{date.today().year}-"
    last = db.scalars(select(SupplierRfq.number).where(SupplierRfq.number.like(f"{prefix}%"))).all()
    n = max([int(x.rsplit("-", 1)[1]) for x in last if x.rsplit("-", 1)[1].isdigit()] or [0]) + 1
    return f"{prefix}{n:04d}"


def _supplier_number(part: Optional[Part], supplier_id: str, fallback: str) -> str:
    if part is None:
        return fallback
    own = next((c.number for c in part.cross_refs if c.kind == "supplier" and c.supplier_id == supplier_id), None)
    return own or part.oem_number or part.article


def _translate(db: Session, names: list[str], lang: str) -> list[Optional[str]]:
    if lang == "ru" or not names:
        return [None] * len(names)
    try:
        tr = adapters.translator()
        res = tr.translate(names, "ru", lang)
        db.add(IntegrationLog(system="translator", operation="rfq_names", ok=True, details={"provider": tr.name, "lang": lang, "count": len(names)}))
        return res
    except Exception as e:   # переводчик недоступен — строки остаются без перевода
        db.add(IntegrationLog(system="translator", operation="rfq_names", ok=False, details={"error": str(e)[:300]}))
        return [None] * len(names)


def _rfq_payload(db: Session, rfq: SupplierRfq) -> dict:
    d = serialize(rfq)
    sup = db.get(Factory, rfq.supplier_id)
    d["supplier_name"] = (sup.short_name or sup.name) if sup else ""
    d["status_title"] = STATUS.get(rfq.status, rfq.status)
    d["language_title"] = LANG_TITLE.get(rfq.language, rfq.language)
    reqs = {}
    lines = []
    for l in rfq.lines:
        x = serialize(l)
        rl = db.get(PartRequestLine, l.request_line_id) if l.request_line_id else None
        if rl:
            req = reqs.get(rl.request_id) or db.get(PartRequest, rl.request_id)
            reqs[rl.request_id] = req
            x["request_number"], x["request_id"], x["chosen"] = req.number, req.id, rl.rfq_line_id == l.id
        lines.append(x)
    d["lines"] = lines
    d["answered"] = sum(1 for l in rfq.lines if l.availability)
    return d


# ------------------------------------------------------------------ создание запросов из заявки

class RfqCreateIn(BaseModel):
    line_ids: list[str]
    supplier_ids: list[str]
    due_days: int = 3
    comment: str = ""


@router.post("/requests/{rid}/rfq", status_code=201)
def create_rfqs(rid: str, body: RfqCreateIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Один запрос на каждого поставщика по отмеченным строкам заявки; строки переходят в статус «запрос поставщику»."""
    req = _req_or_404(db, rid, user)
    _editable(user, req)
    lines = [l for l in req.lines if l.id in set(body.line_ids) and l.status not in ("cancelled",)]
    if not lines:
        raise HTTPException(422, "Отметьте строки для запроса")
    if not body.supplier_ids:
        raise HTTPException(422, "Выберите поставщиков")
    created = []
    for sid in dict.fromkeys(body.supplier_ids):
        sup = db.get(Factory, sid)
        if sup is None:
            raise HTTPException(404, "Поставщик не найден")
        lang = supplier_language(sup.country, sup.language)
        rfq = SupplierRfq(number=_next_number(db), supplier_id=sid, language=lang, due_date=date.today() + timedelta(days=max(body.due_days, 0)),
                          comment=body.comment, created_by=user.id)
        db.add(rfq)
        db.flush()
        parts = [db.get(Part, l.part_id) if l.part_id else None for l in lines]
        names = [p.name_ru if p else l.raw_name for p, l in zip(parts, lines)]
        known = [((p.names or {}).get(CARD_KEY.get(lang, "")) if p else None) for p in parts]
        need = [i for i, k in enumerate(known) if not k and lang != "ru" and names[i]]
        tr = _translate(db, [names[i] for i in need], lang)
        machine = {i: t for i, t in zip(need, tr) if t}
        for i, (l, p) in enumerate(zip(lines, parts), start=0):
            rfq.lines.append(SupplierRfqLine(no=i + 1, request_line_id=l.id, part_id=l.part_id, number=_supplier_number(p, sid, l.raw_article),
                                             name_ru=names[i], name_foreign=known[i] or machine.get(i) or "", machine_translated=i in machine,
                                             qty=l.qty, unit=l.unit))
        created.append(rfq)
    for l in lines:
        if l.status in ("new", "quoted"):
            l.status = "rfq"
    db.flush()
    audit(db, user.id, None, "supplier_rfq", req.id, "create", None, {"request": req.number, "rfqs": [r.number for r in created], "lines": len(lines)})
    db.commit()
    return {"rfqs": [_rfq_payload(db, r) for r in created], "request": _payload(db, req, user)}


# ------------------------------------------------------------------ список, карточка, файл, отправка

@router.get("/rfq")
def rfqs(open_only: bool = True, request_id: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    stmt = select(SupplierRfq).order_by(SupplierRfq.created_at.desc())
    if open_only:
        stmt = stmt.where(SupplierRfq.status != "closed")
    out = []
    for r in db.scalars(stmt).all():
        if request_id and not any(l.request_line_id and db.get(PartRequestLine, l.request_line_id) and
                                  db.get(PartRequestLine, l.request_line_id).request_id == request_id for l in r.lines):
            continue
        d = _rfq_payload(db, r)
        d.pop("lines")
        d["lines_count"] = len(r.lines)
        out.append(d)
    return out


def _rfq_or_404(db: Session, rid: str) -> SupplierRfq:
    r = db.get(SupplierRfq, rid)
    if r is None:
        raise HTTPException(404, "Запрос не найден")
    return r


@router.get("/rfq/{rfq_id}")
def rfq_card(rfq_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    return _rfq_payload(db, _rfq_or_404(db, rfq_id))


def _file(db: Session, rfq: SupplierRfq, only_unanswered: bool = False) -> str:
    sup = db.get(Factory, rfq.supplier_id)
    lines = [serialize(l) for l in rfq.lines if not only_unanswered or not l.availability]
    d = tempfile.mkdtemp()
    return build_rfq_xlsx(os.path.join(d, f"{rfq.number}.xlsx"), rfq.number, sup.name if sup else "", rfq.language, lines,
                          rfq.due_date.strftime("%d.%m.%Y") if rfq.due_date else "")


@router.get("/rfq/{rfq_id}/xlsx")
def rfq_xlsx(rfq_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    rfq = _rfq_or_404(db, rfq_id)
    path = _file(db, rfq)
    return FileResponse(path, filename=os.path.basename(path), background=BackgroundTask(os.unlink, path))


class SendIn(BaseModel):
    via_mail: bool = True
    via_wecom: bool = True
    only_unanswered: bool = False      # напоминание — только по строкам без ответа


def send_supplier_file(sup: Factory, subject: str, path: str, wecom_text: str, ref: str, via_mail: bool = True, via_wecom: bool = True,
                       roles: tuple[str, ...] = ("rfq",)) -> list[dict]:
    """Файл поставщику: почта с вложением и сообщение WeCom контактам нужного направления (иначе — основному контакту)."""
    from ..adapters.mail import OutgoingMail
    from ..core.config import get_settings
    contacts = [c for c in (sup.contacts or []) if set(roles) & set(c.get("roles") or [])] or [c for c in (sup.contacts or []) if c.get("primary")]
    s = get_settings()
    results: list[dict] = []
    if via_mail:
        emails = [c["email"] for c in contacts if c.get("email")]
        if not emails:
            results.append({"via": "mail", "ok": False, "error": "не отправлено: нет e-mail в карточке поставщика"})
        else:
            try:
                adapters.mail().send(OutgoingMail(to=emails, subject=subject, body_html=f"<p>{subject}. Файл во вложении.</p>", attachments=[path]))
                ok = s.mail_provider != "console"
                results.append({"via": "mail", "to": emails, "ok": ok, "error": "" if ok else "почта не подключена — файл не ушёл"})
            except Exception as e:
                results.append({"via": "mail", "to": emails, "ok": False, "error": str(e)[:200]})
    if via_wecom:
        ids = [c["wecom_id"] for c in contacts if c.get("wecom_id")] or list(sup.wecom_external_ids or [])
        if not ids:
            results.append({"via": "wecom", "ok": False, "error": "не отправлено: нет WeCom-контакта в карточке поставщика"})
        else:
            try:
                for i in ids:
                    adapters.messenger().send_to_external(i, wecom_text, ref)
                ok = s.messenger_provider != "console"
                results.append({"via": "wecom", "to": ids, "ok": ok, "error": "" if ok else "WeCom не подключён"})
            except Exception as e:
                results.append({"via": "wecom", "to": ids, "ok": False, "error": str(e)[:200]})
    return results


@router.post("/rfq/{rfq_id}/send")
def rfq_send(rfq_id: str, body: SendIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Отправка поставщику контактам с направлением «Заявки и КП» (или основному): почта с файлом и сообщение WeCom."""
    rfq = _rfq_or_404(db, rfq_id)
    sup = db.get(Factory, rfq.supplier_id)
    if body.only_unanswered and all(l.availability for l in rfq.lines):
        raise HTTPException(409, "Поставщик ответил по всем строкам — напоминать не о чем")
    path = _file(db, rfq, body.only_unanswered)
    now = datetime.now(timezone.utc)
    subj = f"{'Напоминание: ' if body.only_unanswered else ''}Запрос цены {rfq.number}"
    try:
        results = send_supplier_file(sup, subj, path, f"{subj}: строк {len(rfq.lines)}. Файл отправлен на почту.", rfq.number, body.via_mail, body.via_wecom)
    finally:
        os.unlink(path)
    rfq.send_log = [*(rfq.send_log or []), *({**r, "at": now.isoformat()} for r in results)]
    if any(r["ok"] for r in results) or not results:
        rfq.sent_at = rfq.sent_at or now
        if rfq.status == "draft":
            rfq.status = "sent"
    audit(db, user.id, None, "supplier_rfq", rfq.id, "send", None, {"results": results, "reminder": body.only_unanswered})
    db.commit()
    return {**_rfq_payload(db, rfq), "send_results": results}


@router.post("/rfq/{rfq_id}/mark-sent")
def rfq_mark_sent(rfq_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Файл отправлен вручную (например, из личного WeChat)."""
    rfq = _rfq_or_404(db, rfq_id)
    rfq.sent_at = rfq.sent_at or datetime.now(timezone.utc)
    if rfq.status == "draft":
        rfq.status = "sent"
    rfq.send_log = [*(rfq.send_log or []), {"at": datetime.now(timezone.utc).isoformat(), "via": "manual", "ok": True}]
    db.commit()
    return _rfq_payload(db, rfq)


# ------------------------------------------------------------------ ответы

class AnswerIn(BaseModel):
    id: str
    availability: str = ""
    price: Optional[float] = None
    currency: str = ""
    lead_days: Optional[int] = None
    moq: Optional[float] = None
    analog_number: str = ""
    comment: str = ""


class AnswersIn(BaseModel):
    lines: list[AnswerIn] = Field(default_factory=list)


def _apply_answers(rfq: SupplierRfq, answers: dict[str, dict]) -> int:
    now = datetime.now(timezone.utc)
    n = 0
    for l in rfq.lines:
        a = answers.get(l.id)
        if not a:
            continue
        if a.get("availability") and a["availability"] not in AVAILABILITY:
            raise HTTPException(422, f"Строка {l.no}: наличие — в наличии, под заказ, нет или аналог")
        if a.get("availability") in ("available", "on_order", "analog") and not a.get("price"):
            raise HTTPException(422, f"Строка {l.no}: укажите цену")
        if a.get("price") and not (a.get("currency") or l.currency):
            raise HTTPException(422, f"Строка {l.no}: укажите валюту")
        for k in ("availability", "price", "currency", "lead_days", "moq", "analog_number", "comment"):
            if k in a:
                setattr(l, k, a[k] if k != "currency" else (a[k] or l.currency))
        l.answered_at = now if l.availability else None
        n += 1
    st = rfq_status([{"availability": l.availability} for l in rfq.lines])
    if st:
        rfq.status = st
    return n


@router.put("/rfq/{rfq_id}/answers")
def rfq_answers(rfq_id: str, body: AnswersIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    rfq = _rfq_or_404(db, rfq_id)
    n = _apply_answers(rfq, {a.id: a.model_dump() for a in body.lines})
    audit(db, user.id, None, "supplier_rfq", rfq.id, "answers", None, {"lines": n})
    db.commit()
    return _rfq_payload(db, rfq)


@router.post("/rfq/{rfq_id}/import")
def rfq_import(rfq_id: str, file: UploadFile = File(...), db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Заполненный поставщиком файл запроса: ответы сопоставляются по номеру строки."""
    rfq = _rfq_or_404(db, rfq_id)
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    with os.fdopen(fd, "wb") as f:
        f.write(file.file.read())
    try:
        parsed = parse_rfq_answer(read_rows(path))
    except ValueError as e:
        raise HTTPException(422, str(e))
    finally:
        os.unlink(path)
    by_no = {l.no: l.id for l in rfq.lines}
    n = _apply_answers(rfq, {by_no[no]: a for no, a in parsed.items() if no in by_no})
    audit(db, user.id, None, "supplier_rfq", rfq.id, "import", None, {"file": file.filename, "lines": n})
    db.commit()
    return {**_rfq_payload(db, rfq), "imported": n}


@router.post("/rfq/{rfq_id}/close")
def rfq_close(rfq_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    rfq = _rfq_or_404(db, rfq_id)
    rfq.status = "closed"
    db.commit()
    return _rfq_payload(db, rfq)


# ------------------------------------------------------------------ сравнение и выбор по строке заявки

@router.get("/requests/{rid}/quotes")
def quotes(rid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Ответы всех поставщиков по каждой строке заявки — для сравнения и выбора."""
    req = _req_or_404(db, rid, user)
    ids = [l.id for l in req.lines]
    rows = db.scalars(select(SupplierRfqLine).where(SupplierRfqLine.request_line_id.in_(ids))).all() if ids else []
    out: dict[str, list] = {i: [] for i in ids}
    for x in rows:
        rfq = db.get(SupplierRfq, x.rfq_id)
        sup = db.get(Factory, rfq.supplier_id)
        out[x.request_line_id].append({**serialize(x), "rfq_number": rfq.number, "rfq_status": rfq.status, "supplier_id": rfq.supplier_id,
                                       "supplier_name": (sup.short_name or sup.name) if sup else ""})
    return out


class ChooseIn(BaseModel):
    rfq_line_id: str
    route_id: Optional[str] = None


def _route_transit(db: Session, route_id: Optional[str]) -> int:
    r = db.get(SupplyRoute, route_id) if route_id else None
    return sum(s.transit_days or 0 for s in r.steps) if r else 0


@router.post("/requests/{rid}/lines/{line_id}/choose")
def choose(rid: str, line_id: str, body: ChooseIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Выбрать ответ поставщика для строки: поставщик, маршрут, цена поставщика (для себестоимости) и срок клиенту."""
    req = _req_or_404(db, rid, user)
    _editable(user, req)
    line = next((l for l in req.lines if l.id == line_id), None)
    if line is None:
        raise HTTPException(404, "Строка не найдена")
    q = db.get(SupplierRfqLine, body.rfq_line_id)
    if q is None or q.request_line_id != line.id:
        raise HTTPException(422, "Ответ относится к другой строке")
    if q.availability not in ("available", "on_order", "analog") or not q.price:
        raise HTTPException(422, "У этого ответа нет цены — выбрать нельзя")
    rfq = db.get(SupplierRfq, q.rfq_id)
    sup = db.get(Factory, rfq.supplier_id)
    part = db.get(Part, line.part_id) if line.part_id else None
    line.supplier_id = rfq.supplier_id
    line.route_id = body.route_id or (sup.default_route_id if sup else None) or (part.route_id if part else None)
    line.quote_price, line.quote_currency, line.rfq_line_id = q.price, q.currency, q.id
    line.lead_days = (q.lead_days or 0) + _route_transit(db, line.route_id) or None
    line.source = "order"
    if q.availability == "analog" and q.analog_number:
        line.comment = (line.comment + "; " if line.comment else "") + f"аналог поставщика: {q.analog_number}"
    if line.status in ("new", "rfq", "quoted"):
        line.status = "replaced" if q.availability == "analog" else "quoted"
    db.flush()
    _recalc(db, req)
    audit(db, user.id, None, "part_request_line", line.id, "choose_quote", None, {"rfq": rfq.number, "price": q.price, "currency": q.currency})
    db.commit()
    return _payload(db, req, user)
