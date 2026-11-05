"""Раздел «Поставщики»: отметка, что поставщик поставляет запасные части или ГШО (добавляется при фактическом использовании)."""
from __future__ import annotations

from typing import Optional


def mark(db, factory_id: Optional[str], kind: str) -> None:
    from ..models import Factory
    f = db.get(Factory, factory_id) if factory_id else None
    if f is not None and kind not in (f.supplies or []):
        f.supplies = [*(f.supplies or []), kind]


def is_approved(f) -> bool:
    return f is not None and getattr(f, "approval_status", None) in (None, "approved")


def require_approved(f, what: str = "выбрать") -> None:
    """Неутверждённого поставщика нельзя выбрать в сделке, запросить у него цену или оформить заказ."""
    from fastapi import HTTPException
    if f is not None and not is_approved(f):
        state = "не утверждён" if f.approval_status == "rejected" else "ещё не утверждён"
        raise HTTPException(409, f"Поставщик «{f.short_name or f.name}» {state} КД или ЗГД — {what} нельзя. Утверждение — в разделе «Поставщики».")


def new_factory_status(user) -> dict:
    """Поставщик, заведённый автоматически (импорт RFQ, каталог): КД и ЗГД — сразу утверждён, остальные — на утверждении."""
    from datetime import datetime, timezone
    from ..core.roles import Role
    if user is not None and user.has(Role.CD, Role.GD):
        return {"approval_status": "approved", "approved_by": user.id, "approved_at": datetime.now(timezone.utc), "created_by": user.id}
    return {"approval_status": "pending", "created_by": getattr(user, "id", None)}
