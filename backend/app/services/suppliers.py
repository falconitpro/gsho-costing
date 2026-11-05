"""Раздел «Поставщики»: отметка, что поставщик поставляет запасные части или ГШО (добавляется при фактическом использовании)."""
from __future__ import annotations

from typing import Optional


def mark(db, factory_id: Optional[str], kind: str) -> None:
    from ..models import Factory
    f = db.get(Factory, factory_id) if factory_id else None
    if f is not None and kind not in (f.supplies or []):
        f.supplies = [*(f.supplies or []), kind]
