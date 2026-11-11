"""Помощник CRM (docs/operations/06): только чтение. Факты дают серверные инструменты после проверки прав, а не модель.

Поток: вопрос → лимиты → детерминированный выбор инструмента по тексту → инструмент с текущим пользователем (права,
замещение, видимость сделок и финансов) → ответ по шаблону + источники из результатов инструмента → (если владелец включил
провайдера и политику allow_minimized) модель может только переформулировать текст; проверка чисел и ссылок, иначе
детерминированный ответ. По умолчанию провайдер none (D-055): режимы «CRM» и «Справка» работают, общий режим честно выключен.

Модель не получает сессию БД, SQL, файлы, HTTP, окружение и не выбирает произвольную функцию: инструменты — закрытый список,
аргументы проверяются, пользователь берётся из сессии.
"""
from __future__ import annotations

import contextvars
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Optional

from fastapi import HTTPException
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser
from ..core.config import Settings
from ..models import AiMessage, AiToolAudit, Deal, Task, User

log = logging.getLogger("gsho.assistant")

MODES = {"crm": "Вопросы по CRM", "help": "Справка", "general": "Общий вопрос"}
MAX_TEXT = 4000
HISTORY_LIMIT = 20
GENERAL_OFF = "Общий ИИ отключён. Вопросы по CRM и справка доступны в разрешённых разделах."


@dataclass
class Source:
    type: str
    id: str
    label: str
    url: str

    def dto(self) -> dict:
        return {"type": self.type, "id": self.id, "label": self.label[:160], "url": self.url}


@dataclass
class Answer:
    text: str
    sources: list[Source] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tool: str = ""
    status: str = "complete"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _msk_today() -> date:
    return (_now() + timedelta(hours=3)).date()


def _money(v) -> str:
    try:
        x = Decimal(str(v))
    except Exception:   # noqa: BLE001
        return str(v)
    q = f"{x:,.2f}".replace(",", " ").replace(".", ",")
    return q[:-3] if q.endswith(",00") else q


# ------------------------------------------------------------------ справка (локальный индекс, без модели)

