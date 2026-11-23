"""Рабочее место ЗГД, новый вид «Пульт» (D-153, ТЗ «Рабочее место ЗГД: интерфейс» v1 от 23.11.2026, первый релиз: PR-1…PR-3).

Только подтверждённые данные: всё считается из тех же списков, что и прежнее рабочее место (сделки, этапы исполнения, задачи,
согласования, «Финансы», юр. блок, Канцелярия) — новых сущностей нет. Чего в системе нет (план продаж, оргструктура «отдел →
руководитель», управленческие поручения с приёмкой), то не выдумывается: «Направления» строятся по ролям сотрудников, «Поручения» —
резолюции Канцелярии с контролем исполнения, план/факт к плану не показывается (плана нет).

Каждая карточка: значение, подпись, период, источник, дата расчёта, качество данных (confirmed / partial / unavailable) и ссылка
на список с теми же условиями. Неизвестное — null, а не 0.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.workflow import DealStatus

LOGISTICS = ("readiness", "shipment", "border", "declaration", "delivery")
PAYMENTS = ("client_advance", "client_final", "factory_advance", "factory_final", "lc_opened")
FACTORY = ("production", "factory_preacceptance", "local_option_order")
SERVICE = ("commissioning", "local_option_install")
PRIO = {"critical": 0, "high": 1, "normal": 2}


def _money(v: Optional[float], ccy: str = "RUB") -> str:
    if v is None:
        return "нет данных"
    sign = {"RUB": "₽", "CNY": "¥", "USD": "$", "EUR": "€"}.get((ccy or "RUB").upper(), (ccy or "").upper())
    if abs(v) >= 1e6:
        return f"{v / 1e6:,.1f} млн {sign}".replace(",", " ").replace(".", ",")
    if abs(v) >= 1e3:
        return f"{v / 1e3:,.0f} тыс. {sign}".replace(",", " ")
    return f"{v:,.0f} {sign}".replace(",", " ")


def money_by_currency(items: list[tuple[Optional[float], str]]) -> str:
    """«12,1 млн ¥ · 3,4 млн ₽» — суммы в разных валютах не складываются (раньше показывались только рубли, D-153)."""
    acc: dict[str, float] = defaultdict(float)
    for amount, ccy in items:
        if amount:
            acc[(ccy or "RUB").upper()] += float(amount)
    return " · ".join(_money(v, k) for k, v in sorted(acc.items(), key=lambda x: (x[0] != "RUB", x[0]))) or "—"


def _kpi(key: str, title: str, value: Any, sub: str, url: str, tone: str = "", quality: str = "confirmed", period: str = "", source: str = "") -> dict:
    return {"key": key, "title": title, "value": value, "sub": sub, "url": url, "tone": tone, "data_quality": quality, "period": period, "source": source}


def build(db: Session, user, *, today: date, names: dict, fin: bool, open_deals: list, open_parts: list, exec_rows: list, won_q: list, won_q_parts: list,
          approvals: list, money: Optional[dict], legal: Optional[dict], cin: dict, claims: list, poas: list, price) -> dict:
    from ..models import ExecutionStage, OfficeDoc, User, WarrantyClaim
    from ..services.execution_service import won_variant
    now = datetime.now(timezone.utc).isoformat()
    q = (today.month - 1) // 3 + 1
    period = f"{q} квартал {today.year}"

    # ---------------- показатели
    in_work = sum(price(d) or 0 for d in open_deals) + sum(p["price_rub"] for p in open_parts)
    risk_rows = [r for r in exec_rows if r["overdue"]]
    risk_sum = sum(price(r["deal"]) or 0 for r in risk_rows)
    won_sum = sum(price(d) or 0 for d in won_q) + sum(p["price_rub"] for p in won_q_parts)
    margins = []
    for d in won_q:
        v = won_variant(d) if d.variants else None
        p = price(d)
        if v is not None and getattr(v, "margin_pct", None) is not None and p:
            margins.append((float(v.margin_pct), p))
    m_quality = "unavailable" if not margins else ("partial" if len(margins) < len(won_q) else "confirmed")
    m_val = round(sum(m * p for m, p in margins) / sum(p for _, p in margins), 1) if margins else None
    from ..services.finance.cashview import rows as fin_rows
    rec_rows = [r for r in fin_rows(db, user, today, today) if r["direction"] == "in" and r["overdue_days"] > 0] if fin else []
    in_exec = [r for r in exec_rows if r["deal"].status == DealStatus.EXECUTION]
    overdue_stages = sum(r["overdue"] for r in exec_rows)
    kpis = [
        _kpi("active", "Активные сделки", len(open_deals) + len(open_parts), (_money(in_work) + " в работе · " if fin else "") + f"ГШО {len(open_deals)} · ЗЧ {len(open_parts)}",
             "/deals?block=work", source="реестр сделок: в работе"),
        _kpi("risk", "Сделки под риском", len(risk_rows), (f"на {_money(risk_sum)} · " if fin and risk_sum else "") + "этапы без факта после плановой даты",
             "/deals?block=won&flag=overdue", "bad" if risk_rows else "ok", source="этапы исполнения: просрочено (уточнённая или плановая дата)"),
        _kpi("won", "Выиграно за квартал", len(won_q) + len(won_q_parts), (_money(won_sum) + " · " if fin else "") + "план продаж не задан — без % к плану",
             "/deals?block=won", "ok" if won_q or won_q_parts else "", period=period, source="выигранные сделки по дате контракта"),
        _kpi("margin", "Маржа выигранных", f"{m_val:.1f} %".replace(".", ",") if m_val is not None else None,
             (f"по {len(margins)} из {len(won_q)} сделок · " if won_q else "") + "ориентир утверждённого расчёта" if fin else "нет доступа к финансам",
             "/deals?block=won", quality=m_quality if fin else "unavailable", period=period, source="маржа базового сценария утверждённого расчёта, средневзвешенная по цене"),
        _kpi("receivable", "Дебиторка просрочена", len(rec_rows) if fin else None, money_by_currency([(r["amount"], r["currency"]) for r in rec_rows]) if fin else "нет доступа к финансам",
             (money or {}).get("url") or "/finance?tab=liquidity", "bad" if rec_rows else "ok", quality="confirmed" if fin else "unavailable",
             source="«Финансы»: обязательства «нам платят» с прошедшим сроком, остаток к оплате"),
        _kpi("exec", "Исполнение", len(in_exec), f"{overdue_stages} этапов просрочено · уточнённый срок сдвинут у {sum(1 for r in exec_rows if r['delay'] > 0)}",
             "/deals?block=won&flag=overdue" if overdue_stages else "/deals?block=won", "warn" if overdue_stages else "", source="этапы исполнения выигранных сделок"),
    ]

    # ---------------- требует вмешательства: одна очередь, без повторов (ключ — источник + правило)
    esc: dict[str, dict] = {}

    def add(key: str, **kw):
        if key not in esc:
            esc[key] = {"key": key, **kw}

    for r in exec_rows:
        d = r["deal"]
        cl = d.client.name if d.client else ""
        late = [s for s in r["stages"] if not s.actual_on and (s.refined_on or s.planned_on) and (s.refined_on or s.planned_on) < today]
        pay = [s for s in late if s.code in ("client_advance", "client_final")]
        for s in pay:
            days = (today - (s.refined_on or s.planned_on)).days
            add(f"deal:{d.id}:receivable", priority="critical" if days > 30 else "high", title=f"{s.title} · {cl}",
                reason=f"{d.number}: оплата клиента не отмечена, просрочка {days} дн.", impact={"amount": float(s.amount), "currency": s.currency} if fin and s.amount else None,
                owner=names.get(d.manager_id, ""), owner_role="менеджер", next_action="Связаться с клиентом, уточнить дату оплаты", url=f"/deals/{d.id}?tab=execution",
                due=(s.refined_on or s.planned_on).isoformat(), days=days, source="этап исполнения")
        other = [s for s in late if s.code not in ("client_advance", "client_final")]
        if other:
            first = min(other, key=lambda s: s.refined_on or s.planned_on)
            days = (today - (first.refined_on or first.planned_on)).days
            add(f"deal:{d.id}:stages_late", priority="high" if days > 14 or len(other) >= 3 else "normal", title=f"{d.number} · {cl}: {len(other)} этап(ов) без факта",
                reason=f"первый — «{first.title}», план {(first.refined_on or first.planned_on):%d.%m}, {days} дн. назад", impact=None,
                owner=names.get(d.manager_id, ""), owner_role="менеджер", next_action="Отметить факт или уточнить дату", url=f"/deals/{d.id}?tab=execution",
                due=(first.refined_on or first.planned_on).isoformat(), days=days, source="этапы исполнения")
    for r in rec_rows:   # «Финансы»: просроченные поступления (в т. ч. по договорам вне этапов) — тот же ключ, что у этапа оплаты
        k = f"deal:{r['deal_id']}:receivable" if r.get("deal_id") else f"obligation:{r['obligation_id']}:receivable"
        add(k, priority="critical" if r["overdue_days"] > 30 else "high", title=f"{r['title']} · {r['client']}",
            reason=f"{r['deal_number']}: поступление не разнесено, просрочка {r['overdue_days']} дн.", impact={"amount": r["amount"], "currency": r["currency"]} if r["amount"] else None,
            owner=r.get("manager") or "ФД", owner_role="менеджер" if r.get("manager") else "финансы", next_action="Проверить оплату и связаться с клиентом",
            url=r["url"] or "/finance?tab=liquidity", due=r["on"].isoformat(), days=r["overdue_days"], source="«Финансы»")
    for a in approvals:
        since = a.get("waiting_since")
        try:
            waited = (today - datetime.fromisoformat(since).date()).days if since else 0
        except ValueError:
            waited = 0
        add(f"approval:{a['url']}:{a['kind']}", priority="high" if waited >= 2 else "normal", title=a["title"], reason=a["sub"] + (f" · ждёт {waited} дн." if waited else ""),
            impact=None, owner="вы", owner_role="решение ЗГД", next_action="Рассмотреть", url=a["url"], due=None, days=waited, source="согласование")
    for cl in claims:
        if cl.answer_due and cl.answer_due < today and cl.status in ("sent", "received"):
            days = (today - cl.answer_due).days
            add(f"claim:{cl.id}:answer", priority="high", title=f"Претензия {cl.number}: " + ("клиент не ответил" if cl.direction == "outgoing" else "наш ответ просрочен"),
                reason=f"срок ответа истёк {cl.answer_due:%d.%m}, {days} дн. назад", impact=None, owner="юрист", owner_role="юр. блок",
                next_action="Решить: суд, повторное требование или закрытие" if cl.direction == "outgoing" else "Ответить контрагенту",
                url=f"/legal?tab=claims&open={cl.id}", due=cl.answer_due.isoformat(), days=days, source="претензия")
    from ..services.office_watch import control
    try:
        ctl = control(db, today)
    except Exception:  # noqa: BLE001 — Канцелярия не ломает пульт
        ctl = {"items": []}
    for r in ctl.get("items", []):
        if r["overdue"]:
            add(f"office:{r['user_id'] or 'none'}:overdue", priority="high" if r["overdue"] >= 3 else "normal", title=f"{r['name']}: просрочено поручений по документам — {r['overdue']}",
                reason=", ".join(f"{x['number']} ({x['days']} р. дн.)" for x in r["late_docs"][:3]), impact=None, owner=r["name"], owner_role="исполнитель",
                next_action="Запросить статус", url="/office?t=control", due=None, days=max((x["days"] for x in r["late_docs"]), default=0), source="Канцелярия")
    if cin.get("no_scan"):
        add("contracts:no_scan", priority="normal", title=f"Подписанных договоров без скана: {len(cin['no_scan'])}", reason=", ".join(list(dict.fromkeys(x["number"] for x in cin["no_scan"]))[:4]),
            impact=None, owner="юрист", owner_role="юр. блок", next_action="Загрузить сканы", url="/legal?tab=contracts&flag=no_scan", due=None, days=0, source="реестр договоров")
    for x in cin.get("expiring", [])[:3]:
        add(f"contract:{x['number']}:expiring", priority="normal", title=f"Договор {x['number']} ({x['cp_name']}) истекает через {x['days']} дн.", reason="продлить или закрыть",
            impact=None, owner="юрист", owner_role="юр. блок", next_action="Решить о продлении", url="/legal?tab=contracts&flag=expiring", due=None, days=0, source="реестр договоров")
    if fin:
        from ..models.finance import FinanceObligation, FinanceTransaction
        rec = (db.scalar(select(func.count(FinanceTransaction.id)).where(FinanceTransaction.status == "needs_reconciliation", FinanceTransaction.withdrawn_at.is_(None))) or 0) \
            + (db.scalar(select(func.count(FinanceObligation.id)).where(FinanceObligation.sync_status == "conflict")) or 0)
        if rec:
            add("finance:reconciliation", priority="normal", title=f"Записей «требует сверки» в «Финансах»: {rec}", reason="отметки об оплате без банковского подтверждения и конфликты графиков",
                impact=None, owner="ФД", owner_role="финансы", next_action="Сверить с банком", url="/finance?tab=reconciliation", due=None, days=0, source="«Финансы»")
    escalations = sorted(esc.values(), key=lambda e: (PRIO[e["priority"]], -(e["days"] or 0)))

    # ---------------- направления — по ролям сотрудников (оргструктуры «отдел → руководитель» в системе нет)
    users = db.scalars(select(User).where(User.is_active.is_(True))).all()

    def people(*roles: str) -> list[str]:
        return [u.display_name for u in users if set(u.roles or []) & set(roles)]

    def late_by(codes: tuple) -> list[ExecutionStage]:
        return [s for r in exec_rows for s in r["stages"] if s.code in codes and not s.actual_on and (s.refined_on or s.planned_on) and (s.refined_on or s.planned_on) < today]

    def tone(bad: int, warn: int = 0) -> str:
        return "bad" if bad else "warn" if warn else "ok"

    kp_late = [d for d in open_deals if d.kp_due and d.kp_due < today and d.status not in (DealStatus.KP_SENT,)]
    stale = [d for d in open_deals if d.status == DealStatus.DRAFT and d.updated_at and (today - d.updated_at.date()).days >= 7]
    lg, py, fc, sv = late_by(LOGISTICS), late_by(PAYMENTS), late_by(FACTORY), late_by(SERVICE)
    try:
        wc_open = db.scalar(select(func.count(WarrantyClaim.id)).where(WarrantyClaim.status.not_in(("closed",)))) or 0
    except Exception:  # noqa: BLE001
        wc_open = None
    lg_ = legal or {}
    directions = [
        {"key": "sales", "title": "Коммерция", "people": people("manager"), "tone": tone(len(kp_late), len(stale)),
         "lines": [f"в работе: {len(open_deals)} ГШО · {len(open_parts)} ЗЧ", f"КП с прошедшим сроком: {len(kp_late)}", f"черновики без движения 7+ дн.: {len(stale)}"], "url": "/deals?block=work"},
        {"key": "factory", "title": "Завод и закупки", "people": people("purchasing", "manager"), "tone": tone(len(fc)),
         "lines": [f"этапов без факта: {len(fc)}" + (f" (первый — {min(fc, key=lambda s: s.refined_on or s.planned_on).title})" if fc else ""), f"заявок на запчасти в работе: {len(open_parts)}"],
         "url": "/deals?block=won&flag=overdue" if fc else "/supply"},
        {"key": "logistics", "title": "Логистика", "people": people("logistician"), "tone": tone(sum(1 for s in lg if (today - (s.refined_on or s.planned_on)).days > 7), len(lg)),
         "lines": [f"этапов без факта: {len(lg)}", "готовность, отгрузка, граница, ДТ, доставка"], "url": "/deals?block=won&flag=overdue" if lg else "/summary?kind=shipments"},
        {"key": "finance", "title": "Финансы", "people": people("fd"), "tone": tone(sum(1 for s in py if s.code.startswith("client")), len(py)),
         "lines": [f"платежей без факта: {len(py)}", f"просроченная дебиторка: {money_by_currency([(r['amount'], r['currency']) for r in rec_rows]) if fin else 'нет доступа'}"],
         "url": "/finance?tab=liquidity" if fin else "/deals?block=won"},
        {"key": "legal", "title": "Юридический блок", "people": people("lawyer"), "tone": tone(lg_.get("claims_answer_overdue", 0), lg_.get("contracts_no_scan", 0) + lg_.get("contracts_review", 0)),
         "lines": [f"договоры без скана: {lg_.get('contracts_no_scan', 0)} · на проверке: {lg_.get('contracts_review', 0)}", f"претензии: открыто {lg_.get('claims_open', 0)}, срок ответа истёк {lg_.get('claims_answer_overdue', 0)}"],
         "url": "/legal"},
        {"key": "service", "title": "Сервис", "people": people("service_head"), "tone": tone(len(sv), wc_open or 0),
         "lines": [f"ПНР и установка опций без факта: {len(sv)}", f"гарантийные претензии в работе: {wc_open if wc_open is not None else 'нет данных'}"], "url": "/supply?tab=claims"},
    ]

    # ---------------- поручения — резолюции Канцелярии с контролем исполнения (управленческих поручений с приёмкой в системе пока нет)
    o_open = sum(r["open"] for r in ctl.get("items", []))
    o_over = sum(r["overdue"] for r in ctl.get("items", []))
    o_today = sum(r["today"] for r in ctl.get("items", []))
    nearest = db.scalars(select(OfficeDoc).where(OfficeDoc.status.in_(("assigned", "in_work", "review")), OfficeDoc.due_on.is_not(None)).order_by(OfficeDoc.due_on).limit(3)).all()
    assignments = {"open": o_open, "overdue": o_over, "today": o_today,
                   "items": [{"number": x.number, "subject": x.subject, "executor": names.get(x.reviewer_id if x.status == "review" else x.executor_id, "") or "не назначен",
                              "due_on": x.due_on.isoformat(), "overdue": x.due_on < today, "url": f"/office?doc={x.id}"} for x in nearest],
                   "url": "/office?t=control", "create_url": "/office", "source": "резолюции Канцелярии"}

    return {"meta": {"generated_at": now, "today": today.isoformat(), "period": period, "timezone": "Europe/Moscow"},
            "kpis": kpis, "escalations": escalations, "directions": directions, "assignments": assignments}
