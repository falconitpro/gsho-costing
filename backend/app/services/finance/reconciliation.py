"""«Сверка и уточнения» — очереди проблем (docs/finance/01, раздел 11; 04 — GET /api/finance/reconciliation; 05, раздел 13; D-142).

Только чтение: каждая строка — что не совпадает, где это поправить и кто ответственный. Очереди:
  unallocated       — проведённые операции без разнесения или с частичным;
  manual_marks      — старые отметки «оплачено» без банковского подтверждения (needs_reconciliation);
  provisional       — временные разнесения, ждут подтверждения;
  source_conflict   — источник изменился/исчез, а по обязательству уже есть разнесения (conflict | orphaned);
  duplicates        — отметка без банка с тем же номером документа, что и проведённая операция того же счёта;
  no_date           — активные обязательства без юридического и прогнозного срока;
  stale_balance     — счёт без подтверждённого остатка или остаток старше 7 дней;
  pnl_vs_cash       — информационно: деньги по сделке поступили, а выручка не признана;
  questions         — открытые и отвеченные уточнения.
Видимость — как в реестре и операциях. Суммы экономики (признание) — только при праве «Себестоимость».
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.roles import READ
from ...models import Deal, FinanceAccount, FinanceAllocation, FinanceObligation, FinanceQuestion, FinanceRecognitionEvent, FinanceTransaction
from . import accounts as acc
from .money import ZERO, ru, s
from .transactions import allocated

QUEUES = {"unallocated": "Операции без разнесения", "manual_marks": "Отметки без банка", "provisional": "Временные разнесения",
          "source_conflict": "Источник изменился", "duplicates": "Возможные дубли", "no_date": "Без даты", "stale_balance": "Устаревшие остатки",
          "pnl_vs_cash": "Признание и деньги", "questions": "Уточнения"}
SEVERITY = {"high": "важно", "medium": "проверить", "info": "для сведения"}
STALE_DAYS = 7
ORIGIN = {"contract_legacy": "в договоре", "execution_legacy": "в этапе исполнения", "planfact_legacy": "в план/факте"}


def _tx_ref(t: FinanceTransaction) -> dict:
    return {"type": "transaction", "id": t.id, "url": f"/finance?tab=liquidity&tx={t.id}", "title": f"{'поступление' if t.direction == 'in' else 'списание'} {t.booked_at.date():%d.%m.%Y}"
            + (f" · {t.external_ref}" if t.external_ref else "")}


def _ob_ref(o: FinanceObligation) -> dict:
    return {"type": "obligation", "id": o.id, "url": f"/finance?tab=calendar&open={o.id}", "title": o.title}


def build(db: Session, user, vis, today: Optional[date] = None, queue: Optional[str] = None, deal_id: Optional[str] = None) -> dict:
    today = today or date.today()
    items: list[dict] = []
    want = (lambda q: True)   # очереди считаются все — счётчики вкладок полные; фильтр по очереди — в конце
    deal_nums: dict[str, str] = {}

    def deal_ref(did: Optional[str]) -> Optional[dict]:
        if not did:
            return None
        if did not in deal_nums:
            d = db.get(Deal, did)
            deal_nums[did] = d.number if d else ""
        return {"id": did, "number": deal_nums[did], "url": f"/deals/{did}"}

    # --- операции
    from .fx import leg_ids
    txs = db.scalars(select(FinanceTransaction).where(FinanceTransaction.status.in_(("posted", "needs_reconciliation")), FinanceTransaction.origin_type != "reversal",
                                                      FinanceTransaction.withdrawn_at.is_(None))).all()
    legs = leg_ids(db, [t.id for t in txs])
    txs = [t for t in txs if t.id not in legs]
    used = allocated(db, [t.id for t in txs])
    alloc_obs: dict[str, list[str]] = defaultdict(list)
    for tid, oid in db.execute(select(FinanceAllocation.transaction_id, FinanceAllocation.obligation_id).where(
            FinanceAllocation.transaction_id.in_([t.id for t in txs]), FinanceAllocation.status.in_(("provisional", "confirmed")))).all() if txs else []:
        alloc_obs[tid].append(oid)
    obs_cache: dict[str, Optional[FinanceObligation]] = {}

    def ob(oid: str) -> Optional[FinanceObligation]:
        if oid not in obs_cache:
            obs_cache[oid] = db.get(FinanceObligation, oid)
        return obs_cache[oid]

    def tx_deal(t: FinanceTransaction) -> Optional[str]:
        for oid in alloc_obs.get(t.id, []):
            o = ob(oid)
            if o is not None and o.deal_id:
                return o.deal_id
        return None

    posted_refs = {(t.account_id, t.external_ref) for t in txs if t.status == "posted" and t.external_ref}
    for t in txs:
        if deal_id and tx_deal(t) != deal_id:
            continue
        left = t.amount - used[t.id]
        if t.status == "posted" and left > 0 and want("unallocated"):
            part = used[t.id] > 0
            items.append({"queue": "unallocated", "severity": "medium", "object": _tx_ref(t), "deal": deal_ref(tx_deal(t)),
                          "problem": ("частично разнесена: " if part else "не разнесена: ") + f"остаток {ru(left, t.currency)} не привязан к обязательству",
                          "amount": s(left), "currency": t.currency, "on": t.booked_at.date().isoformat(), "counterparty": t.counterparty_name,
                          "action": {"title": "Разнести", "url": f"/finance?tab=liquidity&tx={t.id}"}})
        if t.status == "needs_reconciliation":
            if want("manual_marks"):
                items.append({"queue": "manual_marks", "severity": "high", "object": _tx_ref(t), "deal": deal_ref(tx_deal(t)),
                              "problem": f"отметка «оплачено» ({ORIGIN.get(t.origin_type, t.origin_type)}) без банковского подтверждения — в остатки и оплату не входит",
                              "amount": s(t.amount), "currency": t.currency, "on": t.booked_at.date().isoformat(), "counterparty": t.counterparty_name,
                              "action": {"title": "Подтвердить по банку", "url": f"/finance?tab=liquidity&tx={t.id}"}})
            if want("duplicates") and t.external_ref and (t.account_id, t.external_ref) in posted_refs:
                items.append({"queue": "duplicates", "severity": "high", "object": _tx_ref(t), "deal": deal_ref(tx_deal(t)),
                              "problem": f"номер документа {t.external_ref} уже есть у проведённой операции этого счёта — вероятно, это та же оплата",
                              "amount": s(t.amount), "currency": t.currency, "on": t.booked_at.date().isoformat(), "counterparty": t.counterparty_name,
                              "action": {"title": "Отозвать отметку или связать", "url": f"/finance?tab=liquidity&tx={t.id}"}})

    # --- обязательства
    if any(want(q) for q in ("provisional", "source_conflict", "no_date")):
        oq = select(FinanceObligation)
        if deal_id:
            oq = oq.where(FinanceObligation.deal_id == deal_id)
        obs = [o for o in db.scalars(oq).all() if vis.obligation(o)]
        prov = defaultdict(lambda: ZERO)
        if obs:
            for oid, total in db.execute(select(FinanceAllocation.obligation_id, func.sum(FinanceAllocation.amount_obligation)).where(
                    FinanceAllocation.obligation_id.in_([o.id for o in obs]), FinanceAllocation.status == "provisional").group_by(FinanceAllocation.obligation_id)).all():
                prov[oid] = Decimal(total)
        for o in obs:
            if want("provisional") and prov[o.id] > 0:
                items.append({"queue": "provisional", "severity": "medium", "object": _ob_ref(o), "deal": deal_ref(o.deal_id),
                              "problem": f"временно разнесено {ru(prov[o.id], o.currency)} — в оплату не входит до подтверждения",
                              "amount": s(prov[o.id]), "currency": o.currency, "on": (o.legal_due_on or o.forecast_on).isoformat() if (o.legal_due_on or o.forecast_on) else None,
                              "counterparty": o.counterparty_name_snapshot, "action": {"title": "Подтвердить разнесение", "url": f"/finance?tab=calendar&open={o.id}"}})
            if want("source_conflict") and o.sync_status in ("conflict", "orphaned"):
                items.append({"queue": "source_conflict", "severity": "high", "object": _ob_ref(o), "deal": deal_ref(o.deal_id),
                              "problem": ("источник изменился после разнесения" if o.sync_status == "conflict" else "строка исчезла из источника, а деньги по ней разнесены")
                              + (f": {o.excluded_reason}" if o.excluded_reason else ""),
                              "amount": s(o.amount), "currency": o.currency, "on": None, "counterparty": o.counterparty_name_snapshot,
                              "action": {"title": "Перенести разнесения", "url": f"/finance?tab=calendar&open={o.id}"}})
            if want("no_date") and o.is_active and not o.legal_due_on and not o.forecast_on:
                items.append({"queue": "no_date", "severity": "medium", "object": _ob_ref(o), "deal": deal_ref(o.deal_id),
                              "problem": "нет ни срока по договору, ни ожидаемой даты — не попадает в прогноз остатков" + (f" ({o.legal_due_text})" if o.legal_due_text else ""),
                              "amount": s(o.amount), "currency": o.currency, "on": None, "counterparty": o.counterparty_name_snapshot,
                              "action": {"title": "Открыть источник", "url": f"/finance?tab=calendar&open={o.id}"}})

    # --- счета
    if want("stale_balance") and not deal_id:
        now = datetime.now(timezone.utc)
        for a in db.scalars(select(FinanceAccount).where(FinanceAccount.status == "active")).all():
            b = acc.balance(db, a)
            snap = b["snapshot"]
            age = (now - datetime.fromisoformat(snap["as_of"])).days if snap else None
            if snap is None or age > STALE_DAYS:
                items.append({"queue": "stale_balance", "severity": "high" if snap is None else "medium",
                              "object": {"type": "account", "id": a.id, "url": f"/finance?tab=liquidity&account={a.id}", "title": a.title},
                              "deal": None, "problem": "остаток не подтверждён — прогноз по валюте неизвестен" if snap is None else f"последний подтверждённый остаток — {age} дн. назад",
                              "amount": None, "currency": a.currency, "on": snap["as_of"][:10] if snap else None, "counterparty": "",
                              "action": {"title": "Подтвердить остаток", "url": f"/finance?tab=liquidity&account={a.id}"}})

    # --- признание и деньги (информационно): поступления по сделке подтверждены, выручка не признана
    if want("pnl_vs_cash") and user.can("cost", READ):
        rows = db.execute(select(FinanceObligation.deal_id, FinanceTransaction.currency, func.sum(FinanceAllocation.amount_tx))
                          .join(FinanceAllocation, FinanceAllocation.obligation_id == FinanceObligation.id)
                          .join(FinanceTransaction, FinanceTransaction.id == FinanceAllocation.transaction_id)
                          .where(FinanceAllocation.status == "confirmed", FinanceTransaction.status == "posted", FinanceTransaction.direction == "in",
                                 FinanceObligation.deal_id.is_not(None), *( [FinanceObligation.deal_id == deal_id] if deal_id else []))
                          .group_by(FinanceObligation.deal_id, FinanceTransaction.currency)).all()
        recognized = {did for (did,) in db.execute(select(FinanceRecognitionEvent.deal_id).where(FinanceRecognitionEvent.kind == "revenue",
                                                                                               FinanceRecognitionEvent.status == "posted")).all()}
        for did, cur, total in rows:
            if did in recognized or not vis.deal(did):
                continue
            items.append({"queue": "pnl_vs_cash", "severity": "info", "object": {"type": "deal_metric", "id": f"{did}:revenue", "url": f"/finance?tab=economy&deal={did}", "title": "выручка сделки"},
                          "deal": deal_ref(did), "problem": f"поступило {ru(Decimal(total), cur)}, выручка не признана — это может быть аванс; ошибкой само по себе не является",
                          "amount": s(Decimal(total)), "currency": cur, "on": None, "counterparty": "", "action": {"title": "Экономика сделки", "url": f"/finance?tab=economy&deal={did}"}})

    # --- уточнения
    qcount = 0
    if want("questions"):
        qq = select(FinanceQuestion).where(FinanceQuestion.status.in_(("open", "answered", "rejected")))
        if deal_id:
            qq = qq.where(FinanceQuestion.deal_id == deal_id)
        from .questions import STATUS, _name
        for q in db.scalars(qq.order_by(FinanceQuestion.due_on.nulls_last())).all():
            if q.deal_id and not vis.deal(q.deal_id):
                continue
            qcount += 1
            late = bool(q.due_on and q.due_on < today and q.status in ("open", "rejected"))
            items.append({"queue": "questions", "severity": "high" if late or q.status == "answered" else "medium",
                          "object": {"type": "question", "id": q.id, "url": f"/finance?tab=reconciliation&question={q.id}", "title": (q.context_snapshot or {}).get("title", "")},
                          "deal": deal_ref(q.deal_id), "problem": f"{STATUS[q.status]}" + (" · срок прошёл" if late else "") + f": {q.question[:160]}",
                          "amount": None, "currency": "", "on": q.due_on.isoformat() if q.due_on else None, "counterparty": "",
                          "responsible": _name(db, q.assignee_id), "action": {"title": "Проверить ответ" if q.status == "answered" else "Открыть", "url": f"/finance?tab=reconciliation&question={q.id}"}})

    # открытые вопросы по объекту — чтобы не задавать второй
    open_q = defaultdict(int)
    for st, sid in db.execute(select(FinanceQuestion.subject_type, FinanceQuestion.subject_id).where(FinanceQuestion.status.in_(("open", "answered", "rejected")))).all():
        open_q[(st, sid)] += 1
    for it in items:
        it["open_questions"] = open_q.get((it["object"]["type"], it["object"]["id"]), 0)
        it["can_ask"] = it["object"]["type"] in ("obligation", "transaction", "account", "deal_metric")
        it.setdefault("responsible", "")
    order = {"high": 0, "medium": 1, "info": 2}
    items.sort(key=lambda x: (order[x["severity"]], list(QUEUES).index(x["queue"]), x.get("on") or "9999"))
    counts = defaultdict(int)
    for it in items:
        counts[it["queue"]] += 1
    shown = [it for it in items if not queue or it["queue"] == queue]
    return {"as_of": today.isoformat(), "queues": [{"code": k, "title": t, "count": counts.get(k, 0)} for k, t in QUEUES.items()],
            "items": shown, "total": len(shown), "severity_titles": SEVERITY}
