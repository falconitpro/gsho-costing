"""D-209: кто может видеть кадровый приказ с оплатой труда — только с доступом к оплате труда «Персонала» (ЗГД, ФД, бухгалтер по зарплате
по умолчанию). Исключение — сам работник, которого знакомят с его приказом (лист ознакомления)."""
from __future__ import annotations

from typing import Iterable, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import User


def has_pay_access(db: Session, user: User) -> bool:
    from .access_service import perms_for
    return perms_for(db, user).get("hr_payroll", 0) >= 1


def pay_viewers_ok(db: Session, ids: Iterable[str], allow: Optional[set[str]] = None, what: str = "видеть приказ с оплатой труда") -> None:
    bad = [u.display_name for u in db.scalars(select(User).where(User.id.in_(list(ids))))
           if u.id not in (allow or set()) and not has_pay_access(db, u)]
    if bad:
        raise HTTPException(422, f"Приказ содержит оплату труда — {what} могут только ЗГД, ФД и бухгалтер по зарплате "
                                 f"(доступ к оплате труда «Персонала»). Нельзя: {', '.join(bad)}")
