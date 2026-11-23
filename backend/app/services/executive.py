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
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.roles import Role
from ..core.workflow import STATUS_TITLES as STATUS_NAMES, DealStatus

LOGISTICS = ("readiness", "shipment", "border", "declaration", "delivery")
PAYMENTS = ("client_advance", "client_final", "factory_advance", "factory_final", "lc_opened")
FACTORY = ("production", "factory_preacceptance", "local_option_order")
SERVICE = ("commissioning", "local_option_install")
PRIO = {"critical": 0, "high": 1, "normal": 2, "info": 3}
STALE_BANK_DAYS = 3
METHOD = {'active': 'Сделки ГШО в работе (статусы до выигрыша) и открытые заявки на запчасти. Сумма — цена КП без НДС в рублях из расчёта. Исключены: черновики в корзине, проигранные, закрытые.',
          'risk': 'Выигранные сделки, у которых хотя бы один этап исполнения без отметки факта, а уточнённая (если есть) или плановая дата уже прошла. Сумма — цена сделки без НДС.',
          'won': 'Сделки со статусом «выиграна» и дальше, дата контракта — в текущем квартале; плюс выигранные заявки на запчасти. Сравнения с планом нет: план продаж не задан.',
          'margin': 'Средневзвешенная по цене маржа базового сценария утверждённой версии расчёта по выигранному предложению. Сделки без утверждённого расчёта не учитываются — покрытие указано в подписи.',
          'receivable': 'Обязательства «нам платят» в «Финансах» с прошедшим сроком и ненулевым остатком к оплате. Суммы — в валюте обязательства, разные валюты не складываются; рубли — справочно по курсу ЦБ на сегодня. «Устарело» — банковские операции не загружались больше 3 рабочих дней.',
          'exec': 'Этапы исполнения выигранных сделок без отметки факта, у которых уточнённая или плановая дата прошла. Отдельно: сколько сделок имеют такие этапы и у скольких уточнённый срок окончания позже планового.'}        # D-155: рабочих дней без загрузки банковских операций — дебиторка «устарело»


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


QUALITY = ("confirmed", "partial", "stale", "unavailable", "unverified")


def rub_reference(db: Session, items: list[tuple[Optional[float], str]], today: date) -> Optional[dict]:
    """Справочно: сумма в рублях по курсу ЦБ на сегодня (последний курс не позже сегодняшней даты). Решение владельца 23.11.2026:
    дебиторка — в валюте обязательства, рубли — только справочно, с курсом и датой. Нет курса по какой-то валюте — справки нет."""
    from ..models import FxRate
    acc: dict[str, float] = defaultdict(float)
    for amount, ccy in items:
        if amount:
            acc[(ccy or "RUB").upper()] += float(amount)
    if not acc or set(acc) == {"RUB"}:
        return None
    total, rates = 0.0, []
    for ccy, v in acc.items():
        if ccy == "RUB":
            total += v
            continue
        r = db.scalars(select(FxRate).where(FxRate.currency == ccy, FxRate.on <= today).order_by(FxRate.on.desc(), FxRate.source).limit(1)).first()
        if r is None:
            return None
        total += v * float(r.rate)
        rates.append({"currency": ccy, "rate": round(float(r.rate), 4), "on": r.on.isoformat(), "source": r.source})
    on = min(x["on"] for x in rates)
    return {"amount": round(total, 2), "text": f"≈ {_money(total)} по курсу ЦБ на {on[8:10]}.{on[5:7]}",
            "rates": rates}


def _kpi(key: str, title: str, value: Any, sub: str, url: str, tone: str = "", quality: str = "confirmed", period: str = "", source: str = "",
         unit: str = "", currency: str = "", note: str = "", calculated_at: str = "", drill: bool = False, extra: Optional[list] = None,
         method: str = "", ref: Optional[dict] = None) -> dict:
    """KPI по ТЗ v2 §7: key, title, value, unit/currency, period, calculated_at, data_quality, drilldown. Нет данных — value=None и причина."""
    assert quality in QUALITY
    return {"key": key, "title": title, "value": value, "sub": sub, "url": url, "tone": tone, "data_quality": quality, "quality_note": note,
            "period": period, "source": source, "unit": unit, "currency": currency, "calculated_at": calculated_at,
            "drilldown": {"url": url, "list": key if drill else None}, "extra": extra or [], "method": method, "reference": ref}


