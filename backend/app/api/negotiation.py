"""Торги по сделке (D-074): только КД и ЗГД. Шаги снижения цены и изменения условий, данные для решения, КП на каждом шаге."""
from __future__ import annotations

import os
import re
import tempfile
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import adapters
from ..core.auth import CurrentUser, require_roles
from ..core.roles import Role
from ..core.workflow import DealStatus
from ..db import get_db
from ..models import Attachment, DealVersion, FeedEntry, KpRevision, NegotiationStep, Task, User
from ..services import negotiation as ng
from ..services.deal_service import audit, ensure_folders, notify
from .common import get_deal_or_404

router = APIRouter(prefix="/api/deals/{deal_id}/negotiation", tags=["negotiation"])
CD_GD = require_roles(Role.CD, Role.GD)
OPEN = (DealStatus.APPROVED, DealStatus.KP_SENT)


def _ver(db: Session, deal) -> Optional[DealVersion]:
    return db.get(DealVersion, deal.approved_version_id) if deal.approved_version_id else None


def _need_ver(db: Session, deal) -> DealVersion:
    ver = _ver(db, deal)
    if ver is None or deal.status not in OPEN:
        raise HTTPException(409, "Торги ведутся после утверждения расчёта и до выигрыша / проигрыша сделки")
    return ver


@router.get("")
def overview(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(CD_GD)):
    """Всё для торгов: состояние по предложениям, данные для решения, шаги, конкуренты, аналитика."""
    deal = get_deal_or_404(db, deal_id, user)
    ver = _ver(db, deal)
    if ver is None:
        return {"ready": False, "message": "Торги открываются после утверждения расчёта (первая цена КП — цена утверждённой версии)."}
    st = ng.current(db, deal, ver)
    rows = ng.steps(db, deal, ver)
    comps = ng.competitors(db, deal)
    names = ng.names(db)
    return {"ready": True, "can_edit": deal.status in OPEN, "status": deal.status.value, "vat_pct": ng._vat(db), "reasons": ng.REASONS,
            "variants": [{"id": vid, "name": s["name"], "units": s["units"], "currency": s["currency"], "kp_rate": s["kp_rate"], "first_rub": s["first_rub"],
                          "price_rub": s["price_rub"], "client_payments": s["client_payments"], "delivery_term": s["delivery_term"]} for vid, s in st.items()],
            "decision": ng.evaluate(db, deal, ver, {}, {}), "steps": [ng.step_dict(r, names) for r in rows], "competitors": comps,
            "analytics": ng.analytics(deal, ver, st, rows, comps), "fx": _rates(db, deal),
            "client_rates": ng.client_rates(deal),
            "client_rates_history": [{**h, "by_name": names.get(h.get("by") or "", "")} for h in (deal.negotiation_info or {}).get("client_rates_history") or []]}


def _rates(db: Session, deal) -> dict:
    from ..services.deal_calc import build_calc_input
    return {k: v for k, v in build_calc_input(db, deal).fx.rates.items()}


class StepIn(BaseModel):
    prices: dict[str, dict] = Field(default_factory=dict)    # {variant_id: {mode: pct|amount|target, basis: net|gross, currency, value}}
    terms: dict[str, dict] = Field(default_factory=dict)     # {variant_id: {client_payments: [...], delivery_term}}
    reason: str = "client"
    comment: str = ""
    show_discount: bool = False                              # в КП — строкой «Скидка» от первой цены, иначе только новая цена
    confirm_below_threshold: bool = False


