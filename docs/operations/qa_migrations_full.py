"""Репетиция миграций в порядке рабочего сервера (этап 7, 11 §9). Только синтетические базы на локальном PostgreSQL.

A — «как на рабочем сервере»: схема моделей baseline-коммита 30c4390 (до пакета) + SQL 001…012, затем SQL пакета
013, 014, 015, 016, 016b, 017, 018, 019 — дважды подряд (повторный прогон должен быть безопасным).
B — эталон: схема моделей текущего main + SQL 001…019.
Сравниваются все таблицы: столбцы (тип, NULL, длина), индексы, ограничения, триггеры. Расхождение = недостающий ALTER в SQL.
MIG-03: до SQL пакета в A кладутся синтетические сотрудник, задача и вложение (моделями baseline); после — значения на месте,
новые столбцы получили значения по умолчанию, старое вложение помечено legacy_unscanned.

    python3 docs/operations/qa_migrations_full.py <путь к baseline worktree> [venv python]
"""
import json
import os
import subprocess
import sys

BASE, PY = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else sys.executable)
URL = "postgresql+psycopg://gsho:gsho@localhost:5432/"
PKG = ["013_backup_operations", "014_security_core", "015_access_review", "016_security_audit", "016b_file_scanning",
       "017_internal_support", "018_ai_assistant", "019_daily_briefing"]
PRE = [f[:-4] for f in sorted(os.listdir("backend/migrations")) if f.endswith(".sql") and f[:3] <= "012"]


def psql(db, *args, **kw):
    return subprocess.run(["psql", "-h", "localhost", "-U", "gsho", "-d", db, "-v", "ON_ERROR_STOP=1", "-q", *args], capture_output=True, text=True, **kw)


def create(db, backend_dir):
    psql("postgres", "-c", f"drop database if exists {db}")
    psql("postgres", "-c", f"create database {db}")
    code = "import app.models; from app.db import Base, engine; Base.metadata.create_all(engine)"
    r = subprocess.run([PY, "-c", code], cwd=backend_dir, env={**os.environ, "DATABASE_URL": URL + db, "PYTHONPATH": backend_dir, "APP_ENV": "dev"},
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]


def apply(db, names, tag):
    for n in names:
        r = psql(db, "-f", f"backend/migrations/{n}.sql")
        print(f"  {tag} {n}: {'OK' if r.returncode == 0 else 'FAIL ' + r.stderr.strip()[-300:]}")
        assert r.returncode == 0


SHAPE = """
select json_build_object(
 'columns', (select json_agg(x order by x) from (select table_name||'.'||column_name||' '||data_type||' '||is_nullable||' '||coalesce(character_maximum_length,0) x
             from information_schema.columns where table_schema='public') s),
 'indexes', (select json_agg(x order by x) from (select tablename||' '||regexp_replace(indexdef,'^CREATE (UNIQUE )?INDEX \\S+ ON \\S+ ','\\1') x
             from pg_indexes where schemaname='public') s),
 'constraints', (select json_agg(x order by x) from (select conrelid::regclass||' '||pg_get_constraintdef(oid) x from pg_constraint
             where connamespace='public'::regnamespace) s),
 'triggers', (select json_agg(x order by x) from (select tgrelid::regclass||' '||tgname x from pg_trigger where not tgisinternal) s))
"""


def shape(db):
    r = psql(db, "-At", "-c", SHAPE)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


print("A: baseline 30c4390 + 001…012 (рабочий сервер сейчас)")
create("mig_a", os.path.join(BASE, "backend"))
apply("mig_a", PRE, "pre")
SEED = """
from datetime import date
from sqlalchemy import text
from app.db import SessionLocal
from app import models as m
with SessionLocal() as s:
    u = m.User(id='mig-user', external_id='mig-user', email='mig@example.test', display_name='Синтетический', roles=['manager'], password_hash='x')
    s.add(u); s.flush()
    s.execute(text("set session_replication_role = replica"))   # сделки для синтетики не нужны: внешние ключи на время вставки отключены
    s.execute(m.Task.__table__.insert().values(id='mig-task', deal_id='mig-deal', role='manager', assignee_id='mig-user', title='Синтетическая задача',
              block='', due=date(2026, 12, 1), overdue_notified=False))
    s.execute(m.Attachment.__table__.insert().values(id='mig-att', deal_id='mig-deal', folder='05_kp', entity='deal_doc', category='other', comment='',
              file_name='a.pdf', storage_path='p', storage_url='u', sha256='0'*64, size=1))
    s.commit()
"""
r = subprocess.run([PY, "-c", SEED], cwd=os.path.join(BASE, "backend"), capture_output=True, text=True,
                   env={**os.environ, "DATABASE_URL": URL + "mig_a", "PYTHONPATH": os.path.join(BASE, "backend"), "APP_ENV": "dev"})
assert r.returncode == 0, r.stderr[-1500:]
print("A: синтетические данные до SQL пакета — сотрудник, задача, вложение")
print("A: SQL пакета, первый прогон")
apply("mig_a", PKG, "1st")
print("A: SQL пакета, повторный прогон")
apply("mig_a", PKG, "2nd")
print("B: модели текущего main + 001…019")
create("mig_b", "backend")
apply("mig_b", PRE + PKG, "ref")
a, b = shape("mig_a"), shape("mig_b")
bad = 0
for k in ("columns", "indexes", "constraints", "triggers"):
    sa, sb = set(a[k] or []), set(b[k] or [])
    only_a, only_b = sorted(sa - sb), sorted(sb - sa)
    print(f"{k}: A={len(sa)} B={len(sb)} " + ("совпадает" if not (only_a or only_b) else "РАСХОЖДЕНИЕ"))
    for x in only_b:
        print("   нет на рабочем после SQL:", x)
    for x in only_a:
        print("   лишнее на рабочем:", x)
    bad += len(only_a) + len(only_b)
CHECK = ("select (select email||'|'||roles::text||'|'||password_must_change||'|'||failed_logins||'|'||coalesce(offboarded_at::text,'-') from users where id='mig-user'),"
         " (select title||'|'||assignee_id from tasks where id='mig-task'), (select scan_status||'|'||file_name from attachments where id='mig-att')")
got = psql("mig_a", "-At", "-F", " ; ", "-c", CHECK).stdout.strip()
want = 'mig@example.test|["manager"]|false|0|- ; Синтетическая задача|mig-user ; legacy_unscanned|a.pdf'
print("MIG-03 данные после SQL:", got, "—", "сохранены" if got == want else "РАСХОЖДЕНИЕ, ожидалось " + want)
bad += got != want
print("ИТОГ:", "PASS" if bad == 0 else f"FAIL ({bad})")
sys.exit(1 if bad else 0)