HELP = [
    {"id": "support-create", "title": "Как создать обращение в поддержку", "route": "/support?new=1",
     "keys": "обращение поддержка ошибка проблема тикет сообщить баг", "text": "Нажмите «Поддержка» в шапке → «Новое обращение». Выберите категорию и срочность, опишите, что делали и что произошло, приложите скриншот. Ответ придёт в CRM — в колокольчик и на кнопку «Поддержка»."},
    {"id": "mfa-enable", "title": "Как включить вход с кодом из приложения", "route": "/settings?s=security",
     "keys": "код приложение двухфакторн totp mfa безопасность телефон вход", "text": "«Настройки → Безопасность» → «Включить код из приложения». Отсканируйте QR-код приложением (Яндекс Ключ, Google Authenticator и т. п.), введите код и сохраните 10 кодов восстановления."},
    {"id": "password-change", "title": "Как сменить пароль", "route": "/settings?s=security",
     "keys": "пароль сменить поменять забыл", "text": "«Настройки → Безопасность» → «Сменить пароль». Пароль — от 12 символов, с буквой и цифрой. Забыли — попросите администратора безопасности выдать временный."},
    {"id": "sessions", "title": "Как завершить вход на другом устройстве", "route": "/settings?s=security",
     "keys": "сессии устройство выйти завершить вход", "text": "«Настройки → Безопасность» → «Активные входы»: завершите лишний вход или все, кроме текущего."},
    {"id": "deal-create", "title": "Как создать новый расчёт (сделку)", "route": "/deals",
     "keys": "расчёт сделка создать новый кп", "text": "Кнопка «Новый расчёт» на рабочем месте или в разделе «Сделки». Выберите клиента и площадку, затем добавьте позиции оборудования."},
    {"id": "tasks", "title": "Где мои задачи и как отметить выполнение", "route": "/tasks",
     "keys": "задачи поручения выполнить отметить срок", "text": "«Мои задачи» в меню. Выполненную задачу отметьте галочкой — на рабочем месте или в карточке сделки."},
    {"id": "briefing", "title": "Что показывает блок «Ваши приоритеты»", "route": "/",
     "keys": "приоритеты сводка главное рабочее место отложить скрыть закрепить", "text": "На рабочем месте — 3–5 дел из ваших задач, согласований, сроков и обращений. Через «⋯» дело можно закрепить, отложить или скрыть, пока оно не изменится; срок самого дела при этом не меняется."},
    {"id": "workplace-layout", "title": "Как настроить рабочее место", "route": "/settings?s=workplace",
     "keys": "рабочее место блоки порядок скрыть настроить", "text": "«Настройки → Рабочее место»: включите нужные блоки и расставьте их порядок."},
    {"id": "finance", "title": "Где смотреть платежи и задолженность", "route": "/finance",
     "keys": "финансы платежи задолженность долг дебиторка оплата обязательства", "text": "Раздел «Финансы» (если он вам доступен): реестр обязательств, задолженность по контрагентам, ликвидность. Остаток уменьшают только подтверждённые оплаты."},
    {"id": "access", "title": "Как выдать сотруднику права", "route": "/settings?s=access",
     "keys": "права доступ выдать исключение уровни роль", "text": "«Настройки → Уровни доступа» (для администратора доступа): стартовые уровни ролей и исключения для сотрудников. Повышение прав безопасности и поддержки требует подтверждения кодом."},
    {"id": "journal", "title": "Где журнал безопасности и предупреждения", "route": "/settings?s=security_audit",
     "keys": "журнал безопасности предупреждения скачивания выгрузки алерт", "text": "«Настройки → Журнал и предупреждения» (по праву на журнал): кто входил, менял права, скачивал файлы. Предупреждение закрывается с комментарием."},
    {"id": "backup", "title": "Где статус резервных копий", "route": "/settings?s=backup",
     "keys": "резервные копии бэкап backup восстановление", "text": "«Настройки → Резервные копии»: последняя копия, проверка, следующий запуск."},
    {"id": "files", "title": "Почему файл не загрузился", "route": "/support?new=1",
     "keys": "файл загрузить не загружается антивирус угроза архив", "text": "Каждый файл проверяет антивирус. «Не принят: угроза» — не открывайте исходный файл и сообщите администратору. «Проверка временно недоступна» — повторите через несколько минут. Большие и многоуровневые архивы не принимаются."},
    {"id": "catalog", "title": "Где каталог оборудования и цены", "route": "/catalog",
     "keys": "каталог оборудование модель цена исполнение", "text": "Раздел «Каталог»: модели, исполнения, фото и документы. Цены заводов видны по праву на историю цен."},
]


def help_search(question: str, limit: int = 5) -> list[dict]:
    words = [w for w in re.findall(r"[а-яёa-z0-9]+", (question or "").lower()) if len(w) >= 3]
    scored = []
    for h in HELP:
        hay = (h["title"] + " " + h["keys"]).lower()
        score = sum(2 if w in h["keys"] else 1 for w in words if any(k.startswith(w[:5]) for k in hay.split()))
        if score:
            scored.append((score, h))
    scored.sort(key=lambda x: -x[0])
    return [h for _, h in scored[:min(limit, 5)]]


# ------------------------------------------------------------------ инструменты (закрытый список)

CURRENT: contextvars.ContextVar[tuple] = contextvars.ContextVar("assistant_msg", default=(None, None))   # (conversation_id, message_id)


def _audit(db: Session, user: CurrentUser, tool: str, args: dict, n: int, ids: list[str], started: float, status: str = "ok", err: str = "") -> None:
    from .security_events import sanitize_meta
    cid, mid = CURRENT.get()
    db.add(AiToolAudit(conversation_id=cid, message_id=mid, actor_user_id=user.id, tool_name=tool, arguments_sanitized=sanitize_meta(args), result_status=status, result_count=n,
                       entity_ids=[str(i)[:64] for i in ids[:20]], duration_ms=int((time.monotonic() - started) * 1000), error_code=err[:32]))


