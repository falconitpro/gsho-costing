"""Операции над сделкой: нумерация, журнал, задачи, уведомления, версии, папки хранилища."""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import adapters
from ..core.roles import Role
from ..core.workflow import SlaDefaults, TaskSpec, TransitionResult
from ..models import Approval, AuditLog, Deal, DealVersion, NotificationLog, Task, User
from . import settings_service
from .deal_calc import excel_meta, run_calc
from .excel_export import export_excel


def next_number(db: Session, year: int) -> str:
    n = db.scalar(select(func.count(Deal.id)).where(Deal.number.like(f"D-{year}-%"))) or 0
    return f"D-{year}-{n + 1:04d}"


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
    # закрываем открытые задачи того же блока
    for spec in tr.tasks:
        assignee = _assignee(db, deal, spec)
        db.add(Task(deal_id=deal.id, role=spec.role.value, assignee_id=assignee, title=spec.title, block=spec.block, due=spec.due))
        for n in tr.notifications:
            if spec.role in n.roles:
                notify(db, deal, assignee, n.event, n.channels, f"{deal.number}: {n.text}")
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


def notify(db: Session, deal: Deal | None, user_id: str | None, event: str, channels: list[str], text: str) -> None:
    user = db.get(User, user_id) if user_id else None
    allowed = set(user.notify_channels or []) if user else {"app"}
    for ch in channels:
        if ch not in allowed and ch != "app":
            continue
        row = NotificationLog(deal_id=deal.id if deal else None, user_id=user_id, event=event, channel=ch, text=text)
        try:
            if ch == "outlook" and user:
                from ..adapters.mail import OutgoingMail
                adapters.mail().send(OutgoingMail(to=[user.email], subject=text, body_html=f"<p>{text}</p>"))
            elif ch == "wecom" and user and user.wecom_user_id:
                adapters.messenger().notify([user.wecom_user_id], text)
            row.status = "sent"
        except Exception as e:   # интеграция недоступна — остаётся в очереди
            row.status, row.error = "failed", str(e)[:500]
        db.add(row)


def ensure_folders(db: Session, deal: Deal) -> None:
    if deal.storage_folder:
        return
    st = adapters.storage()
    folder = st.deal_folder(deal.client.name, deal.requested_at.year, deal.number, deal.site)
    st.ensure_deal_folders(folder)
    st.set_permissions(folder, "04_cost", [])   # менеджера нет в списке доступа к 04
    deal.storage_folder = folder


def create_version(db: Session, deal: Deal, reason: str, user_id: str | None, calc_date: date | None = None) -> DealVersion:
    """Фиксирует версию расчёта (вход + результат + курс) и формирует Excel в двух вариантах."""
    inp, res = run_calc(db, deal, calc_date)
    deal.current_version_no += 1
    approvals = [{"step": a.step, "role": a.role, "user": a.user_id, "at": a.at.isoformat() if a.at else "", "decision": "да" if a.approved else "нет",
                  "comment": a.comment} for a in db.scalars(select(Approval).where(Approval.deal_id == deal.id, Approval.reset_at.is_(None))).all()]
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
