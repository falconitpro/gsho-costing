"""Бизнес-процесс сделки: статусы, переходы, задачи, ступени визации (разделы 3 и 10 ТЗ).

Чистая логика без БД: на вход — текущее состояние, на выход — новое состояние и список задач/уведомлений.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Optional

from .roles import Role


class DealStatus(str, Enum):
    DRAFT = "draft"                       # Черновик
    LOGISTICS_CALC = "logistics_calc"     # На расчёте логистики
    SERVICE_CALC = "service_calc"         # На расчёте сервиса (параллельно)
    CALC_READY = "calc_ready"             # Расчёт сформирован
    CD_APPROVAL = "cd_approval"           # На утверждении КД
    FD_VISA = "fd_visa"                   # На визе финдиректора
    GD_VISA = "gd_visa"                   # На визе гендиректора
    REWORK = "rework"                     # На доработке
    APPROVED = "approved"                 # Утверждено
    KP_SENT = "kp_sent"                   # КП отправлено
    WON = "won"                           # Выиграна (переходный статус: сразу → «Заключение контракта», задача 2)
    LOST = "lost"
    CANCELLED = "cancelled"
    CONTRACT = "contract"                 # Заключение контракта (задача 2)
    EXECUTION = "execution"               # Исполнение контракта
    CLOSED = "closed"                     # Закрыта после исполнения (закрывает КД)


# Стадии после выигрыша (задача 2)
POST_WIN_STATUSES = {DealStatus.WON, DealStatus.CONTRACT, DealStatus.EXECUTION, DealStatus.CLOSED}
# Статусы, в которых сделка ещё открыта (для автозакрытия связанных заявок, задача 9)
OPEN_STATUSES = {DealStatus.DRAFT, DealStatus.LOGISTICS_CALC, DealStatus.SERVICE_CALC, DealStatus.CALC_READY, DealStatus.CD_APPROVAL,
                 DealStatus.FD_VISA, DealStatus.GD_VISA, DealStatus.REWORK, DealStatus.APPROVED, DealStatus.KP_SENT}


STATUS_TITLES = {
    DealStatus.DRAFT: "Черновик",
    DealStatus.LOGISTICS_CALC: "На расчёте логистики",
    DealStatus.SERVICE_CALC: "На расчёте сервиса",
    DealStatus.CALC_READY: "Расчёт сформирован",
    DealStatus.CD_APPROVAL: "На утверждении КД",
    DealStatus.FD_VISA: "На визе финдиректора",
    DealStatus.GD_VISA: "На визе гендиректора",
    DealStatus.REWORK: "На доработке",
    DealStatus.APPROVED: "Утверждено",
    DealStatus.KP_SENT: "КП отправлено",
    DealStatus.WON: "Выиграна",
    DealStatus.LOST: "Проиграна",
    DealStatus.CANCELLED: "Отменена",
    DealStatus.CONTRACT: "Заключение контракта",
    DealStatus.EXECUTION: "Исполнение контракта",
    DealStatus.CLOSED: "Закрыта",
}

# Статусы, начиная с которых логист видит сделку
LOGISTICS_VISIBLE_FROM = {
    DealStatus.LOGISTICS_CALC, DealStatus.SERVICE_CALC, DealStatus.CALC_READY, DealStatus.CD_APPROVAL,
    DealStatus.FD_VISA, DealStatus.GD_VISA, DealStatus.REWORK, DealStatus.APPROVED, DealStatus.KP_SENT,
    DealStatus.WON, DealStatus.LOST, DealStatus.CANCELLED, DealStatus.CONTRACT, DealStatus.EXECUTION, DealStatus.CLOSED,
}


@dataclass
class SlaDefaults:
    """Нормативные сроки задач в рабочих днях (задаёт администратор)."""
    logistics_days: int = 2
    service_days: int = 2
    cd_days: int = 1
    fd_days: int = 1
    gd_days: int = 1
    manager_rework_days: int = 1


def add_business_days(start: date, days: int) -> date:
    """Рабочие дни по производственному календарю РФ (calc/calendar.py)."""
    from ..calc.calendar import add_business_days as _add
    return _add(start, days)


@dataclass
class TaskSpec:
    role: Role
    title: str
    due: date
    block: str = ""
    assignee_hint: Optional[str] = None   # конкретный пользователь (автор блока при возврате)


@dataclass
class Notification:
    event: str
    roles: list[Role]
    channels: list[str]
    text: str
    user_hint: Optional[str] = None


@dataclass
class DealState:
    status: DealStatus
    service_flag: bool
    logistics_done: bool = False
    service_done: bool = False
    manager_id: Optional[str] = None
    logistician_id: Optional[str] = None
    service_head_id: Optional[str] = None
    in_execution: bool = False   # задача 2: сделка уже на стадии контракта/исполнения (повторная виза возвращает туда)
    execution_started: bool = False   # исполнение уже начато — повторная виза возвращает в «Исполнение», а не в «Контракт»


@dataclass
class TransitionResult:
    status: DealStatus
    tasks: list[TaskSpec] = field(default_factory=list)
    notifications: list[Notification] = field(default_factory=list)
    new_version: bool = False
    audit: str = ""


class WorkflowError(Exception):
    pass


# ------------------------------------------------------------------ проверки готовности

def manager_checks(deal: dict[str, Any]) -> list[str]:
    """Проверки перед «Отправить на расчёт» (раздел 4). Возвращает список ошибок."""
    errors: list[str] = []
    if not deal.get("client_id"):
        errors.append("Не выбран клиент.")
    positions = deal.get("positions") or []
    if not positions:
        errors.append("Нет позиций оборудования.")
    for i, p in enumerate(positions, 1):
        for key, title in (("factory", "завод"), ("model", "модель"), ("qty", "количество"),
                           ("unit_price", "цена"), ("currency", "валюта")):
            if not p.get(key):
                errors.append(f"Позиция {i}: не заполнено поле «{title}».")
        # задача 4: обязательные технические характеристики должны быть заполнены (значение завода)
        missing = [r.get("name_ru") or r.get("code") or "?" for r in (p.get("tech_param_rows") or [])
                   if r.get("required") and not r.get("local_ru") and not (r.get("value_ru") or r.get("value_zh"))]
        if missing:
            errors.append(f"Позиция {i}: не заполнены обязательные технические характеристики: {', '.join(missing[:5])}"
                          + (" …" if len(missing) > 5 else "") + ".")
    # задача 1: условия оплаты задаёт КД — менеджер их не вводит; задача 5: варианты только при альтернативных предложениях
    if deal.get("has_alternatives") and len([v for v in (deal.get("variants") or []) if not _is_default_variant(v)]) < 2:
        errors.append("Включены альтернативные предложения — нужно минимум два предложения.")
    if deal.get("service_flag") and not deal.get("target_hours"):
        errors.append("Активен флаг сервиса — укажите целевую наработку.")
    return errors


def _is_default_variant(v: Any) -> bool:
    return bool(v.get("is_default")) if isinstance(v, dict) else bool(getattr(v, "is_default", False))


def cd_checks(deal: dict[str, Any]) -> list[str]:
    """Проверки перед утверждением КД (задачи 1, 6): условия оплаты заданы явно, опции РФ оценены."""
    errors: list[str] = []
    if not deal.get("payment_terms_set"):
        errors.append("Условия оплаты клиента не заданы КД (расчёт идёт по типовой схеме).")
    for p in deal.get("positions") or []:
        if not p.get("factory_payments"):
            errors.append(f"Позиция {p.get('model') or '?'}: график оплаты заводу не задан КД (типовая схема).")
        for o in p.get("local_options") or []:
            if not o.get("cost") or not o.get("supplier_lead_days"):
                errors.append(f"Опция РФ «{o.get('name')}»: не заданы стоимость и срок поставки поставщика.")
    return errors


# ------------------------------------------------------------------ переходы

def send_to_calc(state: DealState, today: date, sla: SlaDefaults) -> TransitionResult:
    if state.status not in (DealStatus.DRAFT, DealStatus.REWORK):
        raise WorkflowError("Отправить на расчёт можно только черновик или сделку на доработке.")
    res = TransitionResult(status=DealStatus.LOGISTICS_CALC, audit="Отправлено на расчёт")
    res.tasks.append(TaskSpec(Role.LOGISTICIAN, "Расчёт логистики и таможни", add_business_days(today, sla.logistics_days), "logistics"))
    res.notifications.append(Notification("task_new", [Role.LOGISTICIAN], ["outlook", "wecom", "app"], "Новая задача: расчёт логистики"))
    if state.service_flag:
        res.tasks.append(TaskSpec(Role.SERVICE_HEAD, "Расчёт сервиса до наработки", add_business_days(today, sla.service_days), "service"))
        res.notifications.append(Notification("task_new", [Role.SERVICE_HEAD], ["outlook", "wecom", "app"], "Новая задача: расчёт сервиса"))
    return res


def block_completed(state: DealState, block: str, today: date, sla: SlaDefaults, checks_ok: bool = True) -> TransitionResult:
    """Логист или сервис завершили блок. Когда все назначенные блоки готовы → «Расчёт сформирован»."""
    if not checks_ok:
        raise WorkflowError("Блок не прошёл проверки (обязательные поля, комментарии к отклонениям).")
    if block == "logistics":
        state.logistics_done = True
    elif block == "service":
        state.service_done = True
    else:
        raise WorkflowError(f"Неизвестный блок {block}")
    all_done = state.logistics_done and (state.service_done or not state.service_flag)
    if not all_done:
        status = DealStatus.SERVICE_CALC if state.logistics_done else DealStatus.LOGISTICS_CALC
        return TransitionResult(status=status, audit=f"Блок «{block}» завершён, ожидание остальных")
    res = TransitionResult(status=DealStatus.CALC_READY, audit="Расчёт сформирован")
    res.tasks.append(TaskSpec(Role.CD, "Назначить цену и утвердить", add_business_days(today, sla.cd_days), "pricing"))
    res.notifications.append(Notification("calc_ready", [Role.CD], ["outlook", "wecom"], "Расчёт готов к утверждению"))
    return res


@dataclass
class ApprovalStep:
    """Ступень визации. Условие — имя предиката из APPROVAL_CONDITIONS, чтобы администратор мог
    добавлять ступени как данные (JSON в настройках) без доработки кода."""
    name: str
    role: Role
    condition: str          # always | margin_below_threshold | cash_gap_above_limit | deal_above_gd_limit | fd_declined | overhead_override
    sla_days: int = 1
    status: DealStatus = DealStatus.CD_APPROVAL


DEFAULT_APPROVAL_LADDER = [
    ApprovalStep("Коммерческая", Role.CD, "always", 1, DealStatus.CD_APPROVAL),
    ApprovalStep("Финансовая", Role.FD, "margin_below_threshold|cash_gap_above_limit", 1, DealStatus.FD_VISA),
    ApprovalStep("Генеральная", Role.GD, "deal_above_gd_limit|fd_declined|overhead_override", 1, DealStatus.GD_VISA),
]


def required_steps(ladder: list[ApprovalStep], flags: dict[str, bool]) -> list[ApprovalStep]:
    """Какие ступени нужны по флагам результата расчёта (ApprovalNeed → flags)."""
    out = []
    for step in ladder:
        conds = step.condition.split("|")
        if any(c == "always" or flags.get(c, False) for c in conds):
            out.append(step)
    return out


def cd_submit_price(state: DealState, flags: dict[str, bool], today: date, sla: SlaDefaults,
                    ladder: list[ApprovalStep] = DEFAULT_APPROVAL_LADDER) -> TransitionResult:
    """КД задал цену по каждому варианту → определяем следующую ступень."""
    if state.status not in (DealStatus.CALC_READY, DealStatus.CD_APPROVAL, DealStatus.REWORK):
        raise WorkflowError("Цена назначается в статусе «Расчёт сформирован» / «На утверждении КД».")
    steps = required_steps(ladder, flags)
    nxt = [s for s in steps if s.role != Role.CD]
    if not nxt:
        return approve(state, today, sla, by=Role.CD)
    step = nxt[0]
    res = TransitionResult(status=step.status, audit=f"Требуется виза: {step.name}")
    res.tasks.append(TaskSpec(step.role, f"Виза: {step.name}", add_business_days(today, step.sla_days), "visa"))
    res.notifications.append(Notification("visa_required", [step.role], ["outlook", "wecom"], f"Требуется виза ({step.name})"))
    return res


def visa_decision(state: DealState, by: Role, approved: bool, flags: dict[str, bool], today: date, sla: SlaDefaults,
                  ladder: list[ApprovalStep] = DEFAULT_APPROVAL_LADDER, cd_insists: bool = False) -> TransitionResult:
    if state.status not in (DealStatus.FD_VISA, DealStatus.GD_VISA):
        raise WorkflowError("Сделка не на визе.")
    if not approved:
        if by == Role.FD and cd_insists:
            flags = dict(flags, fd_declined=True)
            step = next(s for s in ladder if s.role == Role.GD)
            res = TransitionResult(status=step.status, audit="ФД отказал, КД настаивает → виза ГД")
            res.tasks.append(TaskSpec(Role.GD, "Виза: Генеральная", add_business_days(today, step.sla_days), "visa"))
            res.notifications.append(Notification("visa_required", [Role.GD], ["outlook", "wecom"], "Требуется виза ГД"))
            return res
        return rework(state, "pricing", f"Виза отклонена ({by.value})", today, sla)
    # виза получена: есть ли следующая ступень?
    steps = required_steps(ladder, flags)
    order = [s.status for s in steps]
    idx = order.index(state.status) if state.status in order else len(order)
    for s in steps[idx + 1:]:
        res = TransitionResult(status=s.status, audit=f"Виза {by.value} получена, далее: {s.name}")
        res.tasks.append(TaskSpec(s.role, f"Виза: {s.name}", add_business_days(today, s.sla_days), "visa"))
        res.notifications.append(Notification("visa_required", [s.role], ["outlook", "wecom"], f"Требуется виза ({s.name})"))
        return res
    return approve(state, today, sla, by=by)


def approve(state: DealState, today: date, sla: SlaDefaults, by: Role) -> TransitionResult:
    if state.in_execution:
        # задача 2: повторная визация после изменения условий контракта → возврат на стадию контракта
        back = DealStatus.EXECUTION if state.execution_started else DealStatus.CONTRACT
        res = TransitionResult(status=back, new_version=True, audit=f"Изменённые условия контракта утверждены ({by.value})")
        res.notifications.append(Notification("approved", [Role.MANAGER], ["outlook", "wecom"], "Изменённые условия контракта утверждены"))
        return res
    res = TransitionResult(status=DealStatus.APPROVED, new_version=True, audit=f"Утверждено ({by.value})")
    res.tasks.append(TaskSpec(Role.MANAGER, "Отправить КП клиенту", add_business_days(today, sla.manager_rework_days), "kp"))
    res.notifications.append(Notification("approved", [Role.MANAGER], ["outlook", "wecom"], "Сделка утверждена, КП готово"))
    return res


def rework(state: DealState, block: str, reason: str, today: date, sla: SlaDefaults) -> TransitionResult:
    """Возврат на доработку (КД или ГД, на любом этапе). Создаёт новую версию расчёта."""
    if not reason:
        raise WorkflowError("Укажите причину возврата.")
    role = {"manager": Role.MANAGER, "logistics": Role.LOGISTICIAN, "service": Role.SERVICE_HEAD, "pricing": Role.CD}.get(block)
    if role is None:
        raise WorkflowError("Укажите блок доработки: manager / logistics / service / pricing.")
    days = {"manager": sla.manager_rework_days, "logistics": sla.logistics_days, "service": sla.service_days, "pricing": sla.cd_days}[block]
    res = TransitionResult(status=DealStatus.REWORK, new_version=True, audit=f"Возврат на доработку ({block}): {reason}")
    res.tasks.append(TaskSpec(role, f"Доработка: {reason}", add_business_days(today, days), block))
    res.notifications.append(Notification("rework", [role], ["outlook", "wecom", "app"], f"Возврат на доработку: {reason}"))
    return res


def price_changed_after_visa(state: DealState) -> Optional[TransitionResult]:
    """Изменение цены после визы сбрасывает визы и возвращает на ступень 1."""
    if state.status in (DealStatus.FD_VISA, DealStatus.GD_VISA, DealStatus.APPROVED):
        return TransitionResult(status=DealStatus.CD_APPROVAL, new_version=True, audit="Цена изменена после визы — визы сброшены")
    return None


def kp_sent(state: DealState) -> TransitionResult:
    if state.status != DealStatus.APPROVED:
        raise WorkflowError("КП отправляется только по утверждённой сделке.")
    return TransitionResult(status=DealStatus.KP_SENT, audit="КП отправлено")


def close(state: DealState, outcome: str, reason: str, today: Optional[date] = None, sla: Optional[SlaDefaults] = None) -> TransitionResult:
    """Исход сделки. «Выиграна» сразу переводит сделку в стадию «Заключение контракта» (задача 2)."""
    if state.status not in (DealStatus.KP_SENT, DealStatus.APPROVED):
        raise WorkflowError("Исход указывается после отправки КП.")
    status = {"won": DealStatus.CONTRACT, "lost": DealStatus.LOST, "cancelled": DealStatus.CANCELLED}.get(outcome)
    if status is None or not reason:
        raise WorkflowError("Укажите исход (won/lost/cancelled) и причину.")
    res = TransitionResult(status=status, audit=f"Исход: {outcome} — {reason}")
    if status == DealStatus.CONTRACT:
        today = today or date.today()
        sla = sla or SlaDefaults()
        res.tasks.append(TaskSpec(Role.MANAGER, "Заключить контракт: внести номер и дату подписания", add_business_days(today, 5), "contract"))
        res.notifications.append(Notification("won", [Role.FD], ["app", "outlook"], "Сделка выиграна: стадия заключения контракта, далее — факт платежей"))
        res.notifications.append(Notification("won", [Role.CD], ["app"], "Сделка выиграна: заключение контракта"))
    return res


# ------------------------------------------------------------------ стадия контракта и исполнения (задача 2)

def start_execution(state: DealState, contract_signed: bool, today: date) -> TransitionResult:
    if state.status != DealStatus.CONTRACT:
        raise WorkflowError("Перейти к исполнению можно только со стадии «Заключение контракта».")
    if not contract_signed:
        raise WorkflowError("Укажите номер контракта и дату подписания с клиентом.")
    res = TransitionResult(status=DealStatus.EXECUTION, audit="Контракт подписан — исполнение")
    res.notifications.append(Notification("execution", [Role.LOGISTICIAN, Role.SERVICE_HEAD, Role.FD], ["app", "outlook"],
                                          "Контракт подписан: начато исполнение, вносите фактические даты этапов"))
    return res


def contract_terms_changed(state: DealState, reason: str, today: date, sla: SlaDefaults) -> TransitionResult:
    """Условия контракта отличаются от утверждённого КП → новая версия расчёта, сброс виз, повторная визация с КД."""
    if state.status not in (DealStatus.CONTRACT, DealStatus.EXECUTION):
        raise WorkflowError("Изменение условий контракта фиксируется на стадии контракта или исполнения.")
    if not reason:
        raise WorkflowError("Укажите, что изменилось в условиях контракта.")
    res = TransitionResult(status=DealStatus.CD_APPROVAL, new_version=True, audit=f"Условия контракта изменены: {reason} — визы сброшены")
    res.tasks.append(TaskSpec(Role.CD, f"Повторное утверждение: {reason}", add_business_days(today, sla.cd_days), "pricing"))
    res.notifications.append(Notification("visa_required", [Role.CD], ["outlook", "wecom", "app"], "Условия контракта изменились — повторная визация"))
    return res


def close_execution(state: DealState, reason: str = "") -> TransitionResult:
    if state.status != DealStatus.EXECUTION:
        raise WorkflowError("Закрыть можно только сделку на исполнении.")
    return TransitionResult(status=DealStatus.CLOSED, audit=f"Сделка закрыта после исполнения{(': ' + reason) if reason else ''}")


def overdue(due: date, today: date) -> bool:
    return today > due