def tool_deal_search(db: Session, user: CurrentUser, query: str, limit: int = 10) -> list[dict]:
    from ..api.common import can_see_deal
    from ..core.workflow import STATUS_TITLES
    from ..models import Client
    q = (query or "").strip()[:80]
    if not q:
        return []
    like = f"%{q.replace('%', '').replace('_', '')}%"
    rows = db.scalars(select(Deal).outerjoin(Client, Client.id == Deal.client_id)
                      .where(Deal.deleted_at.is_(None), or_(Deal.number.ilike(like), Client.name.ilike(like), Deal.site.ilike(like)))
                      .order_by(Deal.updated_at.desc()).limit(100)).all()
    out = []
    for d in rows:
        if not can_see_deal(user, d):    # нет доступа — как будто записи нет
            continue
        out.append({"id": d.id, "number": d.number, "client": d.client.name if d.client else "", "status": STATUS_TITLES.get(d.status, str(d.status)),
                    "url": f"/deals/{d.id}"})
        if len(out) >= min(limit, 10):
            break
    return out


def tool_deal_get(db: Session, user: CurrentUser, deal_id: str) -> Optional[dict]:
    from ..api.common import can_see_deal
    from ..core.workflow import STATUS_TITLES
    d = db.get(Deal, deal_id)
    if d is None or d.deleted_at is not None or not can_see_deal(user, d):
        return None
    mgr = db.get(User, d.manager_id) if d.manager_id else None
    out = {"id": d.id, "number": d.number, "client": d.client.name if d.client else "", "site": d.site or "", "status": STATUS_TITLES.get(d.status, str(d.status)),
           "manager": mgr.display_name if mgr else "", "kp_due": d.kp_due.isoformat() if d.kp_due else None,
           "decision_expected_on": d.decision_expected_on.isoformat() if d.decision_expected_on else None,
           "equipment": [f"{int(p.qty or 1)} × {' '.join(x for x in (p.trademark or '', p.model or '') if x).strip() or p.machine_type or 'позиция'}" for p in (d.positions or [])[:5]],
           "url": f"/deals/{d.id}"}
    if user.can("finance_analytics"):     # цена КП — как на рабочем месте; себестоимость и маржа помощнику не отдаются
        from ..api.workplace import _price
        p = _price(d)
        if p is not None:
            out["price_rub"] = str(round(p, 2))
    tasks = db.scalars(select(Task).where(Task.deal_id == d.id, Task.done_at.is_(None))).all()
    mine = [t for t in tasks if t.assignee_id == user.id or t.assignee_id in user.acting_for]
    out["my_open_tasks"] = [{"title": t.title, "due": t.due.isoformat()} for t in mine[:5]]
    return out


def tool_price_history(db: Session, user: CurrentUser, model: str, limit: int = 10) -> Optional[list[dict]]:
    """Цены заводов — только с правом на историю цен завода; без права — None (недоступно), а не пустой список."""
    if not user.can("factory_price_history"):
        return None
    from ..models import Factory, FactoryPriceHistory
    q = (model or "").strip()[:80]
    if not q:
        return []
    # модель ищем по «кодовым» словам вопроса (с цифрами: XE215, 9150), иначе по всем словам от 3 букв
    toks = [t for t in re.findall(r"[\w\-]+", q) if len(t) >= 3]
    codes = [t for t in toks if re.search(r"\d", t)] or toks
    if not codes:
        return []
    cond = or_(*[FactoryPriceHistory.model.ilike(f"%{t.replace('%', '').replace('_', '')}%") for t in codes[:4]])
    rows = db.scalars(select(FactoryPriceHistory).where(cond).order_by(FactoryPriceHistory.on.desc()).limit(min(limit, 10))).all()
    fac = {f.id: f.name for f in db.scalars(select(Factory).where(Factory.id.in_({r.factory_id for r in rows} or {"-"}))).all()}
    return [{"model": r.model, "price": _money(r.unit_price), "currency": r.currency, "on": r.on.isoformat(), "basis": r.basis, "source": r.source,
             "factory": fac.get(r.factory_id, ""), "url": f"/deals/{r.deal_id}" if r.deal_id else "/catalog"} for r in rows]


