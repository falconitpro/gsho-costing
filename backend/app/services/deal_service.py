"""Операции над сделкой: нумерация, журнал, задачи, уведомления, версии, папки хранилища."""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from .. import adapters
from ..core.config import get_settings
from ..core.roles import Role
from ..core.workflow import SlaDefaults, TaskSpec, TransitionResult
from ..models import Approval, AuditLog, Deal, DealVersion, NotificationLog, Task, User
from . import settings_service
from .deal_calc import excel_meta, run_calc
from .excel_export import export_excel


def next_number(db: Session, year: int) -> str:
    # следующий номер = максимальный основной номер года + 1; дубли (D-2026-0003-2, задача 9) не учитываются,
    # пропуски в нумерации (например, после старой логики) не приводят к повтору номера
    import re
    prefix = f"D-{year}-"
    pat = re.compile(rf"^D-{year}-(\d+)$")
    nums = [int(m.group(1)) for (num,) in db.execute(select(Deal.number).where(Deal.number.like(f"{prefix}%"))).all() if (m := pat.match(num or ""))]
    return f"{prefix}{(max(nums) if nums else 0) + 1:04d}"


def audit(db: Session, user_id: str | None, deal_id: str | None, entity: str, entity_id: str, action: str,
          before: Any = None, after: Any = None, comment: str = "") -> None:
    from fastapi.encoders import jsonable_encoder   # даты и Decimal в JSONB (плановая дата CMR в партии и т. п.)
    db.add(AuditLog(user_id=user_id, deal_id=deal_id, entity=entity, entity_id=entity_id, action=action,
                    before=jsonable_encoder(before), after=jsonable_encoder(after), comment=comment))


def sla(db: Session) -> SlaDefaults:
    d = settings_service.get(db, "sla_days")
    return SlaDefaults(logistics_days=d["logistics"], service_days=d["service"], cd_days=d["cd"], fd_days=d["fd"],
                       gd_days=d["gd"], manager_rework_days=d["manager_rework"])


def _assignee(db: Session, deal: Deal, spec: TaskSpec) -> str | None:
    by_role = {Role.MANAGER: deal.manager_id, Role.LOGISTICIAN: deal.logistician_id, Role.SERVICE_HEAD: deal.service_head_id}
    if spec.assignee_hint:
        return spec.assignee_hint
    if spec.role in by_role and by_role[spec.role]:
        return by_role[spec.role]
    # первый активный пользователь с ролью (в проде — правило назначения в настройках)
    for u in db.scalars(select(User).where(User.is_active.is_(True))).all():
        if spec.role.value in (u.roles or []):
            return u.id
    return None


def apply_transition(db: Session, deal: Deal, tr: TransitionResult, user_id: str | None) -> None:
    before = deal.status.value
    deal.status = tr.status
    audit(db, user_id, deal.id, "deal", deal.id, "status", {"status": before}, {"status": tr.status.value}, tr.audit)
    if deal.approved_version_id:   # «Финансы»: прогноз утверждённого расчёта — по статусу сделки, в той же транзакции (D-126)
        from .finance.sources import sync_calculation
        db.flush()
        sync_calculation(db, deal, user_id)
    # закрываем открытые задачи того же блока
    for spec in tr.tasks:
        assignee = _assignee(db, deal, spec)
        db.add(Task(deal_id=deal.id, role=spec.role.value, assignee_id=assignee, title=spec.title, block=spec.block, due=spec.due))
        # уведомление о задаче отправляется автоматически при её создании (_task_notifications)
    for n in tr.notifications:
        if not any(n.roles and t.role in n.roles for t in tr.tasks):
            for role in n.roles:
                for u in db.scalars(select(User).where(User.is_active.is_(True))).all():
                    if role.value in (u.roles or []):
                        notify(db, deal, u.id, n.event, n.channels, f"{deal.number}: {n.text}")


def complete_tasks(db: Session, deal: Deal, block: str | None = None, role: str | None = None) -> None:
    for t in db.scalars(select(Task).where(Task.deal_id == deal.id, Task.done_at.is_(None))).all():
        if (block is None or t.block == block) and (role is None or t.role == role):
            t.done_at = datetime.now(timezone.utc)


# каналы уведомлений исполнителя (выбирают КД, ЗГД или администратор в карточке сотрудника)
CHANNELS = {"app": "в системе", "email": "e-mail", "wecom": "WeCom", "wechat": "WeChat", "telegram": "Telegram", "whatsapp": "WhatsApp",
            "max": "MAX", "sms": "SMS"}


