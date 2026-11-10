"""Уточнения «Финансов» (docs/finance/01, раздел 11; 02, раздел 4.10; 04, раздел 11; D-140, D-141).

- вопрос задаёт пользователь раздела по видимому ему объекту; контекст для ответственного формирует сервер — минимальный и без
  себестоимости, закупочных цен, остатков и банковских реквизитов; клиент свой контекст не передаёт;
- ответственный (или его заместитель) видит только свои вопросы и этот контекст — без права на раздел;
- ответ ничего не исправляет: ФД/ЗГД проверяет ответ; «данные исправлены» отмечается только с реальной ссылкой на изменение источника;
- открытые вопросы попадают в «Мои задачи» task-like элементом (Task не создаётся), уведомление — через общий `notify`.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...calc.types import COST_ITEMS
from ...models import Contract, Deal, FinanceAccount, FinanceObligation, FinanceQuestion, FinanceRecognitionEvent, FinanceTransaction, User
from ..deal_service import audit, notify
from .money import ru, ru_date, s

SUBJECTS = {"obligation": "строка платёжного календаря", "transaction": "банковская операция", "recognition": "событие признания",
            "account": "финансовый счёт", "deal_metric": "показатель экономики сделки"}
STATUS = {"open": "ждёт ответа", "answered": "ответ получен", "verified": "ответ проверен", "rejected": "ответ отклонён — нужно уточнить", "closed": "закрыт"}
METRICS = {"revenue": "выручка", "cost": "себестоимость", "profit": "прибыль", "margin": "маржа", "cash_gap": "кассовый разрыв", "cash": "движение денег"}
SENSITIVE_SOURCES = ("calculation", "purchase_order")   # суммы раскрывают закупочные условия — ответственному не показываются


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _d(v) -> Optional[str]:
    return v.isoformat() if v else None


def subject(db: Session, user, vis, subject_type: str, subject_id: str) -> dict:
    """Объект вопроса, видимый задающему: deal_id, контекст для ответственного и ссылка для финансового пользователя. Чужое → 404."""
    if subject_type not in SUBJECTS:
        raise HTTPException(422, "Тип объекта: " + ", ".join(SUBJECTS))
    nf = HTTPException(404, "Объект не найден")
    if subject_type == "obligation":
        o = db.get(FinanceObligation, subject_id)
        if o is None or not vis.obligation(o):
            raise nf
        d = db.get(Deal, o.deal_id) if o.deal_id else None
        k = db.get(Contract, o.contract_id) if o.contract_id else None
        ctx = {"Что": o.title, "Направление": "нам платят" if o.direction == "in" else "мы платим", "Контрагент": o.counterparty_name_snapshot,
               "Срок по договору": ru_date(o.legal_due_on) if o.legal_due_on else (o.legal_due_text or "не определён"),
               "Ожидаемая дата": ru_date(o.forecast_on) if o.forecast_on else None}
        if o.source_type not in SENSITIVE_SOURCES:
            ctx["Сумма"] = ru(o.amount, o.currency)
        if d:
            ctx["Сделка"] = d.number
        if k:
            ctx["Договор"] = k.number
        return {"deal_id": o.deal_id, "context": ctx, "url": f"/finance?tab=calendar&open={o.id}", "title": o.title}
    if subject_type == "transaction":
        from .access import can_read
        t = db.get(FinanceTransaction, subject_id)
        if t is None or not can_read(user):
            raise nf
        ctx = {"Операция": "поступление" if t.direction == "in" else "списание", "Дата": ru_date(t.booked_at.date()), "Сумма": ru(t.amount, t.currency),
               "Контрагент": t.counterparty_name or "—", "Назначение платежа": t.purpose or "—", "Номер документа банка": t.external_ref or "—"}
        return {"deal_id": None, "context": ctx, "url": f"/finance?tab=liquidity&tx={t.id}", "title": f"операция {t.external_ref or ru_date(t.booked_at.date())}"}
    if subject_type == "recognition":
        from .recognition import can_view_all
        e = db.get(FinanceRecognitionEvent, subject_id)
        if e is None or not vis.deal(e.deal_id) or not (can_view_all(user) or e.created_by == user.id):
            raise nf
        d = db.get(Deal, e.deal_id) if e.deal_id else None
        title = "выручка" if e.kind == "revenue" else next((t for _, k, t in COST_ITEMS if k == e.cost_item_code), "расход")
        ctx = {"Что": f"признание: {title}", "Дата признания": ru_date(e.recognized_on), "Документ": f"{e.document_type} № {e.document_number}".strip() if e.document_number else "без документа",
               "Сделка": d.number if d else "—"}
        if e.kind == "revenue":
            ctx["Сумма"] = ru(e.amount, e.currency)
        return {"deal_id": e.deal_id, "context": ctx, "url": f"/finance?tab=economy&deal={e.deal_id}", "title": f"признание «{title}»"}
    if subject_type == "account":
        from .access import can_read
        a = db.get(FinanceAccount, subject_id)
        if a is None or not can_read(user):
            raise nf
        return {"deal_id": None, "context": {"Счёт": a.title, "Валюта": a.currency}, "url": f"/finance?tab=liquidity&account={a.id}", "title": f"счёт «{a.title}»"}
    # deal_metric: {deal_id}:{metric}
    deal_id, _, metric = subject_id.partition(":")
    from ...core.roles import READ
    if metric not in METRICS or not vis.deal(deal_id) or not (user.can("finance_analytics", READ) and user.can("cost", READ)):
        raise nf
    d = db.get(Deal, deal_id)
    if d is None:
        raise nf
    return {"deal_id": d.id, "context": {"Сделка": d.number, "Показатель": METRICS[metric]}, "url": f"/finance?tab=economy&deal={d.id}", "title": f"{METRICS[metric]} по сделке {d.number}"}


def _user(db: Session, uid: Optional[str]) -> Optional[User]:
    return db.get(User, uid) if uid else None


def _name(db: Session, uid: Optional[str]) -> str:
    u = _user(db, uid)
    return u.display_name if u else ""


def _hist(q: FinanceQuestion, by: Optional[str], action: str, comment: str = "") -> None:
    q.history = [*(q.history or []), {"at": _now().isoformat(), "by": by, "action": action, "comment": comment[:500]}]


def is_assignee(user, q: FinanceQuestion) -> bool:
    return q.assignee_id == user.id or q.assignee_id in user.acting_for


def create(db: Session, user, vis, body: dict) -> FinanceQuestion:
    text = (body.get("question") or "").strip()
    if len(text) < 5:
        raise HTTPException(422, "Сформулируйте вопрос (не короче 5 символов)")
    a = _user(db, body.get("assignee_id"))
    if a is None or not a.is_active:
        raise HTTPException(422, "Ответственный не найден или отключён")
    sub = subject(db, user, vis, body.get("subject_type") or "", str(body.get("subject_id") or ""))
    due = body.get("due_on")
    if due and due < date.today():
        raise HTTPException(422, "Срок ответа — не раньше сегодняшнего дня")
    q = FinanceQuestion(subject_type=body["subject_type"], subject_id=str(body["subject_id"]), deal_id=sub["deal_id"], question=text[:4000],
                        context_snapshot={"title": sub["title"], "fields": {k: v for k, v in sub["context"].items() if v not in (None, "")}},
                        asked_by=user.id, assignee_id=a.id, due_on=due, status="open")
    _hist(q, user.id, "create")
    db.add(q)
    db.flush()
    audit(db, user.id, sub["deal_id"], "finance_question", q.id, "create", after={"subject": f"{q.subject_type}:{q.subject_id}", "assignee": a.id, "due_on": _d(due)})
    deal = db.get(Deal, sub["deal_id"]) if sub["deal_id"] else None
    notify(db, deal, a.id, "finance_question", None, f"Уточнение по «Финансам» от {user.display_name}: {sub['title']}" + (f", срок {due:%d.%m.%Y}" if due else ""))
    return q


def answer(db: Session, user, q: FinanceQuestion, text: str) -> FinanceQuestion:
    if not is_assignee(user, q):
        raise HTTPException(404, "Вопрос не найден")
    if q.status not in ("open", "rejected"):
        raise HTTPException(409, f"Вопрос в статусе «{STATUS[q.status]}» — ответ уже дан")
    text = (text or "").strip()
    if len(text) < 2 and not q.files:
        raise HTTPException(422, "Напишите ответ или приложите файл")
    q.answer, q.answered_by, q.answered_at, q.status = text[:4000], user.id, _now(), "answered"
    _hist(q, user.id, "answer")
    audit(db, user.id, q.deal_id, "finance_question", q.id, "answer")
    deal = db.get(Deal, q.deal_id) if q.deal_id else None
    notify(db, deal, q.asked_by, "finance_question", None, f"Получен ответ на уточнение: {q.context_snapshot.get('title', '')}")
    return q


def review(db: Session, user, q: FinanceQuestion, decision: str, comment: str, source_ref: str) -> FinanceQuestion:
    if q.status != "answered":
        raise HTTPException(409, "Проверить можно только полученный ответ")
    if decision not in ("verified", "rejected"):
        raise HTTPException(422, "decision: verified | rejected")
    if decision == "rejected" and len((comment or "").strip()) < 3:
        raise HTTPException(422, "Укажите, что нужно уточнить")
    q.status, q.reviewed_by, q.reviewed_at, q.review_comment = decision, user.id, _now(), (comment or "").strip()[:2000]
    if decision == "verified" and (source_ref or "").strip():
        q.source_update_ref, q.source_updated_by, q.source_updated_at = source_ref.strip()[:255], user.id, _now()
    _hist(q, user.id, decision, comment or "")
    audit(db, user.id, q.deal_id, "finance_question", q.id, decision, after={"source_update_ref": q.source_update_ref})
    if decision == "rejected":
        deal = db.get(Deal, q.deal_id) if q.deal_id else None
        notify(db, deal, q.assignee_id, "finance_question", None, f"Ответ на уточнение нужно дополнить: {q.context_snapshot.get('title', '')}")
    return q


def close(db: Session, user, q: FinanceQuestion, comment: str) -> FinanceQuestion:
    if q.status == "closed":
        return q
    q.status = "closed"
    _hist(q, user.id, "close", comment or "")
    audit(db, user.id, q.deal_id, "finance_question", q.id, "close", after={"comment": (comment or "")[:500]})
    return q


def scoped_dto(db: Session, q: FinanceQuestion) -> dict:
    """То, что видит ответственный: вопрос, разрешённый контекст, свой ответ, файлы. Без ссылок в финансовый контур."""
    return {"id": q.id, "subject_title": SUBJECTS.get(q.subject_type, q.subject_type), "question": q.question, "context": q.context_snapshot or {},
            "asked_by": _name(db, q.asked_by), "due_on": _d(q.due_on), "status": q.status, "status_title": STATUS[q.status], "answer": q.answer,
            "answered_at": _d(q.answered_at), "review_comment": q.review_comment if q.status == "rejected" else "",
            "files": [{k: f.get(k) for k in ("id", "name", "size", "uploaded_at")} for f in (q.files or [])],
            "can_answer": q.status in ("open", "rejected"), "created_at": _d(q.created_at)}


def full_dto(db: Session, user, vis, q: FinanceQuestion) -> dict:
    out = scoped_dto(db, q)
    try:
        url = subject(db, user, vis, q.subject_type, q.subject_id)["url"]
    except HTTPException:
        url = None
    d = db.get(Deal, q.deal_id) if q.deal_id else None
    out.update({"subject_type": q.subject_type, "subject_id": q.subject_id, "subject_url": url, "deal": {"id": d.id, "number": d.number} if d else None,
                "assignee": {"id": q.assignee_id, "name": _name(db, q.assignee_id)}, "asked_by_id": q.asked_by, "review_comment": q.review_comment,
                "reviewed_by": _name(db, q.reviewed_by), "reviewed_at": _d(q.reviewed_at), "source_update_ref": q.source_update_ref,
                "source_updated_at": _d(q.source_updated_at),
                "history": [{**h, "by": _name(db, h.get("by"))} for h in (q.history or [])], "can_answer": is_assignee(user, q) and q.status in ("open", "rejected")})
    return out


def task_items(db: Session, user) -> list[dict]:
    """Открытые вопросы пользователя (и замещаемых) — для «Мои задачи»: kind=finance_question, свой URL, без финансового контекста."""
    ids = {user.id, *user.acting_for}
    rows = db.scalars(select(FinanceQuestion).where(FinanceQuestion.assignee_id.in_(ids), FinanceQuestion.status.in_(("open", "rejected")))
                      .order_by(FinanceQuestion.due_on.nulls_last(), FinanceQuestion.created_at)).all()
    today = date.today()
    out = []
    for q in rows:
        d = db.get(Deal, q.deal_id) if q.deal_id else None
        due = q.due_on or today
        out.append({"id": f"fq:{q.id}", "kind": "finance_question", "url": f"/finance/questions/{q.id}", "deal_id": d.id if d else None,
                    "deal_number": d.number if d else "Финансы", "deal_status": None, "title": f"Уточнение: {(q.context_snapshot or {}).get('title', '')}"[:255],
                    "role": "", "block": "finance_question", "due": due.isoformat(), "overdue": due < today, "days_left": (due - today).days,
                    "client_name": d.client.name if d and d.client else "", "site": d.site if d else "", "equipment": "",
                    "hint": "ответьте на вопрос — " + ("ответ отклонён, дополните" if q.status == "rejected" else f"спрашивает {_name(db, q.asked_by)}"),
                    "tab": "", "can_close": False, "as_deputy": q.assignee_id != user.id})
    return out