def tool_finance_summary(db: Session, user: CurrentUser, direction: str, counterparty: str = "") -> Optional[dict]:
    """Задолженность через finance.debt.build (тот же расчёт, что экран «Финансы»); без права на раздел — None."""
    if not user.can("finance_section"):
        return None
    from .finance import debt
    from .finance.access import Visibility
    as_of = _msk_today()
    res = debt.build(db, user, Visibility(db, user), as_of=as_of, direction=direction)
    totals = res["totals"].get(direction, {})
    groups = [g for g in res["groups"] if g["direction"] == direction]
    if counterparty:
        c = counterparty.lower()
        groups = [g for g in groups if c in (g["title"] or "").lower()]
        cur: dict[str, dict] = {}
        for g in groups:
            for code, t in g["by_currency"].items():
                x = cur.setdefault(code, {"remaining": Decimal(0), "overdue": Decimal(0)})
                x["remaining"] += Decimal(t.get("remaining", "0"))
                x["overdue"] += Decimal(t.get("overdue", "0"))
        totals = {k: {"remaining": str(v["remaining"]), "overdue": str(v["overdue"])} for k, v in cur.items()}
    return {"as_of": as_of.isoformat(), "direction": direction, "by_currency": totals, "groups": [{"title": g["title"], "by_currency": g["by_currency"]} for g in groups[:5]],
            "undated": len([u for u in res["undated"] if u["direction"] == direction]), "notes": res["notes"][:2], "url": "/finance?tab=debt"}


def tool_tasks_my(db: Session, user: CurrentUser, days: int = 7, limit: int = 10) -> list[dict]:
    today = _msk_today()
    who = [user.id] + list(user.acting_for or [])
    rows = db.scalars(select(Task).where(Task.done_at.is_(None), Task.assignee_id.in_(who), Task.due <= today + timedelta(days=days)).order_by(Task.due).limit(min(limit, 20))).all()
    from ..api.common import can_see_deal
    out = []
    for t in rows:
        d = db.get(Deal, t.deal_id)
        out.append({"id": t.id, "title": t.title, "due": t.due.isoformat(), "overdue": t.due < today, "deputy": t.assignee_id != user.id,
                    "deal": d.number if d and can_see_deal(user, d) else "", "url": f"/deals/{t.deal_id}" if d and can_see_deal(user, d) else "/tasks"})
    return out


def tool_workplace_summary(db: Session, user: CurrentUser) -> dict:
    """Те же «Ваши приоритеты», что на рабочем месте (уже отфильтровано по правам), без денежных блоков."""
    from ..api.workplace import briefing_block, build
    out, ctx = build(db, user)
    b = briefing_block(db, user, ctx)
    return {"items": [{"title": i["title"], "reason": i["reason"], "advice": i["advice_text"], "url": i["url"], "key": i["item_key"], "label": i["source_label"]}
                      for i in b.get("items", [])], "approvals": len(out.get("approvals") or []), "warnings": b.get("warnings", [])}


TOOLS: dict[str, Callable] = {"deal_search": tool_deal_search, "deal_get": tool_deal_get, "equipment_price_history": tool_price_history,
                              "finance_obligations_summary": tool_finance_summary, "tasks_my": tool_tasks_my,
                              "workplace_summary": tool_workplace_summary, "help_search": help_search}


# ------------------------------------------------------------------ выбор инструмента (детерминированно)

_DEAL_NO = re.compile(r"\b([A-ZА-Я]{1,3}-\d{4}-\d{3,5})\b", re.I)


