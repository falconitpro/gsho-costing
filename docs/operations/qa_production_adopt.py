"""Репетиция переноса базы стенда на рабочий сервер (deploy/production/transfer.md). Только локальный PostgreSQL, синтетика.

Исходник — локальная тестовая база (как стенд: APP_ENV=dev, демо-данные, пароли, коды, сессии). Шаги как на сервере:
pg_dump → pg_restore в пустую базу → schema + SQL по порядку + reference (init-db.sh --existing) → adopt → admin → запуск в APP_ENV=prod.

    python3 docs/operations/qa_production_adopt.py [venv python] [исходная база, по умолчанию gsho]     # нужен clamd на 127.0.0.1:3310
"""
import base64
import glob
import json
import os
import re
import subprocess
import sys

PY = sys.argv[1] if len(sys.argv) > 1 else sys.executable
SRC = sys.argv[2] if len(sys.argv) > 2 else "gsho"
DB = "prod_adopt"
URL = f"postgresql+psycopg://gsho:Str0ngLocalPw@localhost:5432/{DB}"
GOOD = {**os.environ, "DATABASE_URL": URL, "APP_ENV": "prod", "FILE_SCAN": "clamav", "CLAMD_HOST": "127.0.0.1", "AUTH_PROVIDER": "password",
        "SECRET_KEY": "s" * 64, "MFA_ENCRYPTION_KEY": base64.b64encode(b"k" * 32).decode(), "STORAGE_LOCAL_ROOT": "/tmp/prod_adopt_storage"}
KEEP = ["users", "deals", "deal_versions", "positions", "clients", "factories", "parts", "attachments", "tasks", "audit_log", "access_rules",
        "contracts", "legal_profiles", "payment_schemes", "supply_routes", "hs_codes", "rate_settings", "glossary", "security_events"]
bad = 0


def ok(name, cond, extra=""):
    global bad
    print(("  ok   " if cond else "  FAIL ") + name + (f" — {extra}" if extra and not cond else ""))
    bad += not cond


def psql(db, *a):
    return subprocess.run(["psql", "-h", "localhost", "-U", "gsho", "-d", db, "-v", "ON_ERROR_STOP=1", "-q", *a], capture_output=True, text=True)


def run(*args):
    return subprocess.run([PY, "-m", *args], cwd="backend", env={**GOOD, "PYTHONPATH": "."}, capture_output=True, text=True)


def counts(db):
    q = " union all ".join(f"select '{t}', count(*) from {t}" for t in KEEP)
    return dict(line.split("|") for line in psql(db, "-Atc", q).stdout.split())