def _compute(db: Session, deal, ver, body: StepIn) -> tuple[dict, dict, dict]:
    st = ng.current(db, deal, ver)
    rates = _rates(db, deal)
    vat = ng._vat(db)
    prices, inputs = {}, {}
    for vid, inp in (body.prices or {}).items():
        if vid not in st:
            raise HTTPException(422, "Предложение не найдено в КП")
        if inp.get("value") in (None, ""):
            continue
        try:
            prices[vid] = ng.new_price(st[vid], inp, vat, rates, ng.client_rates(deal))
        except ValueError as e:
            raise HTTPException(422, str(e))
        inputs[vid] = {**{k: inp.get(k) for k in ("mode", "basis", "currency", "value")}, "units": st[vid]["units"] if inp.get("per_unit", True) else 1}
    terms = {}
    for vid, t in (body.terms or {}).items():
        if vid not in st:
            raise HTTPException(422, "Предложение не найдено в КП")
        t2 = {}
        if t.get("client_payments"):
            pay = t["client_payments"]
            total = sum(float(l.get("share_pct") or 0) for l in pay)
            if any(l.get("share_pct") is not None for l in pay) and abs(total - 100) > 0.01:
                raise HTTPException(422, f"Доли платежей клиента в сумме {total:g} %, нужно 100 %")
            t2["client_payments"] = pay
        if (t.get("delivery_term") or "").strip():
            t2["delivery_term"] = t["delivery_term"].strip()[:300]
        if t2:
            terms[vid] = t2
    return prices, terms, inputs


@router.post("/preview")
def preview(deal_id: str, body: StepIn, db: Session = Depends(get_db), user: CurrentUser = Depends(CD_GD)):
    deal = get_deal_or_404(db, deal_id, user)
    ver = _need_ver(db, deal)
    prices, terms, _ = _compute(db, deal, ver, body)
    return {"variants": ng.evaluate(db, deal, ver, prices, terms)}


@router.post("/steps", status_code=201)
def create_step(deal_id: str, body: StepIn, db: Session = Depends(get_db), user: CurrentUser = Depends(CD_GD)):
    """Зафиксировать шаг торгов: цена и (или) условия → пересчёт → новая редакция КП с файлом."""
    from .pricing import _build_kp_file, revision_totals
    deal = get_deal_or_404(db, deal_id, user)
    ver = _need_ver(db, deal)
    prices, terms, inputs = _compute(db, deal, ver, body)
    if not prices and not terms:
        raise HTTPException(422, "Укажите снижение цены или новые условия")
    if body.reason not in ng.REASONS:
        raise HTTPException(422, "Укажите причину шага")
    res = ng.evaluate(db, deal, ver, prices, terms)
    warn = [f"{r['name']}: {w}" for r in res.values() for w in r["warnings"]]
    if warn and not body.confirm_below_threshold:
        raise HTTPException(422, {"message": "; ".join(warn), "confirm": "below_threshold"})
    if warn and not body.comment.strip():
        raise HTTPException(422, "Цена ниже порога маржи — обоснуйте решение в комментарии")
    st = ng.current(db, deal, ver)
    rows = ng.steps(db, deal, ver)
    before = round(sum(s["price_rub"] for s in st.values()), 2)
    # редакция КП: цена и условия шага, услуги и комплектация — из прошлой редакции
    changes = ng.revision_changes(db, deal, ver, st, prices, terms, body.show_discount)
    totals = revision_totals(deal, ver, changes)
    prev = ng._last_rev(db, deal, ver)
    rno = (prev.no if prev else 0) + 1
    path = _build_kp_file(db, deal, ver, changes, revision_no=rno)
    ensure_folders(db, deal)
    stored = adapters.storage().upload(path, deal.storage_folder, "05_kp", os.path.basename(path))
    db.add(Attachment(deal_id=deal.id, folder="05_kp", entity="version", entity_id=ver.id, file_name=os.path.basename(path),
                      storage_path=stored.path, storage_url=stored.url, sha256=stored.sha256, size=stored.size, uploaded_by=user.id))
    total = round(sum(t["total"] for t in totals.values()), 2)
    cost = sum(t["cost"] or 0 for t in totals.values())
    kind = "price_terms" if prices and terms else "price" if prices else "terms"
    desc = _describe(st, prices, terms, body)
    rev = KpRevision(deal_id=deal.id, version_id=ver.id, no=rno, created_by=user.id, description=desc, changes=changes, price_before_rub=before,
                     price_rub=total, margin_pct=round((total - cost) / total * 100, 2) if cost and total else None,
                     prices={k: t["total"] for k, t in totals.items()}, kp_path=stored.path)
    db.add(rev)
    db.flush()
    margin = None
    base_m = [r["margin"]["base"] for r in res.values() if r["margin"]["base"] is not None]
    if base_m:
        w = [r["price"]["net_rub"] for r in res.values() if r["margin"]["base"] is not None]
        margin = round(sum(m * x for m, x in zip(base_m, w)) / sum(w), 2) if sum(w) else None
    step = NegotiationStep(deal_id=deal.id, version_id=ver.id, no=(rows[-1].no if rows else 0) + 1, created_by=user.id, kind=kind, reason=body.reason,
                           comment=body.comment.strip(), input=inputs, terms=terms, result=res, price_before_rub=before, price_rub=total,
                           margin_pct=margin, show_discount=body.show_discount, kp_revision_id=rev.id)
    db.add(step)
    db.flush()
    for prev_step in rows:   # новый шаг — клиент ответил на прошлое КП: снимаем «на рассмотрении» и напоминание
        r = dict(prev_step.review or {})
        if r.get("status") == "client_review":
            t = db.get(Task, r.get("task_id")) if r.get("task_id") else None
            if t is not None and t.done_at is None:
                t.done_at = ng.now()
            r.update(status="answered", closed_at=ng.now().isoformat(timespec="seconds"), closed_by=user.id)
            prev_step.review = r
    db.add(FeedEntry(deal_id=deal.id, kind="comment", direction="out", counterparty="", subject=f"Торги, шаг {step.no}", body=desc, user_id=user.id))
    audit(db, user.id, deal.id, "negotiation", step.id, "negotiation_step", {"price_rub": before},
          {"no": step.no, "price_rub": total, "margin_pct": margin, "kind": kind, "reason": body.reason}, body.comment.strip() or None)
    if warn and not user.has(Role.GD):
        from ..services.legal_contracts import role_users
        for u in role_users(db, "gd"):
            notify(db, deal, u.id, "negotiation_below_threshold", None,
                   f"Торги по сделке {deal.number}: шаг {step.no} ниже порога маржи ({'; '.join(warn)}). Обоснование: {body.comment.strip()}")
    db.commit()
    return {**ng.step_dict(step, ng.names(db)), "kp_revision_no": rno}