def route(question: str) -> tuple[str, dict]:
    q = (question or "").strip()
    low = q.lower()
    m = _DEAL_NO.search(q)
    if m:
        return "deal_get_by_number", {"number": m.group(1).upper()}
    if re.search(r"\b(как|где|почему|инструкц|что такое|что значит|настро|включить|создать обращ)", low):
        return "help_search", {"question": q}
    if re.search(r"(задолжен|долж|долг|дебитор|кредитор|оплат|платеж|платёж)", low):
        if re.search(r"(нам долж|долж\w* нам|дебитор|поступлен|клиент.* долж|заказчик.* долж|долж\w* (клиент|заказчик))", low):
            return "finance_obligations_summary", {"direction": "in", "counterparty": _after(low, ("заказчик", "клиент", "от"))}
        if re.search(r"(мы долж|наш долг|кредитор|заводу|поставщику|мы оплат|нам оплатить)", low):
            return "finance_obligations_summary", {"direction": "out", "counterparty": _after(low, ("заводу", "поставщику"))}
        return "clarify_direction", {}
    if re.search(r"(цен[аыу]|стоит|стоимост|почём|прайс)", low):
        model = re.sub(r"(сколько|стоит|цена|цены|цену|стоимость|какая|была|на|по|модели|модель|экскаватор|самосвал|погрузчик|\?)", " ", low)
        return "equipment_price_history", {"model": " ".join(model.split())[:60]}
    if re.search(r"(решени|согласован|приоритет|что делать|что сделать|главное|срочн|сегодня)", low):
        return "workplace_summary", {}
    if re.search(r"(задач|поручен|просроч)", low):
        return "tasks_my", {}
    if re.search(r"(сделк|клиент|расчёт|расчет|кп\b)", low):
        return "deal_search", {"query": re.sub(r"(сделк\w*|клиент\w*|найди|покажи|по|расч[её]т\w*)", " ", low).strip()[:60]}
    return "help_search", {"question": q}


def _after(low: str, words: tuple) -> str:
    for w in words:
        m = re.search(w + r"\w*\s+([«\"]?[\w\- ]{2,40})", low)
        if m:
            return m.group(1).strip(" «»\"?")[:40]
    return ""


# ------------------------------------------------------------------ ответы по шаблону