def _channels_of(user: User | None) -> list[str]:
    chs = [("email" if c == "outlook" else c) for c in ((user.notify_channels if user else None) or ["app"])]
    return list(dict.fromkeys(["app", *chs]))


def notify(db: Session, deal: Deal | None, user_id: str | None, event: str, channels: list[str] | None, text: str) -> None:
    """Уведомление исполнителю — во все каналы, выбранные в его карточке (e-mail, мессенджеры, в системе).
    Канал без подключённой интеграции записывается в журнал со статусом «ожидает подключения»."""
    user = db.get(User, user_id) if user_id else None
    # заместитель на период получает уведомления замещаемого
    if user is not None and user.deputy_id and user.deputy_id != user_id:
        from datetime import date as _date
        today = _date.today()
        if (user.deputy_from is None or user.deputy_from <= today) and (user.deputy_to is None or user.deputy_to >= today):
            dep = db.get(User, user.deputy_id)
            if dep is not None and dep.is_active:
                notify(db, deal, dep.id, event, channels, f"{text} (за {user.display_name})")
    contacts = (user.contacts or {}) if user else {}
    for ch in _channels_of(user):
        row = NotificationLog(deal_id=deal.id if deal else None, user_id=user_id, event=event, channel=ch, text=text)
        try:
            if ch == "app":
                row.status = "sent"
            elif ch == "email" and user:
                from ..adapters.mail import OutgoingMail
                to = [a for a in (user.email, contacts.get("extra_email")) if a]
                base = (get_settings().cors_origins or [""])[0].rstrip("/")
                link = f'<p><a href="{base}/deals/{deal.id}">Открыть сделку {deal.number}</a></p>' if deal and base else ""
                adapters.mail().send(OutgoingMail(to=to, subject=f"[ГШО] {text[:120]}", body_html=f"<p>{text}</p>{link}"))
                row.status = "sent" if get_settings().mail_provider != "console" else "queued"
                if row.status == "queued":
                    row.error = "почта не подключена"
            elif ch == "wecom" and user and user.wecom_user_id:
                adapters.messenger().notify([user.wecom_user_id], text)
                row.status = "sent" if get_settings().messenger_provider != "console" else "queued"
                if row.status == "queued":
                    row.error = "WeCom не подключён"
            else:
                address = {"wechat": contacts.get("wechat"), "telegram": contacts.get("telegram"), "whatsapp": contacts.get("whatsapp") or contacts.get("mobile"),
                           "max": contacts.get("max") or contacts.get("mobile"), "sms": contacts.get("mobile"), "wecom": None}.get(ch)
                row.status, row.error = "queued", ("канал не подключён" if address else "в карточке нет контакта для канала")
        except Exception as e:   # интеграция недоступна — остаётся в очереди
            row.status, row.error = "failed", str(e)[:500]
        db.add(row)


def notify_new_task(db: Session, task: Task) -> None:
    """Постановка задачи: уведомление исполнителю (или всем активным с ролью задачи, если исполнитель не назначен)."""
    deal = db.get(Deal, task.deal_id) if task.deal_id else None
    text = f"Новая задача{f' по сделке {deal.number}' if deal else ''}: {task.title}" + (f" (срок {task.due:%d.%m.%Y})" if task.due else "")
    if task.assignee_id:
        notify(db, deal, task.assignee_id, "task", None, text)
        return
    for u in db.scalars(select(User).where(User.is_active.is_(True))).all():
        if task.role in (u.roles or []):
            notify(db, deal, u.id, "task", None, text)


@event.listens_for(Session, "before_flush")
def _task_notifications(session: Session, flush_context, instances) -> None:
    """Любая новая задача (из переходов статуса, исполнения, каталога, опций РФ) — сразу уведомление исполнителю."""
    for obj in list(session.new):
        if isinstance(obj, Task) and not getattr(obj, "_notified", False):
            obj._notified = True
            notify_new_task(session, obj)


def check_overdue_tasks(db: Session, today: date | None = None) -> int:
    """Просроченные задачи: уведомление исполнителю (или всем с ролью задачи), КД и ЗГД — один раз на задачу."""
    from ..core.workflow import overdue, role_title
    today = today or date.today()
    n = 0
    users = db.scalars(select(User).where(User.is_active.is_(True))).all()
    for t in db.scalars(select(Task).where(Task.done_at.is_(None), Task.overdue_notified.is_(False))).all():
        if not t.due or not overdue(t.due, today):
            continue
        deal = db.get(Deal, t.deal_id) if t.deal_id else None
        prefix = f"{deal.number}: " if deal else ""
        text = f"{prefix}просрочена задача «{t.title}» (срок {t.due:%d.%m.%Y})"
        if t.assignee_id:
            notify(db, deal, t.assignee_id, "task_overdue", None, text)
        else:
            for u in users:
                if t.role in (u.roles or []):
                    notify(db, deal, u.id, "task_overdue", None, text)
        who = next((u.display_name for u in users if u.id == t.assignee_id), "") or role_title(t.role)
        for u in users:   # руководству — КД и ЗГД
            if ({"cd", "gd"} & set(u.roles or [])) and u.id != t.assignee_id:
                notify(db, deal, u.id, "task_overdue", None, f"{text} — исполнитель: {who}")
        t.overdue_notified = True
        n += 1
    return n