def _describe(st: dict, prices: dict, terms: dict, body: StepIn) -> str:
    parts = []
    for vid, p in prices.items():
        s = st[vid]
        d = s["price_rub"] - p
        parts.append(f"{s['name']}: цена {_r(s['price_rub'])} → {_r(p)} RUB без НДС (−{_r(d)}, −{d / s['price_rub'] * 100:.2f} %)" if d >= 0
                     else f"{s['name']}: цена {_r(s['price_rub'])} → {_r(p)} RUB без НДС (+{_r(-d)})")
    for vid, t in terms.items():
        if t.get("client_payments"):
            parts.append(f"{st[vid]['name']}: новые условия оплаты")
        if t.get("delivery_term"):
            parts.append(f"{st[vid]['name']}: срок поставки «{t['delivery_term']}»")
    head = f"Торги ({ng.REASONS.get(body.reason, body.reason)})"
    return head + ": " + "; ".join(parts) + (f". {body.comment.strip()}" if body.comment.strip() else "")


def _r(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


class AnswerIn(BaseModel):
    client_answer: str = ""


@router.put("/steps/{step_id}/answer")
def step_answer(deal_id: str, step_id: str, body: AnswerIn, db: Session = Depends(get_db), user: CurrentUser = Depends(CD_GD)):
    """Ответ клиента на шаг (принял, просит ещё, ушёл к конкуренту…) — для истории и аналитики."""
    deal = get_deal_or_404(db, deal_id, user)
    s = db.get(NegotiationStep, step_id)
    if s is None or s.deal_id != deal.id:
        raise HTTPException(404)
    s.client_answer = body.client_answer.strip()[:2000]
    db.commit()
    return ng.step_dict(s, ng.names(db))


class SendIn(BaseModel):
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)   # D-188: копия отдельно
    dispatch_key: str = ""                         # D-188: повтор не отправляет второй раз
    subject: str = ""
    body: str = ""
    format: str = "pdf"          # pdf | docx | both
    manual: bool = False         # отправлено своей почтой — только отметка


