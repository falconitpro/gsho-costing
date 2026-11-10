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
from ..core.roles import EDIT, READ, Role, can
from ..db import get_db
from ..models import Factory, IntegrationLog, Part, PartRequest, PartRequestLine, SupplierRfq, SupplierRfqLine, SupplyRoute
from ..services import mirror_parts as mirror
from ..services.deal_service import audit
from ..services.moysklad_import import read_rows
from ..services.suppliers import mark as mark_supplier
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


def _viewer(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Запросы ведущих их ролей и — для зеркальных запросов — роли с правом «Цепочка компаний группы» (ФД, логист)."""
    if not (user.has(*EDIT_ROLES) or can(user, "intragroup_chain", READ)):
        raise HTTPException(403, "Запросы поставщикам ведут менеджеры, закупки, КД и ЗГД")
    return user


def _check_access(user: CurrentUser, rfq: SupplierRfq, edit: bool = False) -> None:
    """Запрос компании группы поставщику (L) с ценой поставщика — только при праве «Цепочка компаний группы»;
    обычные запросы — ролям, которые их ведут."""
    if mirror.is_lower(rfq):
        if not can(user, "intragroup_chain", EDIT if edit else READ):
            raise HTTPException(403, "Запрос компании группы поставщику и его цены — при праве «Цепочка компаний группы»")
    elif not user.has(*EDIT_ROLES):
        raise HTTPException(403, "Запросы поставщикам ведут менеджеры, закупки, КД и ЗГД")


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


def _rfq_payload(db: Session, rfq: SupplierRfq, user: Optional[CurrentUser] = None) -> dict:
    d = serialize(rfq)
    sup = db.get(Factory, rfq.supplier_id)
    d["supplier_name"] = (sup.short_name or sup.name) if sup else ""
    can_chain = user is None or can(user, "intragroup_chain", READ)
    d["mirror"] = mirror.view(db, rfq, can_chain)
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
    return d if can_chain else mirror.strip_chain(d)


# ------------------------------------------------------------------ создание запросов из заявки

class RfqCreateIn(BaseModel):
    line_ids: list[str]
    supplier_ids: list[str]
    due_days: int = 3
    comment: str = ""
    via_group: dict[str, list[str]] = Field(default_factory=dict)   # компания группы → конечные поставщики (D-156)


def _make_rfq(db: Session, sup: Factory, lines: list[PartRequestLine], user: CurrentUser, due_days: int, comment: str, **extra) -> SupplierRfq:
    lang = supplier_language(sup.country, sup.language)
    rfq = SupplierRfq(number=_next_number(db), supplier_id=sup.id, language=lang, due_date=date.today() + timedelta(days=max(due_days, 0)),
                      comment=comment, created_by=user.id, **extra)
    db.add(rfq)
    db.flush()
    parts = [db.get(Part, l.part_id) if l.part_id else None for l in lines]
    names = [p.name_ru if p else l.raw_name for p, l in zip(parts, lines)]
    known = [((p.names or {}).get(CARD_KEY.get(lang, "")) if p else None) for p in parts]
    need = [i for i, k in enumerate(known) if not k and lang != "ru" and names[i]]
    tr = _translate(db, [names[i] for i in need], lang)
    machine = {i: t for i, t in zip(need, tr) if t}
    for i, (l, p) in enumerate(zip(lines, parts), start=0):
        rfq.lines.append(SupplierRfqLine(no=i + 1, request_line_id=l.id, part_id=l.part_id, number=_supplier_number(p, sup.id, l.raw_article),
                                         name_ru=names[i], name_foreign=known[i] or machine.get(i) or "", machine_translated=i in machine,
                                         qty=l.qty, unit=l.unit))
    db.flush()
    return rfq


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
    from ..services import chain_rules
    from ..services.suppliers import require_approved
    plan: list[tuple[Factory, Optional[list]]] = []          # (поставщик, None) или (компания группы, [(X, маршрут)])
    rules_used: dict[str, str] = {}
    for sid in dict.fromkeys(body.supplier_ids):
        sup = db.get(Factory, sid)
        if sup is None:
            raise HTTPException(404, "Поставщик не найден")
        if sup.supplier_status == "group_company":   # вручную — только ФД/ЗГД; менеджер выбирает конечного поставщика (D-164)
            if not user.has(Role.FD, Role.GD):
                raise HTTPException(422, f"Выберите конечного поставщика — закупку через «{sup.short_name or sup.name}» подставят правила цепочек (их ведут ФД и ЗГД)")
            plan.append((sup, mirror.resolve(db, sup, body.via_group.get(sid) or [])[1]))
            continue
        require_approved(sup, "запросить у него цену")
        route, gf, rule = chain_rules.resolve(db, sup, "parts")
        rules_used[sid] = chain_rules.describe(db, rule)
        if route is not None:
            plan.append((gf, [(sup, route)]))
        else:
            plan.append((sup, None))
    created = []
    for sup, pairs in plan:
        mark_supplier(db, sup.id, "parts")
        if pairs is None:
            created.append(_make_rfq(db, sup, lines, user, body.due_days, body.comment))
            continue
        for end, route in pairs:                     # U — предложение компании группы, L — её запрос поставщику X
            mark_supplier(db, end.id, "parts")
            up = SupplierRfq(number=_next_number(db), supplier_id=sup.id, language="ru", end_supplier_id=end.id, route_id=route.id,
                             due_date=date.today() + timedelta(days=max(body.due_days, 0)), comment=body.comment, created_by=user.id,
                             mirror_state="waiting")
            db.add(up)
            db.flush()
            low = _make_rfq(db, end, lines, user, body.due_days, body.comment, mirror_of_id=up.id)
            for ll in low.lines:
                up.lines.append(SupplierRfqLine(no=ll.no, request_line_id=ll.request_line_id, part_id=ll.part_id, number=ll.number,
                                                name_ru=ll.name_ru, qty=ll.qty, unit=ll.unit))
            db.flush()
            created.extend([up, low])
    for l in lines:
        if l.status in ("new", "quoted"):
            l.status = "rfq"
    db.flush()
    audit(db, user.id, None, "supplier_rfq", req.id, "create", None, {"request": req.number, "rfqs": [r.number for r in created], "lines": len(lines)})
    db.commit()
    return {"rfqs": [_rfq_payload(db, r, user) for r in created if not mirror.is_lower(r) or can(user, "intragroup_chain", READ)],
            "request": _payload(db, req, user), "rules": rules_used}


# ------------------------------------------------------------------ список, карточка, файл, отправка

@router.get("/rfq")
def rfqs(open_only: bool = True, request_id: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(_viewer)):
    stmt = select(SupplierRfq).order_by(SupplierRfq.created_at.desc())
    if open_only:
        stmt = stmt.where(SupplierRfq.status != "closed")
    out = []
    chain = can(user, "intragroup_chain", READ)
    for r in db.scalars(stmt).all():
        if (mirror.is_lower(r) and not chain) or (not mirror.is_lower(r) and not user.has(*EDIT_ROLES)):
            continue
        if request_id and not any(l.request_line_id and db.get(PartRequestLine, l.request_line_id) and
                                  db.get(PartRequestLine, l.request_line_id).request_id == request_id for l in r.lines):
            continue
        d = _rfq_payload(db, r, user)
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
def rfq_card(rfq_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_viewer)):
    rfq = _rfq_or_404(db, rfq_id)
    _check_access(user, rfq)
    return _rfq_payload(db, rfq, user)


def _file(db: Session, rfq: SupplierRfq, only_unanswered: bool = False) -> str:
    sup = db.get(Factory, rfq.supplier_id)
    lines = [serialize(l) for l in rfq.lines if not only_unanswered or not l.availability]
    d = tempfile.mkdtemp()
    return build_rfq_xlsx(os.path.join(d, f"{rfq.number}.xlsx"), rfq.number, sup.name if sup else "", rfq.language, lines,
                          rfq.due_date.strftime("%d.%m.%Y") if rfq.due_date else "")


@router.get("/rfq/{rfq_id}/xlsx")
def rfq_xlsx(rfq_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_viewer)):
    """Файл для поставщика; у предложения компании группы — файл её запроса поставщику (без цен)."""
    rfq = _rfq_or_404(db, rfq_id)
    _check_access(user, rfq)
    rfq = (mirror.lower_of(db, rfq) or rfq) if mirror.is_upper(rfq) else rfq
    path = _file(db, rfq)
    return FileResponse(path, filename=os.path.basename(path), background=BackgroundTask(os.unlink, path))


class SendIn(BaseModel):
    via_mail: bool = True
    via_wecom: bool = True
    only_unanswered: bool = False      # напоминание — только по строкам без ответа


def send_supplier_file(sup: Factory, subject: str, path: str, wecom_text: str, ref: str, via_mail: bool = True, via_wecom: bool = True,
                       roles: tuple[str, ...] = ("rfq",), entity=None) -> list[dict]:
    """Файл поставщику: почта с вложением и сообщение WeCom контактам нужного направления (иначе — основному контакту).
    entity — компания группы, от имени которой идёт запрос: её почта и WeCom (LegalEntity.channels), каналы Берг-Хауса не подставляются."""
    from ..adapters.mail import OutgoingMail
    from ..core.config import get_settings
    ch = (entity.channels or {}) if entity is not None else None
    mailer = adapters.mail() if ch is None else adapters.mail_for(ch.get("mail_profile", ""))
    messenger = adapters.messenger() if ch is None else adapters.messenger_for(ch.get("wecom_profile", ""))
    who = f" «{entity.name}»" if entity is not None else ""
    contacts = [c for c in (sup.contacts or []) if set(roles) & set(c.get("roles") or [])] or [c for c in (sup.contacts or []) if c.get("primary")]
    s = get_settings()
    results: list[dict] = []
    if via_mail:
        emails = [c["email"] for c in contacts if c.get("email")]
        if not emails:
            results.append({"via": "mail", "ok": False, "error": "не отправлено: нет e-mail в карточке поставщика"})
        elif mailer is None:
            results.append({"via": "mail", "to": emails, "ok": False, "error": f"не отправлено: почта{who} не настроена"})
        else:
            try:
                mailer.send(OutgoingMail(to=emails, subject=subject, body_html=f"<p>{subject}. Файл во вложении.</p>", attachments=[path],
                                         sender=(ch or {}).get("mail_from", "")))
                ok = s.mail_provider != "console"
                results.append({"via": "mail", "to": emails, "ok": ok, "error": "" if ok else "почта не подключена — файл не ушёл"})
            except Exception as e:
                results.append({"via": "mail", "to": emails, "ok": False, "error": str(e)[:200]})
    if via_wecom:
        ids = [c["wecom_id"] for c in contacts if c.get("wecom_id")] or list(sup.wecom_external_ids or [])
        if not ids:
            results.append({"via": "wecom", "ok": False, "error": "не отправлено: нет WeCom-контакта в карточке поставщика"})
        elif messenger is None:
            results.append({"via": "wecom", "to": ids, "ok": False, "error": f"не отправлено: WeCom{who} не настроен"})
        else:
            try:
                for i in ids:
                    messenger.send_to_external(i, wecom_text, ref)
                ok = s.messenger_provider != "console"
                results.append({"via": "wecom", "to": ids, "ok": ok, "error": "" if ok else "WeCom не подключён"})
            except Exception as e:
                results.append({"via": "wecom", "to": ids, "ok": False, "error": str(e)[:200]})
    return results


@router.post("/rfq/{rfq_id}/send")
def rfq_send(rfq_id: str, body: SendIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Отправка поставщику контактам с направлением «Заявки и КП» (или основному): почта с файлом и сообщение WeCom.
    Предложение компании группы не отправляется: уходит её запрос поставщику — с её почты и WeCom."""
    up = _rfq_or_404(db, rfq_id)
    if mirror.is_lower(up):
        raise HTTPException(409, "Запрос компании группы поставщику отправляется из её предложения")
    rfq = (mirror.lower_of(db, up) or up) if mirror.is_upper(up) else up
    sup = db.get(Factory, rfq.supplier_id)
    entity = None
    if mirror.is_lower(rfq):
        entity = mirror.group_entity(db, db.get(Factory, up.supplier_id))
    if body.only_unanswered and all(l.availability for l in rfq.lines):
        raise HTTPException(409, "Поставщик ответил по всем строкам — напоминать не о чем")
    path = _file(db, rfq, body.only_unanswered)
    now = datetime.now(timezone.utc)
    subj = f"{'Напоминание: ' if body.only_unanswered else ''}Запрос цены {rfq.number}"
    try:
        if entity is not None:
            subj = f"{entity.name} — {subj}"
        results = send_supplier_file(sup, subj, path, f"{subj}: строк {len(rfq.lines)}. Файл отправлен на почту.", rfq.number, body.via_mail, body.via_wecom,
                                     entity=entity)
    finally:
        os.unlink(path)
    sent = any(r["ok"] for r in results) or not results
    for x in dict.fromkeys([rfq, up]):
        x.send_log = [*(x.send_log or []), *({**r, "at": now.isoformat(), **({"from": entity.name} if entity else {})} for r in results)]
        if sent:
            x.sent_at = x.sent_at or now
            if x.status == "draft":
                x.status = "sent"
    audit(db, user.id, None, "supplier_rfq", rfq.id, "send", None, {"results": results, "reminder": body.only_unanswered,
                                                                     **({"from": entity.name} if entity else {})})
    db.commit()
    return {**_rfq_payload(db, up, user), "send_results": results}


@router.post("/rfq/{rfq_id}/mark-sent")
def rfq_mark_sent(rfq_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Файл отправлен вручную (например, из личного WeChat). У предложения компании группы — отмечается и её запрос поставщику."""
    rfq = _rfq_or_404(db, rfq_id)
    _check_access(user, rfq)
    pair = [rfq, *([mirror.lower_of(db, rfq)] if mirror.is_upper(rfq) else [])]
    for x in (p for p in pair if p is not None):
        x.sent_at = x.sent_at or datetime.now(timezone.utc)
        if x.status == "draft":
            x.status = "sent"
        x.send_log = [*(x.send_log or []), {"at": datetime.now(timezone.utc).isoformat(), "via": "manual", "ok": True}]
    db.commit()
    return _rfq_payload(db, rfq, user)


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


def _answer_target(db: Session, user: CurrentUser, rfq_id: str) -> SupplierRfq:
    rfq = _rfq_or_404(db, rfq_id)
    if mirror.is_upper(rfq):
        low = mirror.lower_of(db, rfq)
        raise HTTPException(409, f"Предложение компании группы считается из ответа поставщика — внесите ответ в запрос {low.number if low else ''}".strip())
    _check_access(user, rfq, edit=True)
    return rfq


def _after_answer(db: Session, rfq: SupplierRfq, user: CurrentUser) -> None:
    """Ответ поставщика в запросе компании группы пересчитывает её предложение; изменения выбранных строк — КД."""
    if not mirror.is_lower(rfq):
        return
    up = mirror.upper_of(db, rfq)
    if up is None:
        return
    db.flush()
    changed = mirror.refresh(db, up)
    if changed:
        from ..services.deal_service import notify
        from ..services.legal_contracts import role_users
        text = (f"{up.number}: поставщик изменил ответ после выбора — строки {', '.join(str(c['no']) for c in changed)}. "
                "Цена в заявке не изменилась; чтобы принять новую, выберите предложение повторно")
        for u in role_users(db, "cd"):
            notify(db, None, u.id, "mirror_changed", None, text)
        audit(db, user.id, None, "supplier_rfq", up.id, "mirror_changed_after_select", None, {"lines": changed})


@router.put("/rfq/{rfq_id}/answers")
def rfq_answers(rfq_id: str, body: AnswersIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_viewer)):
    rfq = _answer_target(db, user, rfq_id)
    n = _apply_answers(rfq, {a.id: a.model_dump() for a in body.lines})
    _after_answer(db, rfq, user)
    audit(db, user.id, None, "supplier_rfq", rfq.id, "answers", None, {"lines": n})
    db.commit()
    return _rfq_payload(db, rfq, user)


@router.post("/rfq/{rfq_id}/import")
def rfq_import(rfq_id: str, file: UploadFile = File(...), db: Session = Depends(get_db), user: CurrentUser = Depends(_viewer)):
    """Заполненный поставщиком файл запроса: ответы сопоставляются по номеру строки."""
    rfq = _answer_target(db, user, rfq_id)
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
    _after_answer(db, rfq, user)
    audit(db, user.id, None, "supplier_rfq", rfq.id, "import", None, {"file": file.filename, "lines": n})
    db.commit()
    return {**_rfq_payload(db, rfq, user), "imported": n}


@router.post("/rfq/{rfq_id}/close")
def rfq_close(rfq_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Закрыть запрос; у зеркальной пары закрываются оба."""
    rfq = _rfq_or_404(db, rfq_id)
    _check_access(user, rfq)
    other = mirror.lower_of(db, rfq) if mirror.is_upper(rfq) else mirror.upper_of(db, rfq) if mirror.is_lower(rfq) else None
    for x in (rfq, other):
        if x is not None:
            x.status = "closed"
    db.commit()
    return _rfq_payload(db, rfq, user)


# ------------------------------------------------------------------ сравнение и выбор по строке заявки

@router.get("/requests/{rid}/quotes")
def quotes(rid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_viewer)):
    """Ответы всех поставщиков по каждой строке заявки — для сравнения и выбора. Через компанию группы выбирается её
    предложение; ответ конечного поставщика (с его ценой) показывается рядом только при праве «Цепочка компаний группы»."""
    req = _req_or_404(db, rid, user)
    ids = [l.id for l in req.lines]
    rows = db.scalars(select(SupplierRfqLine).where(SupplierRfqLine.request_line_id.in_(ids))).all() if ids else []
    out: dict[str, list] = {i: [] for i in ids}
    chain = can(user, "intragroup_chain", READ)
    for x in rows:
        rfq = db.get(SupplierRfq, x.rfq_id)
        if mirror.is_lower(rfq) and not chain:
            continue
        sup = db.get(Factory, rfq.supplier_id)
        d = {**serialize(x), "rfq_number": rfq.number, "rfq_status": rfq.status, "supplier_id": rfq.supplier_id,
             "supplier_name": (sup.short_name or sup.name) if sup else "", "mirror": mirror.view(db, rfq, chain),
             "selectable": not mirror.is_lower(rfq)}
        if not chain:
            d.pop("chain", None)
        out[x.request_line_id].append(d)
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
    if mirror.is_lower(rfq):
        raise HTTPException(422, "Через компанию группы выбирается её предложение, а не ответ поставщика ей")
    part = db.get(Part, line.part_id) if line.part_id else None
    if mirror.is_upper(rfq):
        # поставщик — X, маршрут — через компанию группы, цена — цена X: себестоимость и заказы по маршруту = цена компании группы
        low = mirror.lower_of(db, rfq)
        lq = next((l for l in (low.lines if low else []) if l.no == q.no), None)
        if lq is None or not lq.price:
            raise HTTPException(422, "Нет ответа поставщика по этой строке")
        mirror.accept_pending(q)
        if q.price is None:
            raise HTTPException(422, "Предложение компании группы не рассчитано — проверьте наценки группы")
        line.supplier_id, line.route_id = low.supplier_id, rfq.route_id
        line.quote_price, line.quote_currency, line.rfq_line_id = lq.price, lq.currency, q.id
        if rfq.mirror_state == "changed_after_select" and not any((l.chain or {}).get("pending") for l in rfq.lines):
            rfq.mirror_state = "ok"
    else:
        sup = db.get(Factory, rfq.supplier_id)
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



# ------------------------------------------------------------------ внутренняя цепочка заявки: правка и виза ФД/ЗГД (D-165, D-166)

class ChainIn(BaseModel):
    route_id: Optional[str] = None


@router.get("/requests/{rid}/chain")
def request_chain(rid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_viewer)):
    req = _req_or_404(db, rid, user)
    out = mirror.chain_view(db, req)
    out["can_edit"] = user.has(Role.FD, Role.GD)
    if out["can_edit"]:
        out["routes"] = [{"id": r.id, "name": r.name, "country": r.supplier_country}
                         for r in db.scalars(select(SupplyRoute).where(SupplyRoute.is_active.is_(True)).order_by(SupplyRoute.name)).all() if len(r.steps) >= 2]
    return out