def build(db: Session, user, *, today: date, names: dict, fin: bool, open_deals: list, open_parts: list, exec_rows: list, won_q: list, won_q_parts: list,
          approvals: list, money: Optional[dict], legal: Optional[dict], cin: dict, claims: list, poas: list, price, events: Optional[list] = None) -> dict:
    from ..models import ExecutionStage, User, WarrantyClaim
    from ..services.execution_service import won_variant
    now = datetime.now(timezone.utc).isoformat()
    q = (today.month - 1) // 3 + 1
    period = f"{q} квартал {today.year}"

    # ---------------- показатели (R0 сверка, D-154: маржа — из утверждённой версии расчёта; исполнение — этапы отдельно от сделок)
    from ..models import DealVersion
    can_cost = user.can("cost")
    in_work = sum(price(d) or 0 for d in open_deals) + sum(p["price_rub"] for p in open_parts)
    risk_rows = [r for r in exec_rows if r["overdue"]]
    risk_sum = sum(price(r["deal"]) or 0 for r in risk_rows)
    won_sum = sum(price(d) or 0 for d in won_q) + sum(p["price_rub"] for p in won_q_parts)
    margins, margin_rows = [], []
    for d in won_q:
        v = won_variant(d) if d.variants else None
        ver = db.get(DealVersion, d.approved_version_id) if d.approved_version_id else None
        res = ((ver.calc_result or {}).get("variants", {}).get(v.id, {}) if (ver and v) else {}).get("base", {}) or {}
        m, p = res.get("margin_pct"), price(d)
        why = "" if m is not None else ("нет утверждённой версии расчёта" if not ver else "в утверждённом расчёте нет маржи по выигранному предложению")
        if m is not None and p:
            margins.append((float(m), p))
        margin_rows.append({"title": f"{d.number} · {d.client.name if d.client else ''}", "url": f"/deals/{d.id}",
                            "value": f"{float(m):.1f} %".replace(".", ",") if m is not None and can_cost else None, "note": why or (f"версия {ver.version_no}" if ver else "")})
    m_quality = "unavailable" if not margins else ("partial" if len(margins) < len(won_q) else "confirmed")
    m_val = round(sum(m * p for m, p in margins) / sum(p for _, p in margins), 1) if margins else None
    m_note = ("выигранных сделок за квартал нет" if not won_q else f"утверждённый расчёт с маржой есть у {len(margins)} из {len(won_q)}") if can_cost else "нет права «себестоимость»"
    from ..services.finance.cashview import rows as fin_rows
    rec_rows = [r for r in fin_rows(db, user, today, today) if r["direction"] == "in" and r["overdue_days"] > 0] if fin else []
    in_exec = [r for r in exec_rows if r["deal"].status == DealStatus.EXECUTION]
    overdue_stages = sum(r["overdue"] for r in exec_rows)
    shifted = [r for r in exec_rows if r["delay"] > 0]
    late_stage_rows = sorted(({"title": f"{r['deal'].number} · {s.title}", "url": f"/deals/{r['deal'].id}?tab=execution",
                               "value": f"план {(s.refined_on or s.planned_on):%d.%m}", "note": f"факта нет, {(today - (s.refined_on or s.planned_on)).days} дн.",
                               "_d": s.refined_on or s.planned_on}
                              for r in exec_rows for s in r["stages"] if not s.actual_on and (s.refined_on or s.planned_on) and (s.refined_on or s.planned_on) < today),
                             key=lambda x: x["_d"])
    for x in late_stage_rows:
        x.pop("_d")
    rec_ccy = sorted({(r["currency"] or "RUB").upper() for r in rec_rows})
    # D-155: свежесть банковских данных — просрочка дебиторки верна, только если выписки загружены (оплата могла прийти, но не разнесена)
    rec_q, rec_note, bank_last = ("confirmed", "", None)
    if fin:
        from ..calc.calendar import DEFAULT_CALENDAR as CAL
        from ..models.finance import FinanceImportBatch, FinanceTransaction
        b1 = db.scalar(select(func.max(FinanceImportBatch.updated_at)).where(FinanceImportBatch.status == "confirmed"))
        b2 = db.scalar(select(func.max(FinanceTransaction.created_at)).where(FinanceTransaction.status.in_(("posted", "needs_reconciliation"))))
        bank_last = max([x for x in (b1, b2) if x], default=None)
        if bank_last is None:
            rec_q, rec_note = "unverified", "банковские операции в «Финансах» не загружались — оплата могла прийти"
        elif CAL.working_days_between(bank_last.date(), today) > STALE_BANK_DAYS:
            rec_q, rec_note = "stale", f"последняя банковская операция загружена {bank_last:%d.%m} — выписки устарели"
    kpis = [
        _kpi("active", "Активные сделки", len(open_deals) + len(open_parts), (_money(in_work) + " в работе · " if fin else "") + f"ГШО {len(open_deals)} · ЗЧ {len(open_parts)}",
             "/deals?block=work", source="реестр сделок: в работе", unit="сделок", currency="RUB" if fin else "", period="на сегодня", calculated_at=now),
        _kpi("risk", "Сделки под риском", len(risk_rows), (f"на {_money(risk_sum)} · " if fin and risk_sum else "") + "причина: этапы без факта после плановой даты",
             "/deals?block=won&flag=overdue", "bad" if risk_rows else "ok", source="этапы исполнения: просрочено (уточнённая или плановая дата)",
             unit="сделок", period="на сегодня", calculated_at=now, drill=True),
        _kpi("won", "Выиграно за квартал", len(won_q) + len(won_q_parts), (_money(won_sum) + " · " if fin else "") + "план продаж не задан — без % к плану",
             "/deals?block=won", "ok" if won_q or won_q_parts else "", period=period, source="выигранные сделки по дате контракта", unit="сделок",
             currency="RUB" if fin else "", calculated_at=now),
        _kpi("margin", "Маржа выигранных", f"{m_val:.1f} %".replace(".", ",") if m_val is not None and can_cost else None,
             (f"средневзвешенная по цене · {m_note}" if m_val is not None and can_cost else m_note),
             "/deals?block=won", quality=m_quality if can_cost else "unavailable", period=period, note=m_note,
             source="маржа базового сценария утверждённой версии расчёта по выигранному предложению", unit="%", calculated_at=now, drill=can_cost),
        _kpi("receivable", "Дебиторка просрочена", len(rec_rows) if fin else None, money_by_currency([(r["amount"], r["currency"]) for r in rec_rows]) if fin else "нет доступа к финансам",
             (money or {}).get("url") or "/finance?tab=liquidity", "bad" if rec_rows else "ok", quality=rec_q if fin else "unavailable",
             note=rec_note if fin else "нет права на финансы", unit="обязательств", currency=" · ".join(rec_ccy), period="на сегодня", calculated_at=now,
             source="«Финансы»: обязательства «нам платят» с прошедшим сроком, остаток к оплате; валюты не складываются", drill=fin),
        _kpi("exec", "Исполнение: просроченные этапы", overdue_stages, f"сделок в исполнении: {len(in_exec)}",
             "/deals?block=won&flag=overdue" if overdue_stages else "/deals?block=won", "warn" if overdue_stages else "", unit="этапов", period="на сегодня",
             calculated_at=now, drill=True, source="этапы исполнения выигранных сделок: факт не отмечен, дата прошла",
             extra=[{"label": "сделок с просроченными этапами", "value": len(risk_rows)},
                    {"label": "сделок со сдвигом уточнённого срока окончания", "value": len(shifted)}]),
    ]
    for k in kpis:
        k["method"] = METHOD.get(k["key"], "")
    rub_ref = rub_reference(db, [(r["amount"], r["currency"]) for r in rec_rows], today) if fin else None
    if rub_ref:
        kpis[4]["reference"] = rub_ref
    open_list = [{"title": f"{d.number} · {d.client.name if d.client else ''}", "url": f"/deals/{d.id}", "value": _money(price(d)) if fin and price(d) else None,
                  "note": STATUS_NAMES.get(d.status, "")} for d in open_deals] + \
                [{"title": f"{p.get('number', '')} · {p.get('client_name', '')}", "url": p.get("url") or "/deals?block=work", "value": _money(p["price_rub"]) if fin and p.get("price_rub") else None,
                  "note": "заявка на запчасти"} for p in open_parts]
    won_list = [{"title": f"{d.number} · {d.client.name if d.client else ''}", "url": f"/deals/{d.id}", "value": _money(price(d)) if fin and price(d) else None,
                 "note": STATUS_NAMES.get(d.status, "")} for d in won_q] + \
               [{"title": f"{p.get('number', '')} · {p.get('client_name', '')}", "url": p.get("url") or "/deals?block=won", "value": _money(p["price_rub"]) if fin and p.get("price_rub") else None,
                 "note": "заявка на запчасти"} for p in won_q_parts]
    kpis[0]["drilldown"]["list"], kpis[2]["drilldown"]["list"] = "active", "won"
    rec_list = [{"title": f"{r['title']} · {r['client']}", "url": r["url"] or "/finance?tab=liquidity", "value": _money(r["amount"], r["currency"]),
                 "note": f"{r['deal_number']}, срок {r['on']:%d.%m}, просрочка {r['overdue_days']} дн."} for r in sorted(rec_rows, key=lambda r: -r["overdue_days"])]
    drill = {
        "active": {"title": "Активные сделки", "rows": open_list},
        "won": {"title": f"Выиграно за {period}", "rows": won_list},
        "risk": {"title": "Сделки под риском", "rows": [{"title": f"{r['deal'].number} · {r['deal'].client.name if r['deal'].client else ''}", "url": f"/deals/{r['deal'].id}?tab=execution",
                                                       "value": f"{r['overdue']} этап(ов)", "note": (_money(price(r['deal'])) if fin and price(r['deal']) else "")} for r in risk_rows]},
        "exec": {"title": "Просроченные этапы исполнения", "rows": late_stage_rows},
        "receivable": {"title": "Просроченная дебиторка", "rows": rec_list, "footer": (rub_ref or {}).get("text", "")
                       + (" (" + ", ".join(f"{x['currency']} {x['rate']:.4f}".replace(".", ",") for x in rub_ref["rates"]) + ")" if rub_ref else "")} if fin else None,
        "margin": {"title": "Маржа выигранных за квартал", "rows": margin_rows} if can_cost else None,
    }

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
            add(f"deal:{d.id}:receivable", obj=("сделка", d.number), dir="sales", priority="critical" if days > 30 else "high", title=f"{s.title} · {cl}",
                reason=f"{d.number}: оплата клиента не отмечена, просрочка {days} дн.", impact={"amount": float(s.amount), "currency": s.currency} if fin and s.amount else None,
                owner=names.get(d.manager_id, ""), owner_role="менеджер", next_action="Связаться с клиентом, уточнить дату оплаты", url=f"/deals/{d.id}?tab=execution",
                due=(s.refined_on or s.planned_on).isoformat(), days=days, source="этап исполнения")
        other = [s for s in late if s.code not in ("client_advance", "client_final")]
        if other:
            first = min(other, key=lambda s: s.refined_on or s.planned_on)
            days = (today - (first.refined_on or first.planned_on)).days
            add(f"deal:{d.id}:stages_late", obj=("сделка", d.number), dir="sales", priority="high" if days > 14 or len(other) >= 3 else "normal", title=f"{d.number} · {cl}: {len(other)} этап(ов) без факта",
                reason=f"первый — «{first.title}», план {(first.refined_on or first.planned_on):%d.%m}, {days} дн. назад", impact=None,
                owner=names.get(d.manager_id, ""), owner_role="менеджер", next_action="Отметить факт или уточнить дату", url=f"/deals/{d.id}?tab=execution",
                due=(first.refined_on or first.planned_on).isoformat(), days=days, source="этапы исполнения")
    for r in rec_rows:   # «Финансы»: просроченные поступления (в т. ч. по договорам вне этапов) — тот же ключ, что у этапа оплаты
        k = f"deal:{r['deal_id']}:receivable" if r.get("deal_id") else f"obligation:{r['obligation_id']}:receivable"
        add(k, obj=("обязательство", r["deal_number"]), dir="sales" if r.get("manager") else "finance", priority="critical" if r["overdue_days"] > 30 else "high", title=f"{r['title']} · {r['client']}",
            reason=f"{r['deal_number']}: поступление не разнесено, просрочка {r['overdue_days']} дн.", impact={"amount": r["amount"], "currency": r["currency"]} if r["amount"] else None,
            owner=r.get("manager") or "ФД", owner_role="менеджер" if r.get("manager") else "финансы", next_action="Проверить оплату и связаться с клиентом",
            url=r["url"] or "/finance?tab=liquidity", due=r["on"].isoformat(), days=r["overdue_days"], source="«Финансы»")
    for a in approvals:
        since = a.get("waiting_since")
        try:
            waited = (today - datetime.fromisoformat(since).date()).days if since else 0
        except ValueError:
            waited = 0
        add(f"approval:{a['url']}:{a['kind']}", obj=("согласование", ""), dir="", priority="high" if waited >= 2 else "normal", title=a["title"], reason=a["sub"] + (f" · ждёт {waited} дн." if waited else ""),
            impact=None, owner="вы", owner_role="решение ЗГД", next_action="Рассмотреть", url=a["url"], due=None, days=waited, source="согласование")
    for cl in claims:
        if cl.answer_due and cl.answer_due < today and cl.status in ("sent", "received"):
            days = (today - cl.answer_due).days
            add(f"claim:{cl.id}:answer", obj=("претензия", cl.number), dir="legal", priority="high", title=f"Претензия {cl.number}: " + ("клиент не ответил" if cl.direction == "outgoing" else "наш ответ просрочен"),
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
            add(f"office:{r['user_id'] or 'none'}:overdue", obj=("документы Канцелярии", ""), dir="", owner_id=r["user_id"], priority="high" if r["overdue"] >= 3 else "normal", title=f"{r['name']}: просрочено поручений по документам — {r['overdue']}",
                reason=", ".join(f"{x['number']} ({x['days']} р. дн.)" for x in r["late_docs"][:3]), impact=None, owner=r["name"], owner_role="исполнитель",
                next_action="Запросить статус", url="/office?t=control", due=None, days=max((x["days"] for x in r["late_docs"]), default=0), source="Канцелярия")
    if cin.get("no_scan"):
        add("contracts:no_scan", obj=("договоры", ""), dir="legal", priority="info", title=f"Подписанных договоров без скана: {len(cin['no_scan'])}", reason=", ".join(list(dict.fromkeys(x["number"] for x in cin["no_scan"]))[:4]),
            impact=None, owner="юрист", owner_role="юр. блок", next_action="Загрузить сканы", url="/legal?tab=contracts&flag=no_scan", due=None, days=0, source="реестр договоров")
    for x in cin.get("expiring", [])[:3]:
        add(f"contract:{x['number']}:expiring", obj=("договор", x["number"]), dir="legal", priority="normal", title=f"Договор {x['number']} ({x['cp_name']}) истекает через {x['days']} дн.", reason="продлить или закрыть",
            impact=None, owner="юрист", owner_role="юр. блок", next_action="Решить о продлении", url="/legal?tab=contracts&flag=expiring", due=None, days=0, source="реестр договоров")
    if fin:
        from ..models.finance import FinanceObligation, FinanceTransaction
        rec = (db.scalar(select(func.count(FinanceTransaction.id)).where(FinanceTransaction.status == "needs_reconciliation", FinanceTransaction.withdrawn_at.is_(None))) or 0) \
            + (db.scalar(select(func.count(FinanceObligation.id)).where(FinanceObligation.sync_status == "conflict")) or 0)
        if rec:
            add("finance:reconciliation", obj=("банковские операции", ""), dir="finance", priority="info", title=f"Записей «требует сверки» в «Финансах»: {rec}", reason="отметки об оплате без банковского подтверждения и конфликты графиков",
                impact=None, owner="ФД", owner_role="финансы", next_action="Сверить с банком", url="/finance?tab=reconciliation", due=None, days=0, source="«Финансы»")
    # ответственность: руководитель направления (если назначен и это не сам ответственный); срок реакции; поручение по сигналу
    from ..models import Assignment
    from ..services import assignments as asg
    hd = asg.heads(db)
    by_name = {v: k for k, v in names.items()}
    open_asg = {a.source_key: a for a in db.scalars(select(Assignment).where(Assignment.source_key != "", Assignment.status.in_(asg.OPEN))).all()}
    users_all = {u.id: u for u in db.scalars(select(User)).all()}
    for e in esc.values():
        otype, onum = e.pop("obj")
        e["object_type"], e["object_number"] = otype, onum
        dk = e.pop("dir")
        oid = e.pop("owner_id", None) or by_name.get(e.get("owner") or "")
        if not dk and oid and oid in users_all:
            dk = asg.direction_of(users_all[oid])
        h = hd.get(dk) if dk else None
        e["direction"] = asg.DIRECTION_TITLES.get(dk, "")
        e["head"] = names.get(h, "") if h and h != oid else ""
        e["owner_id"] = oid
        e["react_by"] = today.isoformat() if e["priority"] in ("critical", "high") else (e["due"] or None)
        e["group"] = ":".join(e["key"].split(":")[:2]) if e["key"].startswith("deal:") else e["key"]
        im = e.get("impact")
        if im and (im.get("currency") or "RUB").upper() != "RUB":
            ref = rub_reference(db, [(im["amount"], im["currency"])], today)
            im["rub_ref"] = ref["text"] if ref else ""
        a = open_asg.get(e["key"])
        e["assignment"] = {"number": a.number, "status": asg.STATUS_TITLES[a.status], "url": f"/assignments?open={a.id}"} if a else None
    escalations = sorted(esc.values(), key=lambda e: (PRIO[e["priority"]], e["react_by"] or "9999", -(e["days"] or 0)))
    if fin and can_cost:     # журнал сигналов — только по полной очереди (без прав часть сигналов не видна, D-155)
        from ..services import signals
        signals.sync(db, escalations)

    # ---------------- направления — руководитель из «Направлений» (назначает ЗГД, D-154); сотрудники — по ролям
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
    rec_txt = money_by_currency([(r['amount'], r['currency']) for r in rec_rows]) if fin else "нет доступа"
    D = {
        "sales": (people("cd", "manager"), tone(len(kp_late), len(stale)), [f"в работе: {len(open_deals)} ГШО · {len(open_parts)} ЗЧ", f"выиграно за квартал: {len(won_q) + len(won_q_parts)}"],
                  [x for x in (f"КП с прошедшим сроком: {len(kp_late)}" if kp_late else "", f"черновики без движения 7+ дн.: {len(stale)}" if stale else "") if x], "/deals?block=work"),
        "factory": (people("purchasing"), tone(len(fc)), [f"заявок на запчасти в работе: {len(open_parts)}"],
                    [f"этапов завода без факта: {len(fc)} (первый — {min(fc, key=lambda s: s.refined_on or s.planned_on).title})"] if fc else [], "/deals?block=won&flag=overdue" if fc else "/supply"),
        "logistics": (people("logistician"), tone(sum(1 for s in lg if (today - (s.refined_on or s.planned_on)).days > 7), len(lg)), ["готовность, отгрузка, граница, ДТ, доставка"],
                      [f"этапов логистики без факта: {len(lg)}"] if lg else [], "/deals?block=won&flag=overdue" if lg else "/summary?kind=shipments"),
        "finance": (people("fd"), tone(sum(1 for s in py if s.code.startswith("client")) + len(rec_rows), len(py)), [f"сделок в исполнении: {len(in_exec)}"],
                    [x for x in (f"платежей без факта: {len(py)}" if py else "", f"просроченная дебиторка: {rec_txt}" if rec_rows else "") if x], "/finance?tab=liquidity" if fin else "/deals?block=won"),
        "legal": (people("lawyer"), tone(lg_.get("claims_answer_overdue", 0), lg_.get("contracts_no_scan", 0) + lg_.get("contracts_review", 0)),
                  [f"на проверке и подписи: {lg_.get('contracts_review', 0)}", f"претензии открыты: {lg_.get('claims_open', 0)}"],
                  [x for x in (f"договоры без скана: {lg_.get('contracts_no_scan', 0)}" if lg_.get("contracts_no_scan") else "",
                               f"срок ответа на претензию истёк: {lg_.get('claims_answer_overdue', 0)}" if lg_.get("claims_answer_overdue") else "") if x], "/legal"),
        "service": (people("service_head"), tone(len(sv), wc_open or 0), [f"гарантийные претензии в работе: {wc_open if wc_open is not None else 'нет данных'}"],
                    [f"ПНР и установка опций без факта: {len(sv)}"] if sv else [], "/supply?tab=claims"),
    }
    from ..services import assignments as asg
    hd = asg.heads(db)
    users_by_id = {u.id: u for u in users}
    deal_of = {s.id: r["deal"] for r in exec_rows for s in r["stages"]}

    def st_rows(stages) -> list[dict]:
        return [{"kind": "этап", "title": f"{deal_of[s.id].number} · {s.title}", "url": f"/deals/{deal_of[s.id].id}?tab=execution",
                 "note": f"план {(s.refined_on or s.planned_on):%d.%m}, факта нет {(today - (s.refined_on or s.planned_on)).days} дн.", "bad": True}
                for s in sorted(stages, key=lambda s: s.refined_on or s.planned_on) if s.id in deal_of]
    objects = {
        "sales": [{"kind": "сделка", "title": f"{d.number} · {d.client.name if d.client else ''}", "url": f"/deals/{d.id}", "note": f"срок КП {d.kp_due:%d.%m} прошёл", "bad": True} for d in kp_late]
                 + [{"kind": "черновик", "title": f"{d.number} · {d.client.name if d.client else ''}", "url": f"/deals/{d.id}", "note": f"без движения с {d.updated_at:%d.%m}", "bad": False} for d in stale],
        "factory": st_rows(fc), "logistics": st_rows(lg), "service": st_rows(sv),
        "finance": st_rows(py) + [{"kind": "обязательство", "title": f"{r['title']} · {r['client']}", "url": r["url"] or "/finance?tab=liquidity",
                                   "note": f"{_money(r['amount'], r['currency'])}, просрочка {r['overdue_days']} дн.", "bad": True} for r in rec_rows],
        "legal": [{"kind": "претензия", "title": f"Претензия {cl.number}", "url": f"/legal?tab=claims&open={cl.id}", "note": f"срок ответа {cl.answer_due:%d.%m} истёк", "bad": True}
                  for cl in claims if cl.answer_due and cl.answer_due < today and cl.status in ("sent", "received")]
                 + [{"kind": "договор", "title": f"Договор {x['number']}", "url": "/legal?tab=contracts&flag=no_scan", "note": "подписан, скана нет", "bad": False}
                    for x in list({x["number"]: x for x in cin.get("no_scan", [])}.values())[:30]]
                 + [{"kind": "договор", "title": f"Договор {x['number']} ({x['cp_name']})", "url": "/legal?tab=contracts&flag=expiring", "note": f"истекает через {x['days']} дн.", "bad": False}
                    for x in cin.get("expiring", [])],
    }
    directions = []
    for key, title in asg.DIRECTIONS:
        staff, tn, results, devs, url = D[key]
        h = users_by_id.get(hd.get(key) or "")
        head = None
        if h:
            pos = (h.contacts or {}).get("position", "") if isinstance(h.contacts, dict) else ""
            head = {"id": h.id, "name": h.display_name, "position": pos, "deputy": asg.deputy_info(h, users_by_id, today)}
        directions.append({"key": key, "title": title, "head": head, "people": staff, "staff_count": len(staff), "tone": tn,
                           "results": results, "deviations": devs, "lines": results + devs, "url": url, "card_url": f"/directions/{key}",
                           "objects": objects.get(key, [])})

    # ---------------- ближайшие сроки (D-156, DEV-12): тип, ответственный, срочность; просроченные поручения — тоже
    KIND = {"task": "задача", "kp": "срок КП", "decision": "решение клиента", "stage": "этап исполнения", "poa": "доверенность", "contract": "договор", "claim": "претензия"}
    deal_mgr = {d.id: names.get(d.manager_id, "") for d in list(open_deals) + [r["deal"] for r in exec_rows]}
    legal_head = names.get(hd.get("legal") or "", "") or "юрист"

    def urgency(on: date) -> str:
        return "overdue" if on < today else "today" if on == today else "soon" if (on - today).days <= 3 else "later"
    dl = []
    for ev in events or []:
        on = date.fromisoformat(ev["on"])
        if not today <= on <= today + timedelta(days=14):
            continue
        url = ev.get("url") or ""
        did = url.split("/deals/")[1].split("?")[0].split("/")[0] if url.startswith("/deals/") else ""
        who = deal_mgr.get(did, "") if did else (legal_head if ev.get("kind") in ("poa", "contract", "claim") else "")
        dl.append({"on": ev["on"], "title": ev["title"], "sub": ev.get("sub", ""), "type": KIND.get(ev.get("kind", ""), ev.get("kind", "") or "событие"),
                   "who": who, "url": url, "urgency": urgency(on)})
    from ..models import Assignment as _A
    for a in db.scalars(select(_A).where(_A.status.in_(asg.OPEN), _A.due_on <= today + timedelta(days=14))).all():
        if not asg.can_see(user, a):
            continue
        dl.append({"on": a.due_on.isoformat(), "title": f"{a.number}: {a.title}", "sub": asg.STATUS_TITLES[a.status], "type": "поручение",
                   "who": names.get(a.executor_id or a.assignee_id, ""), "url": f"/assignments?open={a.id}", "urgency": urgency(a.due_on)})
    U = {"overdue": 0, "today": 1, "soon": 2, "later": 3}
    deadlines = sorted(dl, key=lambda x: (x["on"], U[x["urgency"]], x["title"]))

    # ---------------- поручения ЗГД (D-154); контроль документов Канцелярии — отдельной строкой
    assignments = asg.summary(db, user, today, names)
    o_over = sum(r["overdue"] for r in ctl.get("items", []))
    assignments["office"] = {"open": sum(r["open"] for r in ctl.get("items", [])), "overdue": o_over, "url": "/office?t=control"}

    return {"meta": {"generated_at": now, "today": today.isoformat(), "period": period, "timezone": "Europe/Moscow", "quality_titles": {"confirmed": "подтверждено", "partial": "частичные данные", "stale": "устарело", "unavailable": "нет данных", "unverified": "не подтверждено"}},
            "kpis": kpis, "escalations": escalations, "directions": directions, "assignments": assignments, "drill": drill, "deadlines": deadlines,
            "can_assign": user.has(Role.GD)}