@router.post("/steps/{step_id}/send")
def step_send(deal_id: str, step_id: str, body: SendIn, db: Session = Depends(get_db), user: CurrentUser = Depends(CD_GD)):
    """КП шага — клиенту: письмом из системы (когда почта подключена) или отметка «отправлено вручную»."""
    import html
    from ..adapters.mail import OutgoingMail
    from .deals import mark_kp_sent
    from .pricing import _mail_provider, _to_pdf
    deal = get_deal_or_404(db, deal_id, user)
    s = db.get(NegotiationStep, step_id)
    if s is None or s.deal_id != deal.id:
        raise HTTPException(404)
    rev = db.get(KpRevision, s.kp_revision_id) if s.kp_revision_id else None
    if rev is None:
        raise HTTPException(404, "КП шага не найдено")
    from ..services import kp_dispatch as kd
    if kd.existing(db, body.dispatch_key) is not None:
        return ng.step_dict(s, ng.names(db))
    items = kd.resolve(db, deal, body.to, body.cc)   # D-188: только контакты клиента; «Кому» и «Копия» раздельно
    to = [i["email"] for i in items if i["kind"] == "to"]
    cc = [i["email"] for i in items if i["kind"] == "cc"]
    msg_id = ""
    via = "вручную"
    if not body.manual:
        if _mail_provider() == "console":
            raise HTTPException(409, "Почта не подключена — выгрузите КП, отправьте своей почтой и отметьте «отправлено вручную»")
        docx = adapters.storage().download(rev.kp_path, os.path.join(tempfile.mkdtemp(), os.path.basename(rev.kp_path)))
        files = [docx] if body.format == "docx" else [_to_pdf(docx)] if body.format == "pdf" else [docx, _to_pdf(docx)]
        subject = body.subject or f"Коммерческое предложение {deal.number} (ред. {rev.no})"
        html_body = "".join(f"<p>{html.escape(par).replace(chr(10), '<br>')}</p>" for par in (body.body or "").split("\n\n"))
        try:
            msg_id = adapters.mail().send(OutgoingMail(to=to, cc=cc, subject=subject, body_html=html_body, attachments=files, sender=user.email or ""))
        except Exception as e:
            raise HTTPException(502, f"Письмо не отправлено: {e}")
        via = "система"
    s.sent_at = rev.sent_at = ng.now()
    s.sent_to = to + cc
    if deal.status == DealStatus.APPROVED:
        mark_kp_sent(db, deal, user, to + cc, via, subject=f"КП ред. {rev.no}")
    else:
        db.add(FeedEntry(deal_id=deal.id, kind="mail_out", direction="out", counterparty=", ".join(to + cc), subject=f"КП ред. {rev.no} (торги, шаг {s.no})",
                         body=f"Отправлено: {via}", user_id=user.id))
    kd.record(db, deal, items, "manual" if body.manual else "system", user.id, subject=f"КП ред. {rev.no} (торги, шаг {s.no})", message_id=msg_id or "",
              revision_no=rev.no, key=body.dispatch_key)
    audit(db, user.id, deal.id, "negotiation", s.id, "negotiation_kp_sent", after={"to": to, "cc": cc, "via": via, "revision": rev.no})
    db.commit()
    return ng.step_dict(s, ng.names(db))


class ReviewIn(BaseModel):
    remind_on: date
    assignee: str = "manager"      # manager — менеджер сделки, me — я
    note: str = ""