@router.post("/rfq/{rfq_id}/chain")
def rfq_chain(rfq_id: str, body: ChainIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """ФД/ЗГД меняет цепочку запроса до выбора строк: другой маршрут через компанию группы или напрямую."""
    from ..services import chain_rules
    chain_rules.require_fd_gd(user)
    rfq = _rfq_or_404(db, rfq_id)
    res = mirror.set_chain(db, rfq, body.route_id, user.id)
    audit(db, user.id, None, "supplier_rfq", rfq_id, "chain", after={"route": body.route_id, "result": res.id if res else None})
    db.commit()
    return _rfq_payload(db, res, user) if res else {"ok": True}


@router.post("/requests/{rid}/chain/approve")
def approve_request_chain(rid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Виза ФД/ЗГД на внутреннюю цепочку заявки — до КП клиенту."""
    from ..services import chain_rules
    chain_rules.require_fd_gd(user)
    req = _req_or_404(db, rid, user)
    chain = mirror.request_chain(db, req)
    if not chain:
        raise HTTPException(409, "В заявке нет строк через компанию группы — утверждать нечего")
    req.chain_approval = chain_rules.approval_record(user, chain)
    audit(db, user.id, None, "part_request", req.id, "chain_approved", after={"lines": len(chain)})
    db.commit()
    return mirror.chain_view(db, req)