def answer_crm(db: Session, user: CurrentUser, question: str) -> Answer:
    tool, args = route(question)
    started = time.monotonic()
    if tool == "clarify_direction":
        return Answer("Уточните, пожалуйста: речь о том, сколько должны нам (клиенты), или о том, сколько должны мы (заводам и поставщикам)? "
                      "Можно указать контрагента, например: «сколько нам должен заказчик Север».", tool="clarify")
    if tool == "help_search":
        return answer_help(db, user, args["question"])
    if tool == "deal_get_by_number":
        d = db.scalar(select(Deal).where(Deal.number == args["number"], Deal.deleted_at.is_(None)))
        res = tool_deal_get(db, user, d.id) if d else None
        _audit(db, user, "deal_get", {"number": args["number"]}, 1 if res else 0, [res["id"]] if res else [], started)
        if res is None:
            return Answer(f"Сделка {args['number']} не найдена среди доступных вам.", tool="deal_get")
        parts = [f"Сделка {res['number']} · {res['client']}" + (f", {res['site']}" if res["site"] else ""), f"Статус: {res['status']}."]
        if res["manager"]:
            parts.append(f"Менеджер: {res['manager']}.")
        if res["equipment"]:
            parts.append("Оборудование: " + "; ".join(res["equipment"]) + ".")
        if res["kp_due"]:
            parts.append(f"Срок КП: {date.fromisoformat(res['kp_due']):%d.%m.%Y}.")
        if res.get("price_rub"):
            parts.append(f"Сумма КП: {_money(res['price_rub'])} RUB.")
        if res["my_open_tasks"]:
            parts.append("Ваши открытые задачи: " + "; ".join(f"{t['title']} (до {date.fromisoformat(t['due']):%d.%m})" for t in res["my_open_tasks"]) + ".")
        return Answer(" ".join(parts), sources=[Source("deal", res["id"], f"Сделка {res['number']}", res["url"])],
                      facts=[{"deal": res["number"], "status": res["status"], **({"price_rub": res["price_rub"]} if res.get("price_rub") else {})}], tool="deal_get")
    if tool == "deal_search":
        rows = tool_deal_search(db, user, args["query"])
        _audit(db, user, "deal_search", {"query_len": len(args["query"])}, len(rows), [r["id"] for r in rows], started)
        if not rows:
            return Answer("Среди доступных вам сделок ничего не найдено. Уточните номер сделки, клиента или площадку.", tool="deal_search")
        lines = [f"{r['number']} · {r['client']} — {r['status']}" for r in rows]
        return Answer("Нашёл среди доступных вам сделок:\n" + "\n".join(lines), sources=[Source("deal", r["id"], f"Сделка {r['number']}", r["url"]) for r in rows], tool="deal_search")
    if tool == "equipment_price_history":
        rows = tool_price_history(db, user, args["model"])
        _audit(db, user, "equipment_price_history", {"model_len": len(args["model"])}, len(rows or []), [], started, "denied" if rows is None else "ok")
        if rows is None:
            return Answer("История цен заводов вам недоступна. Обратитесь к коммерческому директору или ЗГД.", tool="equipment_price_history")
        if not rows:
            return Answer("Записей о ценах по этой модели среди доступных вам не найдено. Уточните модель так, как она указана в каталоге.", tool="equipment_price_history")
        lines = [f"{r['model']} — {r['price']} {r['currency']}, {date.fromisoformat(r['on']):%d.%m.%Y}" + (f", {r['factory']}" if r["factory"] else "") +
                 (f", {r['basis']}" if r["basis"] else "") for r in rows]
        curs = sorted({r["currency"] for r in rows})
        warn = ["Цены в разных валютах не сравниваются и не усредняются."] if len(curs) > 1 else []
        return Answer("Последние известные цены заводов (показаны доступные вам записи):\n" + "\n".join(lines),
                      sources=[Source("price", f"{r['model']}|{r['on']}", f"{r['model']} {r['on']}", r["url"]) for r in rows][:5],
                      facts=[{"price": r["price"], "currency": r["currency"], "on": r["on"]} for r in rows], warnings=warn, tool="equipment_price_history")
    if tool == "finance_obligations_summary":
        res = tool_finance_summary(db, user, args["direction"], args.get("counterparty", ""))
        _audit(db, user, "finance_obligations_summary", {"direction": args["direction"], "counterparty": bool(args.get("counterparty"))},
               len((res or {}).get("by_currency", {})), [], started, "denied" if res is None else "ok")
        if res is None:
            if user.can("finance_analytics"):
                return Answer("Подробные обязательства вам недоступны. Общая картина поступлений — в сводке по деньгам.",
                              sources=[Source("page", "summary-money", "Сводка по деньгам", "/summary?kind=money")], tool="finance_obligations_summary")
            return Answer("У вас нет доступа к финансовым обязательствам. Обратитесь к финансовому директору.", tool="finance_obligations_summary")
        who = "нам должны клиенты" if args["direction"] == "in" else "должны мы заводам и поставщикам"
        cp = f" (контрагент: «{args['counterparty']}»)" if args.get("counterparty") else ""
        cur = res["by_currency"]
        if not cur:
            text_ = f"По доступным финансовым данным на {date.fromisoformat(res['as_of']):%d.%m.%Y} открытых обязательств, где {who}{cp}, нет."
        else:
            lines = [f"{c}: остаток {_money(t['remaining'])}" + (f", из них просрочено {_money(t['overdue'])}" if Decimal(str(t.get('overdue', '0'))) > 0 else "")
                     + (f"; подтверждено оплат {_money(t['confirmed'])}" if t.get("confirmed") else "")
                     + (f", временно разнесено {_money(t['provisional'])}" if t.get("provisional") and Decimal(str(t["provisional"])) > 0 else "")
                     for c, t in cur.items()]
            text_ = f"По доступным финансовым данным на {date.fromisoformat(res['as_of']):%d.%m.%Y} {who}{cp}:\n" + "\n".join(lines)
        warns = list(res["notes"])
        if res["undated"]:
            warns.append(f"Ещё {res['undated']} обязательств без согласованной даты — в остаток не входят.")
        return Answer(text_, sources=[Source("page", "finance-debt", "Финансы · задолженность", res["url"])],
                      facts=[{"currency": c, "remaining": t["remaining"]} for c, t in cur.items()], warnings=warns, tool="finance_obligations_summary")
    if tool == "tasks_my":
        rows = tool_tasks_my(db, user)
        _audit(db, user, "tasks_my", {"days": 7}, len(rows), [r["id"] for r in rows], started)
        if not rows:
            return Answer("Открытых задач со сроком в ближайшую неделю нет.", sources=[Source("page", "tasks", "Мои задачи", "/tasks")], tool="tasks_my")
        lines = [("⚠ " if r["overdue"] else "") + f"{r['title']} — до {date.fromisoformat(r['due']):%d.%m}" + (f", сделка {r['deal']}" if r["deal"] else "")
                 + (" (по замещению)" if r["deputy"] else "") for r in rows]
        return Answer("Ваши задачи на ближайшую неделю:\n" + "\n".join(lines), sources=[Source("page", "tasks", "Мои задачи", "/tasks")], tool="tasks_my")
    if tool == "workplace_summary":
        res = tool_workplace_summary(db, user)
        _audit(db, user, "workplace_summary", {}, len(res["items"]), [i["key"] for i in res["items"]], started)
        if not res["items"]:
            return Answer("Срочных дел по вашим задачам и срокам нет.", sources=[Source("page", "workplace", "Рабочее место", "/")], tool="workplace_summary")
        lines = [f"{i['title']} — {i['reason']}. {i['advice']}." for i in res["items"]]
        return Answer("Сейчас требует вашего внимания:\n" + "\n".join(lines), warnings=res["warnings"],
                      sources=[Source(i["label"], i["key"], i["title"], i["url"]) for i in res["items"]], tool="workplace_summary")
    return answer_help(db, user, question)