psql("postgres", "-c", f"drop database if exists {DB}")
psql("postgres", "-c", f"create database {DB}")
try:
    print("1. Копия базы стенда → пустая база")
    dump = subprocess.run(["pg_dump", "-h", "localhost", "-U", "gsho", "-d", SRC, "--format=custom", "--no-owner", "-f", "/tmp/prod_adopt.dump"], capture_output=True, text=True)
    ok("pg_dump", dump.returncode == 0, dump.stderr[-300:])
    r = subprocess.run(["pg_restore", "-h", "localhost", "-U", "gsho", "--no-owner", "--exit-on-error", "-d", DB, "/tmp/prod_adopt.dump"], capture_output=True, text=True)
    ok("pg_restore", r.returncode == 0, r.stderr[-300:])
    before = counts(DB)
    print("    строк до:", {k: v for k, v in before.items() if k in ("users", "deals", "parts", "attachments", "tasks", "audit_log")})
    psql("postgres", "-c", "alter role gsho password 'Str0ngLocalPw'")

    print("2. Довести схему (init-db.sh --existing)")
    ok("schema", run("scripts.prod_init", "schema").returncode == 0)
    os.environ["LC_ALL"] = "C"
    failed = [os.path.basename(f) for f in sorted(glob.glob("backend/migrations/*.sql")) if psql(DB, "-f", f).returncode]
    ok("SQL 001…019 поверх базы стенда", not failed, str(failed))
    ok("reference", run("scripts.prod_init", "reference").returncode == 0)

    print("3. Сброс паролей, кодов и служебных данных")
    r = run("scripts.prod_init", "adopt")
    ok("без --yes ничего не меняет", r.returncode == 0 and "Ничего не изменено" in r.stdout and counts(DB)["users"] == before["users"]
       and psql(DB, "-Atc", "select count(*) from users where password_hash is not null").stdout.strip() != "0", r.stdout[-300:] + r.stderr[-300:])
    r = run("scripts.prod_init", "adopt", "--yes")
    ok("adopt --yes", r.returncode == 0, r.stdout[-300:] + r.stderr[-400:])
    after = counts(DB)
    diff = {k: (before[k], after[k]) for k in KEEP if before[k] != after[k] and k not in ("audit_log", "payment_schemes", "supply_routes")}
    ok("сделки, справочники, сотрудники, права, файлы, журналы — без изменений", not diff, str(diff))
    ok("в журнале действий одна новая запись о переносе", int(after["audit_log"]) == int(before["audit_log"]) + 1)
    z = psql(DB, "-Atc", "select (select count(*) from users where password_hash is not null)||' '||(select count(*) from auth_sessions)||' '||"
                         "(select count(*) from mfa_credentials)||' '||(select count(*) from mfa_recovery_codes)||' '||(select count(*) from notifications)||' '||"
                         "(select count(*) from ai_messages)||' '||(select count(*) from access_review_campaigns)").stdout.strip()
    ok("паролей, сессий, кодов, уведомлений, диалогов, пересмотров — 0", z == "0 0 0 0 0 0 0", z)
    gd = psql(DB, "-Atc", "select email from users where roles::text like '%gd%' and is_active order by email limit 1").stdout.strip()
    other = psql(DB, "-Atc", f"select email from users where is_active and email <> '{gd}' order by email limit 1").stdout.strip()
    r = run("scripts.prod_init", "admin", "--email", gd, "--name", "не используется")
    m = re.search(r"показывается один раз\): (\S+)", r.stdout)
    ok("временный пароль существующему ЗГД, имя и роли не изменены", r.returncode == 0 and bool(m)
       and psql(DB, "-Atc", f"select display_name <> 'не используется' from users where email = '{gd}'").stdout.strip() == "t", r.stdout[-300:] + r.stderr[-300:])
    ok("check: готово к запуску", run("scripts.prod_init", "check").returncode == 0)

    print("4. Запуск в рабочем режиме")
    code = r'''
import json, sys
from fastapi.testclient import TestClient
from app.main import app
gd, pw, other = sys.argv[1:4]
out = {}
with TestClient(app, base_url="https://crm.example.test") as c:
    out["health"] = c.get("/api/health").json()
    for old in ("test2027", "berghaus-demo-2026"):
        out["old_" + old] = [c.post("/api/auth/password-login", json={"login": u, "password": old}).status_code for u in (gd, other)]
    out["dev_header"] = c.get("/api/auth/me", headers={"X-Dev-User": gd}).status_code
    out["login"] = c.post("/api/auth/password-login", json={"login": gd, "password": pw}).status_code
    out["change"] = c.post("/api/auth/password", json={"current_password": pw, "new_password": "Rehearsal-Pass-2026-x"}).status_code
    out["mfa_enabled"] = c.get("/api/auth/me").json()["security"]["mfa_enabled"]
    deals = c.get("/api/deals").json()
    deals = deals.get("items", deals) if isinstance(deals, dict) else deals
    out["deals"] = len(deals)
    out["deal_cards"] = sorted({c.get(f"/api/deals/{d['id']}").status_code for d in deals[:25]})
    out["users"] = len(c.get("/api/admin/users").json())
    from app.core.download_audit import walk_routes
    seen, pages = set(), {}
    for r in walk_routes(app.routes):
        path, methods = getattr(r, "path", ""), getattr(r, "methods", None) or set()
        if "GET" in methods and "{" not in path and path.startswith("/api/") and path not in seen and path not in ("/api/auth/login", "/api/auth/callback", "/api/auth/logout"):
            seen.add(path)
            try:
                pages[path] = c.get(path).status_code
            except Exception as e:  # noqa: BLE001
                pages[path] = "EXC " + type(e).__name__
    out["pages"] = len(pages)
    out["broken"] = {k: v for k, v in pages.items() if not isinstance(v, int) or v >= 500}
print("JSON" + json.dumps(out, ensure_ascii=False))
'''
    r = subprocess.run([PY, "-c", code, gd, m.group(1) if m else "", other], cwd="backend", env={**GOOD, "PYTHONPATH": "."}, capture_output=True, text=True)
    line = [x for x in r.stdout.splitlines() if x.startswith("JSON")]
    ok("backend стартовал на перенесённой базе", bool(line), r.stderr[-900:])
    o = json.loads(line[0][4:]) if line else {}
    print("   ", o)
    ok("рабочий режим", o.get("health", {}).get("env") == "prod" and o["health"].get("auth") == "password")
    ok("старые пароли стенда не подходят никому", all(set(v) <= {401, 429} for k, v in o.items() if k.startswith("old_")))
    ok("заголовок «Войти как» не действует", o.get("dev_header") == 401)
    ok("вход ЗГД по временному паролю и смена", o.get("login") == 200 and o.get("change") == 200)
    ok("код из приложения сброшен", o.get("mfa_enabled") is False)
    live = int(psql(DB, "-Atc", "select count(*) from deals where deleted_at is null and archived_at is null").stdout.strip())
    ok("все сделки в списке (без корзины и архива) и открываются", o.get("deals") == live and o.get("deal_cards") in ([200], []), f"{o.get('deals')} из {live}, {o.get('deal_cards')}")
    ok("все сотрудники на месте", o.get("users") == int(before["users"]), f"{o.get('users')} из {before['users']}")
    ok(f"{o.get('pages')} GET-маршрутов без ошибок сервера", not o.get("broken") and (o.get("pages") or 0) > 80, str(o.get("broken")))
    ok("отметка базы: production", psql(DB, "-Atc", "select value from app_markers where key = 'data_classification'").stdout.strip() == "production")
    r = run("scripts.prod_init", "adopt", "--yes")
    ok("повторный adopt на рабочей базе отклонён", r.returncode == 1 and "уже помечена как рабочая" in r.stdout)
finally:
    psql("postgres", "-c", "alter role gsho password 'gsho'")
    psql("postgres", "-c", f"drop database if exists {DB}")
    if os.path.exists("/tmp/prod_adopt.dump"):
        os.remove("/tmp/prod_adopt.dump")
print("ИТОГ:", "PASS" if not bad else f"FAIL ({bad})")
sys.exit(1 if bad else 0)