@router.put("/steps/{step_id}/review")
def set_review(deal_id: str, step_id: str, body: ReviewIn, db: Session = Depends(get_db), user: CurrentUser = Depends(CD_GD)):
    """«На рассмотрении клиента»: КП шага у клиента; в дату напоминания — задача уточнить у клиента результат (D-076)."""
    from ..services.deal_service import notify_new_task
    deal = get_deal_or_404(db, deal_id, user)
    s = db.get(NegotiationStep, step_id)
    if s is None or s.deal_id != deal.id:
        raise HTTPException(404)
    if body.remind_on < ng.today():
        raise HTTPException(422, "Дата напоминания — сегодня или позже")
    assignee = deal.manager_id if body.assignee == "manager" and deal.manager_id else user.id
    prev = dict(s.review or {})
    if prev.get("task_id"):
        old = db.get(Task, prev["task_id"])
        if old is not None and old.done_at is None:
            old.done_at = ng.now()
    u = db.get(User, assignee)
    task = Task(deal_id=deal.id, role=(Role.MANAGER.value if assignee == deal.manager_id else Role.CD.value), assignee_id=assignee,
                title=f"Уточнить у клиента результат рассмотрения КП (торги, шаг {s.no})" + (f": {body.note.strip()[:120]}" if body.note.strip() else ""),
                block="negotiation_review", due=body.remind_on)
    db.add(task)
    db.flush()
    s.review = {"status": "client_review", "since": prev.get("since") or ng.now().isoformat(timespec="seconds"), "remind_on": body.remind_on.isoformat(),
                "task_id": task.id, "assignee_id": assignee, "assignee_name": (u.display_name or u.email) if u else "", "note": body.note.strip()[:500], "set_by": user.id}
    db.add(FeedEntry(deal_id=deal.id, kind="system", subject=f"КП (торги, шаг {s.no}) на рассмотрении клиента; напоминание {body.remind_on:%d.%m.%Y}", body=body.note.strip()))
    audit(db, user.id, deal.id, "negotiation", s.id, "client_review", prev or None, s.review)
    notify_new_task(db, task)
    db.commit()
    return ng.step_dict(s, ng.names(db))


class ReviewCloseIn(BaseModel):
    client_answer: str = ""


@router.post("/steps/{step_id}/review/close")
def close_review(deal_id: str, step_id: str, body: ReviewCloseIn, db: Session = Depends(get_db), user: CurrentUser = Depends(CD_GD)):
    """Клиент ответил: статус снимается, напоминание закрывается, ответ клиента записывается."""
    deal = get_deal_or_404(db, deal_id, user)
    s = db.get(NegotiationStep, step_id)
    if s is None or s.deal_id != deal.id:
        raise HTTPException(404)
    r = dict(s.review or {})
    if r.get("status") != "client_review":
        raise HTTPException(409, "Шаг не на рассмотрении клиента")
    t = db.get(Task, r.get("task_id")) if r.get("task_id") else None
    if t is not None and t.done_at is None:
        t.done_at = ng.now()
    r.update(status="answered", closed_at=ng.now().isoformat(timespec="seconds"), closed_by=user.id)
    s.review = r
    if body.client_answer.strip():
        s.client_answer = body.client_answer.strip()[:2000]
    audit(db, user.id, deal.id, "negotiation", s.id, "client_review_closed", after={"client_answer": s.client_answer})
    db.commit()
    return ng.step_dict(s, ng.names(db))


class ClientRatesIn(BaseModel):
    rates: dict[str, Optional[float | str]] = Field(default_factory=dict)   # {CNY: 12.4}; пусто — курсы заказчика сняты
    comment: str = ""


@router.put("/client-rates")
def put_client_rates(deal_id: str, body: ClientRatesIn, db: Session = Depends(get_db), user: CurrentUser = Depends(CD_GD)):
    """Внутренние курсы заказчика (D-079): в «Торгах» рубли пересчитываются только по ним; расчёт сделки — по курсам ЦБ."""
    deal = get_deal_or_404(db, deal_id, user)
    before = ng.client_rates(deal)
    try:
        out = ng.set_client_rates(deal, body.rates, user.id, body.comment)
    except ValueError as e:
        raise HTTPException(422, str(e))
    audit(db, user.id, deal.id, "negotiation", deal.id, "client_rates", before or None, out or None, body.comment.strip() or None)
    db.commit()
    return {"client_rates": out}