def answer_help(db: Session, user: CurrentUser, question: str) -> Answer:
    started = time.monotonic()
    hits = help_search(question)
    _audit(db, user, "help_search", {"len": len(question)}, len(hits), [h["id"] for h in hits], started)
    if not hits:
        return Answer("В справке ответа не нашлось. Можно задать вопрос специалистам — через «Поддержку».",
                      sources=[Source("help", "support", "Поддержка", "/support?new=1")], tool="help_search")
    top = hits[0]
    more = [f"• {h['title']}" for h in hits[1:3]]
    return Answer(f"{top['title']}.\n{top['text']}" + ("\n\nЕщё по теме:\n" + "\n".join(more) if more else ""),
                  sources=[Source("help", h["id"], h["title"], h["route"]) for h in hits[:3]], tool="help_search")


# ------------------------------------------------------------------ модель (по умолчанию выключена)

_NUM = re.compile(r"\d[\d\s.,]*\d|\d")


def _numbers(s: str) -> set[str]:
    return {re.sub(r"[\s.,]", "", m) for m in _NUM.findall(s or "")}


def provider_state(s: Settings) -> dict:
    from ..adapters.llm import configured_providers
    configured = s.assistant_provider != "none" and configured_providers().get(s.assistant_provider, False)
    return {"provider": s.assistant_provider if configured else "none", "configured": bool(configured),
            "external_context_allowed": bool(configured and s.assistant_context_policy == "allow_minimized")}


def rewrite(ans: Answer, s: Settings, llm_factory: Optional[Callable] = None) -> tuple[Answer, str, bool]:
    """Модель только переформулирует готовый ответ из фактов; проверка: те же числа, никаких новых ссылок. Иначе — исходный текст.
    Возвращает (ответ, провайдер, fallback)."""
    st = provider_state(s)
    if not st["external_context_allowed"] or ans.tool in ("clarify", "help_search"):
        return ans, "none", True
    from ..adapters.llm import make_llm
    llm = (llm_factory or make_llm)(s.assistant_provider, s.assistant_model)
    payload = ans.text      # минимальный контекст: только готовый текст ответа (без ID, ссылок, истории и страницы)
    try:
        r = llm.complete("Переформулируй ответ сотруднику CRM короче и яснее. Не добавляй фактов, чисел, дат, ссылок и советов, "
                         "которых нет в тексте. Отвечай по-русски.", payload, temperature=0.1, max_tokens=800)
        out = (r.text or "").strip()
    except Exception as e:   # noqa: BLE001 — таймаут/ошибка провайдера: детерминированный ответ
        log.warning("assistant provider failed: %s", type(e).__name__)
        ans.warnings.append("Помощник ответил без переформулирования: внешняя модель недоступна.")
        return ans, "none", True
    if not out or _numbers(out) - _numbers(ans.text) or re.search(r"https?://|/api/|\b[0-9a-f]{8}-[0-9a-f]{4}-", out):
        ans.warnings.append("Переформулированный ответ не прошёл проверку — показан исходный.")
        return ans, s.assistant_provider, True
    ans.text = out
    return ans, s.assistant_provider, False


