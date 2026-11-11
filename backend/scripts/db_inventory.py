"""Сводка по базе для планирования переноса со стенда на рабочий сервер. Только чтение.

    docker compose exec backend python -m scripts.db_inventory

Печатает число строк в каждой непустой таблице, список сотрудников (адрес, роли, активен, есть ли пароль и код) и
номера сделок и заявок со статусом и датой. Паролей, ключей, названий клиентов, сумм и содержимого документов в выводе нет.
"""
from __future__ import annotations

import sys

from sqlalchemy import inspect, text


def main() -> int:
    from app.db import engine
    with engine.connect() as c:
        tables = sorted(inspect(engine).get_table_names())
        print("== таблицы (непустые)")
        empty = 0
        for t in tables:
            n = c.execute(text(f'select count(*) from "{t}"')).scalar()
            if n:
                print(f"{t}\t{n}")
            else:
                empty += 1
        print(f"пустых таблиц: {empty} из {len(tables)}")
        mfa = "exists(select 1 from mfa_credentials m where m.user_id = u.id and m.enabled_at is not null)" if "mfa_credentials" in tables else "false"
        print("== сотрудники: адрес | роли | активен | пароль | код из приложения")
        for r in c.execute(text(f"select email, roles::text, is_active, password_hash is not null, {mfa} from users u order by email")):
            print(" | ".join(str(x) for x in r))
        print("== сделки ГШО: номер | статус | дата запроса")
        for r in c.execute(text("select number, status::text, requested_at from deals order by number")):
            print(" | ".join(str(x) for x in r))
        if "part_requests" in tables:
            pc = {x["name"] for x in inspect(engine).get_columns("part_requests")}
            st = "status::text" if "status" in pc else "'—'"
            day = "created_at::date" if "created_at" in pc else "null"
            print("== заявки на запчасти: номер | статус | дата")
            for r in c.execute(text(f"select number, {st}, {day} from part_requests order by 1")):
                print(" | ".join(str(x) for x in r))
        size = c.execute(text("select pg_size_pretty(pg_database_size(current_database()))")).scalar()
        print(f"== размер базы: {size}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