def ensure_folders(db: Session, deal: Deal) -> None:
    if deal.storage_folder:
        return
    st = adapters.storage()
    folder = st.deal_folder(deal.client.name, deal.requested_at.year, deal.number, "")
    st.ensure_deal_folders(folder)
    st.set_permissions(folder, "04_cost", [])   # менеджера нет в списке доступа к 04
    deal.storage_folder = folder


STEP_TITLES = {"commercial": "Коммерческая", "financial": "Финансовая", "general": "ЗГД"}


def version_approvals(db: Session, deal: Deal, before=None) -> list[dict]:
    """Визы для листа «Визы»: ступень, роль и сотрудник по-русски, дата ДД.ММ.ГГГГ."""
    names = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    q = select(Approval).where(Approval.deal_id == deal.id)
    q = q.where(Approval.at <= before) if before is not None else q.where(Approval.reset_at.is_(None))
    out = []
    for a in db.scalars(q.order_by(Approval.at)).all():
        if before is not None and a.reset_at is not None and a.reset_at <= before:
            continue
        out.append({"step": STEP_TITLES.get(a.step, a.step), "role": a.role, "user": names.get(a.user_id, a.user_id or ""),
                    "at": a.at.strftime("%d.%m.%Y %H:%M") if a.at else "", "decision": "согласовано" if a.approved else "отказ", "comment": a.comment})
    return out


def rebuild_version_excel(db: Session, ver: DealVersion, manager: bool) -> str | None:
    """Excel версии заново по сохранённому входу и результату расчёта (те же цифры) текущим форматом выгрузки —
    файлы прежних версий скачиваются уже с исправленными подписями. Возвращает путь к файлу или None."""
    from ..calc.types import DealCalcInput, DealCalcResult
    deal = db.get(Deal, ver.deal_id)
    if deal is None or not ver.calc_input or not ver.calc_result:
        return None
    inp, res = DealCalcInput.model_validate(ver.calc_input), DealCalcResult.model_validate(ver.calc_result)
    meta = excel_meta(deal, ver.version_no, version_approvals(db, deal, before=ver.created_at))
    tmp = tempfile.mkdtemp()
    name = f"{deal.number}_расчёт_v{ver.version_no}{'_менеджер' if manager else ''}.xlsx"
    return export_excel(inp, res, os.path.join(tmp, name), meta, manager_version=manager)


def create_version(db: Session, deal: Deal, reason: str, user_id: str | None, calc_date: date | None = None) -> DealVersion:
    """Фиксирует версию расчёта (вход + результат + курс) и формирует Excel в двух вариантах."""
    inp, res = run_calc(db, deal, calc_date)
    deal.current_version_no += 1
    approvals = version_approvals(db, deal)
    ver = DealVersion(deal_id=deal.id, version_no=deal.current_version_no, reason=reason,
                      calc_input=inp.model_dump(mode="json"), calc_result=res.model_dump(mode="json"), created_by=user_id)
    db.add(ver)
    db.flush()
    try:
        ensure_folders(db, deal)
        meta = excel_meta(deal, ver.version_no, approvals)
        st = adapters.storage()
        with tempfile.TemporaryDirectory() as tmp:
            full = export_excel(inp, res, os.path.join(tmp, f"{deal.number}_расчёт_v{ver.version_no}.xlsx"), meta)
            mgr = export_excel(inp, res, os.path.join(tmp, f"{deal.number}_расчёт_v{ver.version_no}_менеджер.xlsx"), meta, manager_version=True)
            ver.excel_path = st.upload(full, deal.storage_folder, "04_cost", os.path.basename(full)).path
            ver.excel_manager_path = st.upload(mgr, deal.storage_folder, "01_rfq", os.path.basename(mgr)).path
    except Exception as e:   # хранилище недоступно — версия всё равно сохраняется
        audit(db, user_id, deal.id, "version", ver.id, "excel_failed", comment=str(e)[:500])
    audit(db, user_id, deal.id, "version", ver.id, "create", after={"version_no": ver.version_no, "reason": reason})
    return ver
