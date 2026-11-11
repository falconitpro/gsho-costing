"""Репетиция первого запуска рабочей системы на пустой базе (deploy/production/install.md). Только локальный PostgreSQL, синтетика.

1. Рабочий режим с настройками «из репозитория» не запускается — названа каждая причина.
2. Пустая база → prod_init schema → SQL по порядку → reference → admin.
3. Backend стартует в APP_ENV=prod; вход по временному паролю, обязательная смена, тестовых входов нет, экраны на пустой базе отвечают.

    python3 docs/operations/qa_production_init.py [venv python]     # нужен clamd на 127.0.0.1:3310
"""
import base64
import glob
import os
import re
import subprocess
import sys

PY = sys.argv[1] if len(sys.argv) > 1 else sys.executable
DB = "prod_rehearsal"
URL = f"postgresql+psycopg://gsho:Str0ngLocalPw@localhost:5432/{DB}"
BASE = {**os.environ, "DATABASE_URL": URL, "PYTHONPATH": "backend", "APP_ENV": "prod", "FILE_SCAN": "clamav", "CLAMD_HOST": "127.0.0.1",
        "STORAGE_LOCAL_ROOT": "/tmp/prod_rehearsal_storage", "QUARANTINE_DIR": "/tmp/prod_rehearsal_quarantine"}
GOOD = {**BASE, "AUTH_PROVIDER": "password", "SECRET_KEY": "s" * 64, "MFA_ENCRYPTION_KEY": base64.b64encode(b"k" * 32).decode()}
bad = 0


def ok(name, cond, extra=""):
    global bad
    print(("  ok   " if cond else "  FAIL ") + name + (f" — {extra}" if extra and not cond else ""))
    bad += not cond


def psql(db, *a, **kw):
    return subprocess.run(["psql", "-h", "localhost", "-U", "gsho", "-d", db, "-v", "ON_ERROR_STOP=1", "-q", *a], capture_output=True, text=True, **kw)


def run(env, *args):
    return subprocess.run([PY, "-m", *args], cwd="backend", env={**env, "PYTHONPATH": "."}, capture_output=True, text=True)


psql("postgres", "-c", f"drop database if exists {DB}")
psql("postgres", "-c", f"create database {DB}")
psql("postgres", "-c", "alter role gsho password 'Str0ngLocalPw'")   # локальная роль песочницы; в конце возвращается

