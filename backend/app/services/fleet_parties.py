"""Стороны по машине (D-186, K-3): собственник, лизингодатель, эксплуатант с датами; одна главная действующая сторона,
её клиент = client_machines.client_id (на него — сервис и счета). Передача машины = закрыть прежнюю сторону и
открыть новую; закрытые записи остаются историей."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import ClientMachine, ClientSite, MachineParty

ROLES = {"owner": "Собственник", "lessor": "Лизингодатель", "operator": "Эксплуатант", "other": "Другое"}


def active(today: Optional[date] = None):
    t = today or date.today()
    return or_(MachineParty.valid_to.is_(None), MachineParty.valid_to >= t)


def ensure_primary(db: Session, m: ClientMachine) -> MachineParty:
    """У машины без сторон (заведена до 056 или создана другим путём) — главная сторона «собственник» = её клиент."""
    cur = db.scalars(select(MachineParty).where(MachineParty.machine_id == m.id, MachineParty.is_primary.is_(True), active())).first()
    if cur:
        return cur
    if m.id is None:
        db.flush()
    p = MachineParty(machine_id=m.id, client_id=m.client_id, role="owner", valid_from=m.commissioned_on, is_primary=True)
    db.add(p)
    db.flush()
    return p


def parties(db: Session, machine_id: str) -> list[MachineParty]:
    return list(db.scalars(select(MachineParty).where(MachineParty.machine_id == machine_id)
                           .order_by(MachineParty.valid_to.is_not(None), MachineParty.is_primary.desc(), MachineParty.valid_from.desc().nulls_last())).all())


def set_primary(db: Session, m: ClientMachine, p: MachineParty) -> None:
    """Сделать сторону главной: на её клиента — сервис и счета. Площадка чужого клиента снимается."""
    for o in db.scalars(select(MachineParty).where(MachineParty.machine_id == m.id, MachineParty.is_primary.is_(True), MachineParty.id != p.id)).all():
        o.is_primary = False
    p.is_primary = True
    if m.client_id != p.client_id:
        m.client_id = p.client_id
        if m.site_id:
            site = db.get(ClientSite, m.site_id)
            if site is None or site.client_id != p.client_id:
                m.site_id = None


def transfer_client(db: Session, m: ClientMachine, new_client_id: str, on: Optional[date] = None) -> None:
    """Смена клиента машины прежней формой («Клиент» в карточке машины) = передача: прежняя главная сторона закрывается
    вчерашним днём, новая открывается с сегодняшнего с той же ролью."""
    on = on or date.today()
    cur = ensure_primary(db, m)
    if cur.client_id == new_client_id:
        return
    cur.is_primary = False
    if cur.valid_to is None or cur.valid_to >= on:
        cur.valid_to = max(on - timedelta(days=1), cur.valid_from or on - timedelta(days=1))
    db.add(MachineParty(machine_id=m.id, client_id=new_client_id, role=cur.role, valid_from=on, is_primary=True, comment="передача: смена клиента машины"))
    db.flush()