# ------------------------------------------------------------------ лимиты

def take_quota(db: Session, user_id: str, s: Settings) -> None:
    """Атомарно: один UPDATE с условием — параллельные запросы не обходят ни дневной, ни минутный лимит."""
    today, now = _msk_today(), _now()
    db.execute(text("INSERT INTO ai_usage_daily (user_id, usage_date, request_count, token_in, token_out, minute_count, updated_at) "
                    "VALUES (:u, :d, 0, 0, 0, 0, now()) ON CONFLICT (user_id, usage_date) DO NOTHING"), {"u": user_id, "d": today})
    row = db.execute(text("""
        UPDATE ai_usage_daily SET
          request_count = request_count + 1,
          minute_count = CASE WHEN minute_started_at IS NULL OR minute_started_at < :win THEN 1 ELSE minute_count + 1 END,
          minute_started_at = CASE WHEN minute_started_at IS NULL OR minute_started_at < :win THEN :now ELSE minute_started_at END,
          updated_at = now()
        WHERE user_id = :u AND usage_date = :d AND request_count < :daily
          AND (minute_started_at IS NULL OR minute_started_at < :win OR minute_count < :minute)
        RETURNING request_count"""), {"u": user_id, "d": today, "now": now, "win": now - timedelta(seconds=60),
                                     "daily": s.assistant_daily_limit, "minute": s.assistant_minute_limit}).first()
    if row is None:
        db.rollback()
        used = db.execute(text("SELECT request_count FROM ai_usage_daily WHERE user_id = :u AND usage_date = :d"), {"u": user_id, "d": today}).scalar() or 0
        if used >= s.assistant_daily_limit:
            raise HTTPException(429, {"code": "daily_limit", "message": f"Дневной лимит вопросов помощнику исчерпан ({s.assistant_daily_limit}). Он обновится завтра."})
        raise HTTPException(429, {"code": "minute_limit", "message": "Слишком много вопросов подряд — подождите минуту."})


# ------------------------------------------------------------------ хранение

def purge(db: Session, s: Settings) -> int:
    """Фоновое стирание содержимого: удалённые диалоги и сообщения старше срока хранения. Технический след инструментов остаётся."""
    from ..models import AiConversation
    cutoff = _now() - timedelta(days=s.assistant_retention_days)
    deleted = [c.id for c in db.scalars(select(AiConversation).where(AiConversation.deleted_at.is_not(None))).all()]
    q = select(AiMessage).where(AiMessage.purged_at.is_(None), or_(AiMessage.created_at < cutoff, AiMessage.conversation_id.in_(deleted or ["-"])))
    n = 0
    for m in db.scalars(q).all():
        m.content, m.facts, m.sources, m.purged_at = "", [], [], _now()
        n += 1
    for c in db.scalars(select(AiConversation).where(AiConversation.deleted_at.is_not(None), AiConversation.title != "")).all():
        c.title = ""
    db.commit()
    return n


def fingerprint(question: str) -> str:
    return hashlib.sha256((question or "").encode()).hexdigest()[:16]


__all__ = ["Answer", "MODES", "TOOLS", "answer_crm", "answer_help", "help_search", "route", "rewrite", "take_quota", "purge", "provider_state", "GENERAL_OFF"]
