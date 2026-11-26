"""D-179: схема рабочей базы не расходится с кодом.

Берётся образец схемы рабочего сервера (schema/prod_baseline.sql, сверен с сервером по отпечаткам),
на него по порядку накатываются SQL-файлы migrations/, которые новее образца, — ровно как это делает update.sh.
Результат должен совпасть с моделями кода. Если модель изменили, а SQL-файл не приложили (или он неполный),
тест падает ещё в PR, до рабочего сервера.
"""
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "schema" / "prod_baseline.sql"
MIGRATIONS = ROOT / "migrations"


def _last_in_baseline() -> int:
    m = re.search(r"baseline-last-migration:\s*(\d+)", BASELINE.read_text()[:2000])
    assert m, "в образце нет отметки baseline-last-migration"
    return int(m.group(1))


def _psql(url, *args, stdin=None):
    env = {**os.environ, "PGPASSWORD": url.password or ""}
    cmd = ["psql", "-h", url.host or "localhost", "-p", str(url.port or 5432), "-U", url.username,
           "-d", url.database, "-v", "ON_ERROR_STOP=1", "-q", *args]
    r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, f"psql {' '.join(args)}: {r.stderr[-2000:]}"


# в CI не пропускается: без psql тест упадёт, а не тихо пропустится
@pytest.mark.skipif(not shutil.which("psql") and not os.environ.get("CI"), reason="нет psql (локально)")
def test_prod_baseline_plus_new_migrations_matches_code():
    from scripts.schema_check import collect

    base = make_url(os.environ["DATABASE_URL"])
    name = f"prodschema_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = base.set(database=name)
    eng = create_engine(url)
    try:
        _psql(url, "-f", str(BASELINE))
        last = _last_in_baseline()
        newer = sorted(p for p in MIGRATIONS.glob("*.sql") if int(re.match(r"\d+", p.name).group()) > last)
        for p in newer:
            _psql(url, stdin=p.read_text())
        r = collect(eng)
        problems = {k: r[k] for k in ("missing_tables", "missing_columns", "type_diffs", "null_diffs", "missing_fks",
                                       "missing_uniques", "missing_indexes", "extra_tables", "extra_columns") if r[k]}
        assert not problems, (
            "Схема рабочей базы после SQL-файлов " + (", ".join(p.name for p in newer) or "(новых нет)")
            + " не совпадает с моделями кода. Добавьте SQL-файл в backend/migrations: " + repr(problems))
    finally:
        eng.dispose()
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_baseline_not_in_migrations_dir():
    # update.sh применяет всё новое под backend/migrations — образец туда попадать не должен
    assert not list(MIGRATIONS.rglob("*baseline*"))
