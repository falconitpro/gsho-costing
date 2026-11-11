"""Ежедневная сводка «Ваши приоритеты» (docs/operations/07): детерминированно, из фактических данных CRM, без ИИ.

Каждый элемент — реальный источник с ID, сроком и ссылкой, сформированной сервером. Тексты — по шаблону типа источника:
что сделать, почему сейчас (по фактическому сроку), следующий шаг. Никаких придуманных встреч, ответственных и связей.
Права — те же, что у исходных экранов: задачи и согласования берутся из уже отфильтрованного рабочего места, финансы — через
finance.access.Visibility и общий сервис обязательств, поддержка — через support.visible, предупреждения — по security_audit.
Сбой одного источника не ломает сводку: остальные показываются, а для упавшего — предупреждение «часть данных не обновлена».
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser
from ..models import BriefingItemState

log = logging.getLogger("gsho.briefing")

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "info": 3}
SEVERITY_TITLES = {"critical": "критично", "high": "сегодня", "medium": "завтра", "info": "скоро"}
SHOW = 5
MAX_SNOOZE_DAYS = 30


@dataclass
class Item:
    item_key: str
    source_type: str
    source_id: str
    severity: str
    title: str
    reason: str
    advice_text: str
    url: str
    source_label: str
    due_date: Optional[date] = None
    due_label: str = ""
    amount: Optional[str] = None
    currency: Optional[str] = None
    waits_for_user: bool = False          # действие ждёт именно пользователя (выше пассивного)
    deputy: bool = False
    sig_fields: dict = field(default_factory=dict)

    @property
    def signature(self) -> str:
        return hashlib.sha256(json.dumps({"k": self.item_key, **self.sig_fields}, sort_keys=True, default=str).encode()).hexdigest()

    def sort_key(self, pinned: bool):
        return (SEVERITY_RANK[self.severity], not self.waits_for_user, not pinned, self.due_date or date.max, self.item_key)

    def dto(self, state: Optional[BriefingItemState] = None) -> dict:
        return {"item_key": self.item_key, "source_type": self.source_type, "source_id": self.source_id, "source_signature": self.signature,
                "severity": self.severity, "severity_title": SEVERITY_TITLES[self.severity], "title": self.title, "reason": self.reason,
                "advice_text": self.advice_text, "due_date": self.due_date.isoformat() if self.due_date else None, "due_label": self.due_label,
                "source_label": self.source_label, "url": self.url, "amount": self.amount, "currency": self.currency, "deputy": self.deputy,
                "pinned": bool(state and state.pinned_at), "snoozed_until": state.snoozed_until.isoformat() if state and state.snoozed_until else None,
                "hidden": bool(state and state.hidden_at)}


def _due(today: date, d: Optional[date]) -> tuple[Optional[str], str]:
    """(severity, подпись) по фактическому сроку; None — срок дальше трёх дней, в сводку не берём."""
    if d is None:
        return None, ""
    n = (d - today).days
    if n < 0:
        return "critical", f"Просрочено на {-n} дн."
    if n == 0:
        return "high", "Срок — сегодня"
    if n == 1:
        return "medium", "Срок — завтра"
    if n <= 3:
        return "info", f"Срок — через {n} дн. ({d:%d.%m})"
    return None, ""


def _money(v) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{x:,.2f}".replace(",", " ").replace(".", ",").rstrip("0").rstrip(",")


# ------------------------------------------------------------------ источники (каждый — отдельно, сбой не роняет сводку)

def _tasks(db, user, ctx) -> list[Item]:
    out = []
    today = ctx["today"]
    for t in ctx.get("tasks") or []:
        d = date.fromisoformat(t["due"]) if t.get("due") else None
        sev, label = _due(today, d)
        if sev is None:
            continue
        deal = f" · сделка {t['deal_number']}" + (f" ({t['client_name']})" if t.get("client_name") else "") if t.get("deal_number") else ""
        out.append(Item(item_key=f"task:{t['id']}", source_type="task", source_id=t["id"], severity=sev, title=t["title"],
                        reason=label + deal + (" · по замещению" if t.get("as_deputy") else ""),
                        advice_text="Откройте сделку, выполните задачу и отметьте результат", url=f"/deals/{t['deal_id']}",
                        source_label="Задача", due_date=d, due_label=label, waits_for_user=True, deputy=bool(t.get("as_deputy")),
                        sig_fields={"due": t["due"], "deputy": bool(t.get("as_deputy"))}))
    return out


_APPROVAL_ADVICE = {"deal": "Откройте расчёт и примите решение", "package": "Откройте пакет документов и проверьте его",
                    "contract": "Откройте договор и поставьте визу или верните на доработку", "claim": "Откройте претензию и проверьте её",
                    "user": "Проверьте изменения данных сотрудника"}


def _approvals(db, user, ctx) -> list[Item]:
    out = []
    for a in ctx.get("approvals") or []:
        url = a.get("url") or ""
        if not url.startswith("/"):
            continue
        sid = hashlib.sha256(f"{a['kind']}|{url}|{a['title']}".encode()).hexdigest()[:16]
        since = a.get("waiting_since")
        days = None
        if since:
            try:
                days = (ctx["today"] - datetime.fromisoformat(since).date()).days
            except ValueError:
                days = None
        reason = "Ждёт вашего решения" + (f" {days} дн." if days and days > 0 else "")
        out.append(Item(item_key=f"approval:{a['kind']}:{sid}", source_type="approval", source_id=sid, severity="high",
                        title=a["title"], reason=reason, advice_text=_APPROVAL_ADVICE.get(a["kind"], "Откройте источник и проверьте детали"),
                        url=url, source_label="Согласование", waits_for_user=True, sig_fields={"since": since, "sub": a.get("sub", "")[:60]}))
    return out


def _support(db, user, ctx) -> list[Item]:
    from ..models import SupportTicket
    from . import support as sup
    out = []
    today = ctx["today"]
    unread = sup.unread_for(db, user.id)
    for t in db.scalars(select(SupportTicket).where(SupportTicket.reporter_id == user.id, SupportTicket.status.in_(("waiting_reporter", "in_progress", "resolved")))).all():
        if t.status == "waiting_reporter":
            sev, reason, advice = "high", "Поддержке нужны данные от вас", "Откройте обращение и ответьте специалисту"
        elif t.status == "resolved":
            sev, reason, advice = "info", "Поддержка отметила обращение решённым", "Проверьте решение: закройте обращение или нажмите «Проблема осталась»"
        elif unread.get(t.id):
            sev, reason, advice = "medium", "Новый ответ поддержки", "Откройте обращение и прочитайте ответ"
        else:
            continue
        out.append(Item(item_key=f"support:{t.id}", source_type="support", source_id=t.id, severity=sev, title=f"{t.number} · {t.subject}",
                        reason=reason, advice_text=advice, url=f"/support/{t.id}", source_label="Поддержка", waits_for_user=t.status == "waiting_reporter",
                        sig_fields={"status": t.status, "last": t.last_activity_at}))
    if sup.is_admin(user, 2):
        q = select(SupportTicket).where(SupportTicket.status.in_(sup.ACTIVE), (SupportTicket.assignee_id == user.id) | (SupportTicket.assignee_id.is_(None)))
        for t in db.scalars(q).all():
            if t.reporter_id == user.id:
                continue
            s = sup.sla(t)
            if s["overdue"]:
                sev, reason = ("critical" if t.priority == "critical" else "high"), f"Первый ответ просрочен (срок {s['hours']} ч, {sup.SLA_NOTE})"
            elif t.status == "new" and t.assignee_id is None:
                sev, reason = "medium", "Новое обращение в очереди, никто не назначен"
            elif t.assignee_id == user.id and t.status == "in_progress" and unread.get(t.id):
                sev, reason = "medium", "Автор ответил в назначенном вам обращении"
            else:
                continue
            due = datetime.fromisoformat(s["due_at"]).date() if s["due_at"] else None
            out.append(Item(item_key=f"support:{t.id}", source_type="support", source_id=t.id, severity=sev, title=f"{t.number} · {t.subject}",
                            reason=reason, advice_text="Откройте обращение в очереди и ответьте автору", url=f"/support/admin?ticket={t.id}",
                            source_label="Очередь поддержки", due_date=due, waits_for_user=t.assignee_id == user.id,
                            sig_fields={"status": t.status, "assignee": t.assignee_id, "overdue": s["overdue"], "priority": t.priority}))
    return out


def _alerts(db, user, ctx) -> list[Item]:
    if not user.can("security_audit", 1):
        return []
    from ..models import SecurityAlert
    from .security_alerts import RULES
    out = []
    for a in db.scalars(select(SecurityAlert).where(SecurityAlert.status == "new", SecurityAlert.severity.in_(("critical", "high")))).all():
        rule = RULES.get(a.rule_key)
        out.append(Item(item_key=f"alert:{a.id}", source_type="security_alert", source_id=a.id, severity="critical" if a.severity == "critical" else "high",
                        title=f"Предупреждение безопасности: {rule.title.lower() if rule else a.rule_key}", reason=a.summary[:160],
                        advice_text="Откройте «Журнал и предупреждения», проверьте и отметьте с комментарием",
                        url="/settings?s=security_audit", source_label="Безопасность", sig_fields={"status": a.status, "count": a.occurrence_count}))
    return out


def _finance_questions(db, user, ctx) -> list[Item]:
    from ..models.finance import FinanceQuestion
    out = []
    today = ctx["today"]
    who = [user.id] + list(user.acting_for or [])
    for q in db.scalars(select(FinanceQuestion).where(FinanceQuestion.assignee_id.in_(who), FinanceQuestion.status == "open")).all():
        sev, label = _due(today, q.due_on)
        if sev is None:
            sev, label = ("medium", "Срок не указан") if q.due_on is None else (None, "")
        if sev is None:
            continue
        out.append(Item(item_key=f"finance_question:{q.id}", source_type="finance_question", source_id=q.id, severity=sev,
                        title="Ответить на вопрос по финансам", reason=label + (" · по замещению" if q.assignee_id != user.id else ""),
                        advice_text="Откройте вопрос, проверьте данные и ответьте", url=f"/finance/questions/{q.id}", source_label="Вопрос по финансам",
                        due_date=q.due_on, due_label=label, waits_for_user=True, deputy=q.assignee_id != user.id,
                        sig_fields={"due": q.due_on, "status": q.status}))
    return out


def _obligations(db, user, ctx) -> list[Item]:
    """Просроченные и ближайшие платежи — только с правом на раздел «Финансы», через тот же сервис и ту же видимость."""
    if not user.can("finance_section", 1):
        return []
    from .finance import obligations as ob
    from .finance.access import Visibility
    today = ctx["today"]
    vis = Visibility(db, user)
    res = ob.query(db, vis, ob.ObligationFilter(as_of=today, status="open", date_to=today + timedelta(days=3), limit=200))
    out = []
    for o in res["items"]:
        on = date.fromisoformat(o["legal_due_on"]) if o["legal_due_on"] else (date.fromisoformat(o["forecast_on"]) if o["forecast_on"] else None)
        sev, label = _due(today, on)
        if sev is None:
            continue
        if sev == "critical":
            sev = "high"     # просроченный платёж важен, но сам по себе не блокирует работу пользователя (07 §6)
        incoming = o["direction"] == "in"
        cp = o["counterparty"]["name"] or ""
        num = o["source"]["number"]
        out.append(Item(item_key=f"obligation:{o['id']}", source_type="obligation", source_id=o["id"], severity=sev,
                        title=("Поступление от " if incoming else "Платёж ") + cp + (f" · {num}" if num else ""),
                        reason=label + f" · остаток {_money(o['remaining'])} {o['currency']}" + (" · есть неподтверждённые оплаты" if "needs_reconciliation" in o["flags"] else ""),
                        advice_text=("Проверьте поступление и при необходимости напомните клиенту" if incoming else "Проверьте оплату с ответственным за платёж"),
                        url=o["source"]["url"] or "/finance", source_label="Финансы", due_date=on, due_label=label,
                        amount=o["remaining"], currency=o["currency"], sig_fields={"remaining": o["remaining"], "due": on, "status": o["status"]}))
    return out


def _money_summary(db, user, ctx) -> list[Item]:
    """Только finance_analytics без раздела «Финансы»: агрегат из того же блока «Денежный поток» рабочего места."""
    m = ctx.get("money")
    if not m or user.can("finance_section", 1) or not m.get("receivable_count"):
        return []
    n = m["receivable_count"]
    return [Item(item_key="money:receivables", source_type="money", source_id="receivables", severity="medium",
                 title=f"Просроченных поступлений от клиентов: {n}", reason=f"Сумма в рублях: {_money(m.get('receivable_sum', 0))} RUB",
                 advice_text="Откройте сводку по деньгам и проверьте поступления", url=m.get("url") or "/summary?kind=money", source_label="Деньги",
                 amount=str(m.get("receivable_sum", 0)), currency="RUB", sig_fields={"n": n, "sum": m.get("receivable_sum")})]


def _legal(db, user, ctx) -> list[Item]:
    out = []
    today = ctx["today"]
    for p in ctx.get("poas") or []:
        if not p.valid_to:
            continue
        sev, label = _due(today, p.valid_to)
        if sev is None or (p.valid_to - today).days > 3:
            continue
        out.append(Item(item_key=f"poa:{p.id}", source_type="poa", source_id=p.id, severity="medium" if sev in ("info", "medium") else "high",
                        title=f"Доверенность {p.number} — срок действия заканчивается", reason=label.replace("Срок", "Действует до") if label else "",
                        advice_text="Откройте доверенность: продлите её или отзовите", url="/legal?tab=poa", source_label="Доверенность",
                        due_date=p.valid_to, due_label=label, sig_fields={"valid_to": p.valid_to, "status": p.status}))
    for x in (ctx.get("cin") or {}).get("expiring") or []:
        if x.get("days", 99) > 3 or x.get("days", 0) < 0:
            continue
        d = date.fromisoformat(x["valid_to"])
        sev, label = _due(today, d)
        out.append(Item(item_key=f"contract:{x['id']}" if x.get("id") else f"contract:{x['number']}", source_type="contract", source_id=str(x.get("id") or x["number"]),
                        severity="medium", title=f"Договор {x['number']} · {x['cp_name']} — заканчивается срок",
                        reason=(label or "") + (" · автопролонгация" if x.get("auto_renew") else ""), advice_text="Откройте договор и решите: продлить или завершить",
                        url=x["url"], source_label="Договор", due_date=d, due_label=label, sig_fields={"valid_to": x["valid_to"], "auto": x.get("auto_renew")}))
    for cl in ctx.get("claims") or []:
        if not (cl.answer_due and cl.status in ("sent", "received")):
            continue
        sev, label = _due(today, cl.answer_due)
        if sev is None:
            continue
        incoming = cl.direction != "outgoing"
        out.append(Item(item_key=f"claim:{cl.id}", source_type="claim", source_id=cl.id, severity=sev if incoming else ("medium" if sev != "critical" else "high"),
                        title=f"Претензия {cl.number}: " + ("ответить" if incoming else "срок ответа контрагента"), reason=label,
                        advice_text="Откройте претензию и подготовьте ответ" if incoming else "Проверьте, пришёл ли ответ, и решите следующий шаг",
                        url=f"/legal?tab=claims&open={cl.id}", source_label="Претензия", due_date=cl.answer_due, due_label=label,
                        waits_for_user=incoming, sig_fields={"due": cl.answer_due, "status": cl.status}))
    return out


SOURCES: list[tuple[str, str, Callable]] = [
    ("tasks", "задачи", _tasks), ("approvals", "согласования", _approvals), ("support", "поддержка", _support),
    ("alerts", "предупреждения безопасности", _alerts), ("finance_questions", "вопросы по финансам", _finance_questions),
    ("obligations", "платежи", _obligations), ("money", "денежный поток", _money_summary), ("legal", "юридические сроки", _legal),
]


# ------------------------------------------------------------------ сборка

def candidates(db: Session, user: CurrentUser, ctx: dict) -> tuple[list[Item], list[str]]:
    items: dict[str, Item] = {}
    warnings: list[str] = []
    for code, title, fn in SOURCES:
        try:
            with db.begin_nested():
                for it in fn(db, user, ctx):
                    old = items.get(it.item_key)
                    if old is None or SEVERITY_RANK[it.severity] < SEVERITY_RANK[old.severity]:
                        items[it.item_key] = it     # дедуп: один источник — один элемент (наиболее важный вариант)
        except Exception as e:   # noqa: BLE001 — один источник не роняет сводку
            log.warning("briefing source %s failed: %s", code, type(e).__name__)
            warnings.append(f"Часть данных не обновлена: {title}")
    return list(items.values()), warnings


def states(db: Session, user_id: str) -> dict[str, BriefingItemState]:
    return {s.item_key: s for s in db.scalars(select(BriefingItemState).where(BriefingItemState.user_id == user_id)).all()}


def build(db: Session, user: CurrentUser, ctx: dict) -> dict:
    now = datetime.now(timezone.utc)
    cands, warnings = candidates(db, user, ctx)
    st = states(db, user.id)
    visible, snoozed, hidden = [], 0, 0
    for it in cands:
        s = st.get(it.item_key)
        same = s is not None and s.source_signature == it.signature       # существенное изменение источника возвращает элемент
        if same and s.hidden_at:
            hidden += 1
            continue
        if same and s.snoozed_until and s.snoozed_until > now:
            snoozed += 1
            continue
        visible.append((it, s if same else None))
    visible.sort(key=lambda x: x[0].sort_key(bool(x[1] and x[1].pinned_at)))
    shown = visible[:SHOW]
    more = len(visible) - len(shown)
    return {"generated_at": now.isoformat(), "items": [it.dto(s) for it, s in shown], "more": more,
            "more_url": "/tasks" if more else None, "snoozed": snoozed, "hidden": hidden, "warnings": warnings,
            "empty_text": "Срочных дел по вашим задачам и срокам нет" if not shown else ""}


def find(db: Session, user: CurrentUser, ctx: dict, item_key: str, signature: Optional[str]) -> Item:
    """Действия только над своим текущим элементом: ключ и подпись сверяются со свежим расчётом."""
    from fastapi import HTTPException
    cands, _ = candidates(db, user, ctx)
    it = next((c for c in cands if c.item_key == item_key), None)
    if it is None:
        raise HTTPException(404, "Элемент сводки не найден — обновите сводку")
    if signature is not None and signature != it.signature:
        raise HTTPException(409, "Источник изменился — обновите сводку")
    return it


def _state(db: Session, user_id: str, it: Item) -> BriefingItemState:
    s = db.scalar(select(BriefingItemState).where(BriefingItemState.user_id == user_id, BriefingItemState.item_key == it.item_key))
    if s is None:
        s = BriefingItemState(user_id=user_id, item_key=it.item_key, source_signature=it.signature)
        db.add(s)
    elif s.source_signature != it.signature:     # новая версия источника — прежние отметки к ней не относятся
        s.source_signature, s.pinned_at, s.snoozed_until, s.hidden_at = it.signature, None, None, None
    return s


def pin(db: Session, user_id: str, it: Item, on: bool) -> None:
    s = _state(db, user_id, it)
    s.pinned_at = (s.pinned_at or datetime.now(timezone.utc)) if on else None


def snooze(db: Session, user_id: str, it: Item, until: date) -> datetime:
    from fastapi import HTTPException
    today = datetime.now(timezone.utc).date()
    if until <= today or (until - today).days > MAX_SNOOZE_DAYS:
        raise HTTPException(400, f"Отложить можно на 1–{MAX_SNOOZE_DAYS} дней")
    s = _state(db, user_id, it)
    s.snoozed_until = datetime.combine(until, datetime.min.time(), tzinfo=timezone.utc)
    return s.snoozed_until


def hide(db: Session, user_id: str, it: Item) -> None:
    s = _state(db, user_id, it)
    s.hidden_at = s.hidden_at or datetime.now(timezone.utc)
