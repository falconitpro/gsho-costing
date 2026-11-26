"""Сверка схемы базы с моделями кода — ТОЛЬКО ЧТЕНИЕ (Пакет 0, шаг 4: «перечислить расхождения; ничего не исправлять»).

Сравнивает таблицы, столбцы (тип, NULL), первичные ключи, внешние ключи, уникальные ограничения и индексы
моделей SQLAlchemy (app.models) с фактической схемой подключённой базы (DATABASE_URL). Ничего не создаёт
и не меняет, данные не читает — только системный каталог PostgreSQL.

Запуск на рабочем сервере (вывод — в консоль; секретов и данных в нём нет):
  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend python -m scripts.schema_check
Параметры: --json — машиночитаемый отчёт; --short — только итоги.
Код выхода: 0 — расхождений нет; 1 — есть расхождения (это не ошибка программы, а результат сверки).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

from sqlalchemy import inspect

from app.db import Base, engine


def _type(t) -> str:
    s = str(t).lower()
    for a, b in (("character varying", "varchar"), ("timestamp with time zone", "timestamptz"), ("timestamp without time zone", "timestamp"),
                 ("double precision", "float"), ("double_precision", "float"), ("boolean", "bool"), ("integer", "int"), ("bigint", "bigint")):
        s = s.replace(a, b)
    if s.startswith("varchar") or s in ("text", "string"):
        return s
    if s in ("json", "jsonb"):
        return "json"
    if "numeric" in s:
        return s.replace(" ", "")
    return s.split("(")[0]


def collect(eng=None) -> dict:
    from app import models  # noqa: F401  — регистрирует все модели
    insp = inspect(eng or engine)
    db_tables = set(insp.get_table_names())
    orm = Base.metadata.tables
    out: dict = {"missing_tables": [], "extra_tables": sorted(db_tables - set(orm) - {"alembic_version"}),
                 "missing_columns": [], "extra_columns": [], "type_diffs": [], "null_diffs": [],
                 "missing_fks": [], "missing_uniques": [], "missing_indexes": [], "counts": {}}
    for name, t in sorted(orm.items()):
        if name not in db_tables:
            out["missing_tables"].append(name)
            continue
        cols = {c["name"]: c for c in insp.get_columns(name)}
        for c in t.columns:
            dc = cols.get(c.name)
            if dc is None:
                out["missing_columns"].append(f"{name}.{c.name}")
                continue
            ot, dt = _type(c.type), _type(dc["type"])
            if ot != dt and not ({ot, dt} <= {"json"} or (ot.startswith("enum") or dt.startswith("enum") or "enum" in str(type(c.type)).lower())
                                 or (ot == "varchar" and dt.startswith("varchar")) or (ot.startswith("varchar") and dt.startswith("varchar"))
                                 or (ot == "datetime" and dt in ("timestamptz", "timestamp")) or (ot == "float" and dt in ("float", "real"))):
                out["type_diffs"].append(f"{name}.{c.name}: код {ot} — база {dt}")
            if not c.primary_key and bool(c.nullable) != bool(dc["nullable"]):
                out["null_diffs"].append(f"{name}.{c.name}: код {'NULL' if c.nullable else 'NOT NULL'} — база {'NULL' if dc['nullable'] else 'NOT NULL'}")
        for extra in sorted(set(cols) - {c.name for c in t.columns}):
            out["extra_columns"].append(f"{name}.{extra}")
        db_fks = {(tuple(f["constrained_columns"]), f["referred_table"]) for f in insp.get_foreign_keys(name)}
        for fk in t.foreign_key_constraints:
            key = (tuple(c.name for c in fk.columns), fk.referred_table.name)
            if key not in db_fks:
                out["missing_fks"].append(f"{name}({', '.join(key[0])}) → {key[1]}")
        db_uniq = {tuple(sorted(u["column_names"])) for u in insp.get_unique_constraints(name)}
        db_idx = {tuple(sorted(x for x in i["column_names"] if x)): i.get("unique", False) for i in insp.get_indexes(name) if any(i.get("column_names") or [])}
        pk = tuple(sorted(c.name for c in t.primary_key.columns))
        for c in t.columns:
            if c.unique and (c.name,) not in db_uniq and not db_idx.get((c.name,)):
                out["missing_uniques"].append(f"{name}.{c.name}")
        from sqlalchemy import UniqueConstraint
        for cons in t.constraints:
            if isinstance(cons, UniqueConstraint) and len(cons.columns) > 1:
                k = tuple(sorted(c.name for c in cons.columns))
                if k not in db_uniq and not db_idx.get(k):
                    out["missing_uniques"].append(f"{name}({', '.join(k)})")
        for ix in t.indexes:
            k = tuple(sorted(c.name for c in ix.columns))
            if k and k not in db_idx and k not in db_uniq and k != pk:
                out["missing_indexes"].append(f"{name}({', '.join(k)})" + (" UNIQUE" if ix.unique else ""))
        for c in t.columns:
            if c.index and (c.name,) not in db_idx and (c.name,) not in db_uniq and (c.name,) != pk:
                s = f"{name}({c.name})"
                if s not in out["missing_indexes"]:
                    out["missing_indexes"].append(s)
    out["counts"] = {"orm_tables": len(orm), "db_tables": len(db_tables), "orm_columns": sum(len(t.columns) for t in orm.values()),
                     "orm_fks": sum(len(t.foreign_key_constraints) for t in orm.values())}
    return out


def main(argv: list[str]) -> int:
    r = collect()
    if "--json" in argv:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        c = r["counts"]
        print(f"== сверка схемы: таблиц в коде {c['orm_tables']}, в базе {c['db_tables']}; столбцов в коде {c['orm_columns']}; внешних ключей в коде {c['orm_fks']}")
        titles = [("missing_tables", "нет в базе таблиц"), ("missing_columns", "нет в базе столбцов"), ("type_diffs", "типы различаются"),
                  ("null_diffs", "NULL/NOT NULL различается"), ("missing_fks", "нет в базе внешних ключей"), ("missing_uniques", "нет в базе уникальности"),
                  ("missing_indexes", "нет в базе индексов"), ("extra_tables", "лишние таблицы в базе (нет в коде)"), ("extra_columns", "лишние столбцы в базе (нет в коде)")]
        for k, t in titles:
            print(f"{t}: {len(r[k])}")
            if "--short" not in argv:
                for x in r[k][:200]:
                    print(f"   {x}")
    bad = sum(len(r[k]) for k in ("missing_tables", "missing_columns", "type_diffs", "missing_fks", "missing_uniques"))
    print("ИТОГ:", "расхождений, влияющих на работу, нет" if not bad else f"есть расхождения: {bad} (ничего не исправлено — разбор вручную)")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
