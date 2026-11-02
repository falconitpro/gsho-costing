"""Миграция схемы вперёд без alembic: новые таблицы (create_all), новые столбцы (ALTER TABLE ADD COLUMN),
новые значения перечислений (ALTER TYPE ADD VALUE). Безопасна для повторного запуска.

Запуск: python -m scripts.migrate  (из каталога backend, с настроенным DATABASE_URL)
"""
from __future__ import annotations

from app.db import add_missing_columns, init_db


def run() -> None:
    init_db()
    added = add_missing_columns()
    print("migrate: ok" + (f", добавлены столбцы: {', '.join(added)}" if added else ""))


if __name__ == "__main__":
    run()