try:
    print("1. Небезопасные настройки")
    r = run({**BASE, "AUTH_PROVIDER": "dev", "DATABASE_URL": URL.replace("Str0ngLocalPw", "gsho")}, "scripts.prod_init", "check")
    for needle in ("AUTH_PROVIDER=dev", "SECRET_KEY", "MFA_ENCRYPTION_KEY", "пароль базы"):
        ok(f"названо: {needle}", needle in r.stdout, r.stdout[-400:])
    r = run({**GOOD, "AUTH_PROVIDER": "m365"}, "scripts.prod_init", "check")
    ok("неизвестный способ входа отклонён", "AUTH_PROVIDER=m365" in r.stdout)

    print("2. Пустая база")
    r = run(GOOD, "scripts.prod_init", "check")
    ok("пустая база: не готово", r.returncode == 1 and "нет таблицы users" in r.stdout)
    r = subprocess.run([PY, "-c", "from fastapi.testclient import TestClient\nfrom app.main import app\nwith TestClient(app): pass"],
                       cwd="backend", env={**GOOD, "PYTHONPATH": "."}, capture_output=True, text=True)
    ok("backend на пустой базе не стартует", r.returncode != 0)
    r = run(GOOD, "scripts.prod_init", "schema")
    ok("schema", r.returncode == 0, r.stderr[-400:])
    os.environ["LC_ALL"] = "C"
    files = sorted(glob.glob("backend/migrations/*.sql"))
    ok("порядок: 016 раньше 016b", [os.path.basename(f)[:4] for f in files].index("016_") < [os.path.basename(f)[:4] for f in files].index("016b"))
    for f in files:
        r = psql(DB, "-f", f)
        if r.returncode:
            ok(os.path.basename(f), False, r.stderr[-300:])
    ok(f"SQL: {len(files)} файлов", True)
    r = run(GOOD, "scripts.prod_init", "reference")
    ok("reference", r.returncode == 0, r.stderr[-400:])
    r = run(GOOD, "scripts.prod_init", "reference")
    ok("reference повторно", r.returncode == 0)
    n = psql(DB, "-Atc", "select (select count(*) from users)||' '||(select count(*) from clients)||' '||(select count(*) from factories)||' '||"
                         "(select count(*) from payment_schemes)||' '||(select count(*) from legal_entities)||' '||(select count(*) from supply_routes)").stdout.strip()
    ok("демо-данных нет: 0 сотрудников, 0 клиентов, 0 заводов; схемы 3, компании 3, маршруты 6", n == "0 0 0 3 3 6", n)
    r = run(GOOD, "scripts.prod_init", "admin", "--email", "Owner@Example.Test", "--name", "Владелец Тестовый")
    m = re.search(r"Временный пароль \(показывается один раз\): (\S+)", r.stdout)
    ok("admin: временный пароль выдан", r.returncode == 0 and bool(m), r.stdout[-300:] + r.stderr[-300:])
    pw = m.group(1) if m else ""
    r2 = run(GOOD, "scripts.prod_init", "admin", "--email", "second@example.test", "--name", "Второй")
    ok("admin: второй ЗГД этой командой не создаётся", r2.returncode == 1)
    r = run(GOOD, "scripts.prod_init", "check")
    ok("check: готово к запуску", r.returncode == 0, r.stdout[-500:])

    print("3. Запуск и вход")
    code = r'''
import json, sys
from fastapi.testclient import TestClient
from app.main import app
pw = sys.argv[1]
out = {}
with TestClient(app, base_url="https://crm.example.test") as c:
    out["health"] = c.get("/api/health").json()
    out["providers"] = c.get("/api/auth/providers").json()
    out["oidc_login"] = c.get("/api/auth/login", follow_redirects=False).status_code
    out["docs"] = c.get("/api/docs").status_code
    out["dev_header"] = c.get("/api/auth/me", headers={"X-Dev-User": "owner@example.test"}).status_code
    out["bad_pw"] = c.post("/api/auth/password-login", json={"login": "owner@example.test", "password": "wrong-password-1"}).status_code
    r = c.post("/api/auth/password-login", json={"login": "owner@example.test", "password": pw})
    out["login"] = r.status_code
    out["cookie_secure"] = "secure" in r.headers.get("set-cookie", "").lower()
    me = c.get("/api/auth/me").json()
    out["must_change"] = me.get("security", {}).get("password_must_change")
    out["roles"] = [x["code"] for x in me.get("roles", [])]
    out["blocked_before_change"] = c.get("/api/deals").status_code
    r = c.post("/api/auth/password", json={"current_password": pw, "new_password": "Rehearsal-Pass-2026-x"})
    out["change"] = r.status_code
    out["dev_users"] = c.get("/api/auth/dev-users").status_code
    # все GET-маршруты без параметров в пути — на пустой базе ни один не должен падать
    from app.core.download_audit import walk_routes
    seen = set()
    for r in walk_routes(app.routes):
        path, methods = getattr(r, "path", ""), getattr(r, "methods", None) or set()
        if "GET" in methods and "{" not in path and path.startswith("/api/") and path not in seen and path not in ("/api/auth/login", "/api/auth/callback", "/api/auth/logout"):
            seen.add(path)
            try:
                out["page_" + path] = c.get(path).status_code
            except Exception as e:  # noqa: BLE001
                out["page_" + path] = "EXC " + type(e).__name__
print("JSON" + json.dumps(out, ensure_ascii=False))
'''
    r = subprocess.run([PY, "-c", code, pw], cwd="backend", env={**GOOD, "PYTHONPATH": "."}, capture_output=True, text=True)
    import json
    line = [x for x in r.stdout.splitlines() if x.startswith("JSON")]
    ok("backend стартовал в рабочем режиме", bool(line), r.stderr[-900:])
    o = json.loads(line[0][4:]) if line else {}
    print("   ", {k: v for k, v in o.items() if not k.startswith("page_")})
    ok("health: prod, password, clamav", o.get("health", {}).get("env") == "prod" and o["health"].get("auth") == "password" and o["health"].get("file_scan") == "clamav")
    ok("страница входа: только пароль", o.get("providers", {}).get("password") is True and o["providers"].get("m365") is False and o["providers"].get("dev") is False)
    ok("внешний вход отключён (404)", o.get("oidc_login") == 404)
    ok("/api/docs закрыт", o.get("docs") == 404)
    ok("X-Dev-User не действует", o.get("dev_header") == 401)
    ok("неверный пароль — 401", o.get("bad_pw") == 401)
    ok("вход по временному паролю, cookie Secure", o.get("login") == 200 and o.get("cookie_secure"))
    ok("роль ЗГД, требуется смена пароля", o.get("roles") == ["gd"] and o.get("must_change") is True)
    ok("до смены пароля разделы закрыты", o.get("blocked_before_change") in (403, 428))
    ok("смена пароля", o.get("change") == 200)
    ok("«Войти как» недоступен", o.get("dev_users") == 404)
    pages = {k[5:]: v for k, v in o.items() if k.startswith("page_")}
    broken = {k: v for k, v in pages.items() if not isinstance(v, int) or v >= 500}
    other = {k: v for k, v in pages.items() if isinstance(v, int) and 300 <= v < 500}
    ok(f"пустая база: {len(pages)} GET-маршрутов без ошибок сервера", not broken and len(pages) > 80, str(broken))
    print("    не 200 (ожидаемо: нужен параметр или нет данных):", other)
finally:
    psql("postgres", "-c", "alter role gsho password 'gsho'")
    psql("postgres", "-c", f"drop database if exists {DB}")
print("ИТОГ:", "PASS" if not bad else f"FAIL ({bad})")
sys.exit(1 if bad else 0)
