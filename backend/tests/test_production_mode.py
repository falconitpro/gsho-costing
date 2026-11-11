"""Рабочий режим (deploy/production/install.md): вход только по паролю, проверки запуска, первый пользователь, справочники без
демо-данных, ручное обновление с обязательной копией. Полная репетиция на пустой базе — docs/operations/qa_production_init.py."""
from __future__ import annotations

import dataclasses
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.core import environment  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AuthSession, Client, Factory, PaymentScheme, SupplyRoute, User  # noqa: E402
from app.services import password_policy  # noqa: E402
from scripts import prod_init, reference  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
KEYS = {"secret_key": "s" * 64, "mfa_encryption_key": "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        "database_url": "postgresql+psycopg://gsho:own-strong-password@db:5432/gsho"}


def _s(**kw):
    return dataclasses.replace(get_settings(), **kw)


def test_production_requires_own_secrets_and_known_login_mode():
    good = dict(env="prod", auth_provider="password", file_scan="clamav", **KEYS)
    assert environment.config_problems(_s(**good)) == []
    for provider in ("entra", "yandex", "keycloak"):
        assert environment.config_problems(_s(**{**good, "auth_provider": provider})) == []
    for bad, needle in (({"auth_provider": "dev"}, "AUTH_PROVIDER=dev"), ({"auth_provider": "m365"}, "AUTH_PROVIDER=m365"),
                        ({"secret_key": "dev-secret-change-me"}, "SECRET_KEY"), ({"secret_key": "change-me"}, "SECRET_KEY"),
                        ({"secret_key": "short"}, "SECRET_KEY"), ({"mfa_encryption_key": ""}, "MFA_ENCRYPTION_KEY"),
                        ({"mfa_encryption_key": "not-a-key"}, "MFA_ENCRYPTION_KEY"),
                        ({"database_url": "postgresql+psycopg://gsho:gsho@db:5432/gsho"}, "пароль базы")):
        p = environment.config_problems(_s(**{**good, **bad}))
        assert len(p) == 1 and needle in p[0], (bad, p)
    assert environment.config_problems(_s(env="dev")) == []      # стенд по-прежнему запускается с настройками по умолчанию


def test_password_only_mode_hides_external_login():
    s = get_settings()
    old = s.auth_provider
    try:
        with TestClient(app) as c:
            s.auth_provider = "password"
            assert c.get("/api/auth/providers").json() == {**c.get("/api/auth/providers").json(), "password": True, "m365": False, "dev": False}
            assert c.get("/api/auth/login", follow_redirects=False).status_code == 404
            assert c.get("/api/auth/callback?code=x&state=y").status_code == 404
            assert c.get("/api/auth/me", headers={"X-Dev-User": "manager@example.com"}).status_code == 401   # заголовок стенда не действует
            s.auth_provider = "entra"
            assert c.get("/api/auth/providers").json()["m365"] is True
            assert c.get("/api/auth/login", follow_redirects=False).status_code in (302, 307)
    finally:
        s.auth_provider = old


def test_temp_password_fits_policy_and_is_random():
    pws = {prod_init.temp_password() for _ in range(50)}
    assert len(pws) == 50
    for pw in pws:
        assert len(pw) == 23 and not password_policy.problems(pw) and not set("lI1O0") & set(pw)


def test_bootstrap_admin_temp_password_forced_change(capsys):
    seed()
    email = "bootstrap-owner@example.test"
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.email == email))
        if u:
            u.password_hash = None
            db.commit()
    assert prod_init.admin(email, "Владелец Первый") == 1          # на стенде ЗГД с паролем уже есть — без --force отказ
    assert "ЗГД с паролем уже есть" in capsys.readouterr().out
    assert prod_init.admin(email, "Владелец Первый", force=True) == 0
    out = capsys.readouterr().out
    pw = out.split("показывается один раз): ")[1].split()[0]
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.email == email))
        assert u.roles == ["gd"] and u.password_must_change and u.password_hash and pw not in u.password_hash
    with TestClient(app) as c:
        assert c.post("/api/auth/password-login", json={"login": email, "password": pw}).status_code == 200
        assert c.get("/api/auth/me").json()["security"]["password_must_change"] is True
        assert c.get("/api/deals").status_code == 403                # до смены пароля разделы закрыты
        assert c.post("/api/auth/password", json={"current_password": pw, "new_password": "Own-secret-river-2026"}).status_code == 200
        assert c.get("/api/deals").status_code == 200
    assert prod_init.admin(email, "Владелец Первый") == 1          # пароль уже задан — повтор без --force не сбрасывает
    assert prod_init.admin(email, "Владелец Первый", force=True) == 0   # аварийный сброс: новый пароль, все входы завершены
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.email == email))
        assert db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.user_id == u.id, AuthSession.revoked_at.is_(None))) == 0
        u.is_active = False                                            # не оставляем лишнего ЗГД другим тестам
        db.commit()
    assert prod_init.admin("no-at-sign", "Имя") == 2


def test_reference_has_no_demo_data_and_is_idempotent():
    with SessionLocal() as db:
        before = [db.scalar(select(func.count()).select_from(m)) for m in (User, Client, Factory)]
        reference.run(db)
        reference.run(db)
        db.commit()
        assert [db.scalar(select(func.count()).select_from(m)) for m in (User, Client, Factory)] == before
        assert db.scalar(select(func.count()).select_from(PaymentScheme).where(PaymentScheme.name.in_([n for n, _ in reference.SCHEMES]))) == 3
        assert db.scalar(select(func.count()).select_from(SupplyRoute).where(SupplyRoute.name.in_([r[0] for r in reference.ROUTES]))) == len(reference.ROUTES)


def test_seed_as_script_skips_populated_database():
    """«python -m scripts.seed» зовёт выкладка стенда. Раньше падал с NameError; теперь работает, но базу с сотрудниками не трогает."""
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.email == "manager@example.com"))
        old, u.password_hash = u.password_hash, None
        db.commit()
    try:
        p = subprocess.run([sys.executable, "-m", "scripts.seed"], cwd=ROOT / "backend", capture_output=True, text=True, env={**os.environ, "PYTHONPATH": "."})
        assert p.returncode == 0 and "seed: пропущено" in p.stdout, p.stderr[-500:]
        with SessionLocal() as db:
            assert db.scalar(select(User).where(User.email == "manager@example.com")).password_hash is None   # демо-пароль не выдан
    finally:
        with SessionLocal() as db:
            db.scalar(select(User).where(User.email == "manager@example.com")).password_hash = old
            db.commit()


# ------------------------------------------------------------------ deploy/production/update.sh

UPDATE = ROOT / "deploy/production/update.sh"


def _stub(d: Path, name: str, body: str) -> None:
    p = d / name
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


@pytest.fixture()
def box(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "calls.log"
    _stub(bin_, "git", f'''echo "git $*" >> {log}
case "$*" in
  "rev-parse HEAD") echo aaa111 ;;
  "rev-parse origin/main") echo "${{STUB_NEW:-bbb222}}" ;;
  "rev-parse --short"*) echo short ;;
  *"--diff-filter=A"*) printf '%s' "${{STUB_NEW_SQL:-}}" ;;
  *"--diff-filter=MD"*) printf '%s' "${{STUB_CHANGED_SQL:-}}" ;;
  show*) echo "select 1;" ;;
  log*) echo "abc Изменение" ;;
esac
exit 0
''')
    _stub(bin_, "docker", f'cat >/dev/null 2>&1 < /dev/stdin || true; echo "docker $*" >> {log}; exit 0\n')
    _stub(bin_, "timeout", 'shift; exec "$@"\n')
    _stub(bin_, "backup", f'echo "backup $*" >> {log}; exit "${{STUB_BACKUP_RC:-0}}"\n')
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy/deploy-remote.sh").write_text(f'echo "deploy-remote BACKUP_REQUIRED=$BACKUP_REQUIRED" >> {log}\n')
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "GSHO_DIR": str(tmp_path), "GSHO_BACKUP_BIN": str(bin_ / "backup"),
           "GSHO_BACKUP_RETRY_SLEEP": "0"}
    return tmp_path, env, log


def _update(env, *args, stdin="", **extra):
    return subprocess.run(["bash", str(UPDATE), *args], env={**env, **extra}, input=stdin, capture_output=True, text=True, timeout=60)


def _calls(log):
    return log.read_text().splitlines() if log.exists() else []


def test_update_no_changes_does_nothing(box):
    tmp, env, log = box
    p = _update(env, "--yes", STUB_NEW="aaa111")
    assert p.returncode == 0 and "Обновлений нет" in p.stdout
    assert not any(x.startswith(("backup", "docker", "deploy-remote")) for x in _calls(log))


def test_update_order_backup_then_sql_then_deploy(box):
    tmp, env, log = box
    p = _update(env, "--yes", STUB_NEW_SQL="backend/migrations/020_x.sql\n")
    assert p.returncode == 0, p.stderr
    c = _calls(log)
    b = next(i for i, x in enumerate(c) if x.startswith("backup --reason=pre_deploy"))
    q = next(i for i, x in enumerate(c) if "psql" in x and "ON_ERROR_STOP=1" in x)
    d = next(i for i, x in enumerate(c) if x.startswith("deploy-remote"))
    assert b < q < d and c[d].endswith("BACKUP_REQUIRED=false")      # копия одна, не две
    assert any("prod_init check" in x for x in c[d:])


def test_update_stops_without_backup_or_on_changed_sql(box):
    tmp, env, log = box
    p = _update(env, "--yes", STUB_BACKUP_RC="1", STUB_NEW_SQL="backend/migrations/020_x.sql\n")
    assert p.returncode == 1 and "ничего не изменено" in p.stderr
    assert not any("psql" in x or x.startswith("deploy-remote") for x in _calls(log))
    log.unlink()
    p = _update(env, "--yes", GSHO_BACKUP_BIN=str(tmp / "missing"))
    assert p.returncode == 1 and "без копии не обновляется" in p.stderr and not any(x.startswith("deploy-remote") for x in _calls(log))
    p = _update(env, "--yes", STUB_CHANGED_SQL="backend/migrations/014_security_core.sql\n")
    assert p.returncode == 1 and "уже применённые" in p.stderr and not any(x.startswith(("backup", "deploy-remote")) for x in _calls(log))


def test_update_asks_and_cancels(box):
    tmp, env, log = box
    p = _update(env, stdin="нет\n")
    assert p.returncode == 0 and "Отменено" in p.stdout and not any(x.startswith(("backup", "deploy-remote")) for x in _calls(log))
    p = _update(env, stdin="да\n")
    assert p.returncode == 0 and any(x.startswith("deploy-remote") for x in _calls(log))


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck не установлен")
def test_production_scripts_pass_shellcheck():
    files = sorted((ROOT / "deploy/production").glob("*.sh"))
    assert len(files) == 4
    for f in files:
        assert subprocess.run(["bash", "-n", str(f)]).returncode == 0, f
    p = subprocess.run(["shellcheck", "-f", "gcc", *map(str, files)], capture_output=True, text=True, env={**os.environ, "LC_ALL": "C.UTF-8"})
    assert p.returncode == 0, p.stdout


def test_db_inventory_is_read_only_and_has_no_secrets(capsys):
    from scripts import db_inventory
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(User))
        hashes = [h for h in db.scalars(select(User.password_hash)).all() if h][:20]
    assert db_inventory.main() == 0
    out = capsys.readouterr().out
    assert "== таблицы" in out and "== сотрудники" in out and "== сделки ГШО" in out and "размер базы" in out
    assert not any(h in out for h in hashes) and "pbkdf2" not in out
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(User)) == before


# ------------------------------------------------------------------ перенос со стенда: adopt, fx

def test_adopt_dry_run_changes_nothing_and_refuses_production_database(capsys):
    """Сам сброс паролей на общей тестовой базе не выполняем (он нужен другим тестам) — полный прогон в docs/operations/qa_production_adopt.py.
    Здесь: без --yes ничего не меняется; на базе с отметкой production команда отказывается."""
    from app.models import AppMarker
    seed()
    with SessionLocal() as db:
        with_pw = db.scalar(select(func.count()).select_from(User).where(User.password_hash.is_not(None)))
        m = db.get(AppMarker, "data_classification")
        old = m.value if m else None
    assert with_pw > 0
    assert prod_init.adopt() == 0
    out = capsys.readouterr().out
    assert "Ничего не изменено" in out and "будет очищено" in out
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(User).where(User.password_hash.is_not(None))) == with_pw
        m = db.get(AppMarker, "data_classification")
        if m is None:
            m = AppMarker(key="data_classification", value="production", app_env="prod")
            db.add(m)
        m.value = "production"
        db.commit()
    try:
        assert prod_init.adopt(yes=True) == 1
        assert "уже помечена как рабочая" in capsys.readouterr().out
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(User).where(User.password_hash.is_not(None))) == with_pw
    finally:
        with SessionLocal() as db:
            m = db.get(AppMarker, "data_classification")
            if old is None:
                db.delete(m)
            else:
                m.value = old
            db.commit()


def test_adopt_never_touches_business_or_audit_tables():
    keep = {"users", "deals", "deal_versions", "positions", "clients", "factories", "parts", "attachments", "attachment_scans", "tasks", "audit_log",
            "security_events", "security_alerts", "access_rules", "contracts", "fx_rates", "support_tickets", "support_messages"}
    assert not keep & set(prod_init.ADOPT_CLEAR)
    assert {"auth_sessions", "mfa_credentials", "mfa_recovery_codes", "password_history"} <= set(prod_init.ADOPT_CLEAR)


def test_fx_reload_replaces_only_on_success(capsys):
    from datetime import date, timedelta

    from app.models import FxRate

    class Src:
        def __init__(self, n):
            self.n = n

        def history(self, cur, start, end):
            if self.n < 0:
                raise OSError("network")
            return [(date(2020, 1, 1) + timedelta(days=i), 10.0 + i / 1000) for i in range(self.n)]

    cur = "ZZT"
    with SessionLocal() as db:
        db.query(FxRate).filter(FxRate.currency == cur).delete()
        db.add_all([FxRate(on=date(2019, 1, 1), currency=cur, rate=1.0, source="cbr"), FxRate(on=date(2019, 1, 2), currency=cur, rate=2.0, source="manual", comment="руч.")])
        db.commit()
    try:
        assert prod_init.fx((cur,), Src(-1)) == 1 and prod_init.fx((cur,), Src(5)) == 1      # сбой сети и подозрительно короткий ответ
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(FxRate).where(FxRate.currency == cur)) == 2   # прежние курсы целы
        assert prod_init.fx((cur,), Src(300)) == 0
        with SessionLocal() as db:
            rows = db.scalars(select(FxRate).where(FxRate.currency == cur)).all()
            assert len([r for r in rows if r.source == "cbr"]) == 300 and not any(r.on == date(2019, 1, 1) for r in rows if r.source == "cbr")
            assert [r.rate for r in rows if r.source == "manual"] == [2.0]                         # ручные курсы не затронуты
    finally:
        with SessionLocal() as db:
            db.query(FxRate).filter(FxRate.currency == cur).delete()
            db.commit()


# ------------------------------------------------------------------ deploy/production/transfer-from-stand.sh

TRANSFER = ROOT / "deploy/production/transfer-from-stand.sh"


@pytest.fixture()
def tbox(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "calls.log"
    snap = tmp_path / "snapdata/var/lib/gsho-backup/staging/gsho-backup"
    snap.mkdir(parents=True)
    for n in ("db.dump", "files.tar", "manifest.json"):
        (snap / n).write_text(n)
    subprocess.run("sha256sum db.dump files.tar manifest.json > SHA256SUMS", shell=True, cwd=snap, check=True)
    _stub(bin_, "restic", f'''echo "restic $*" >> {log}
if [ "$1" = restore ]; then while [ $# -gt 0 ]; do [ "$1" = --target ] && cp -a {tmp_path}/snapdata/. "$2"/; shift; done; fi
exit 0
''')
    _stub(bin_, "docker", f'''echo "docker $*" >> {log}
case "$*" in
  *"ps -q backend"*) echo "${{STUB_BACKEND_ID:-}}" ;;
  *"information_schema.tables"*) echo "${{STUB_TABLES:-0}}" ;;
  *pg_restore*|*"tar -xf"*) cat > /dev/null ;;
esac
exit 0
''')
    (tmp_path / "deploy/production").mkdir(parents=True)
    (tmp_path / "deploy/production/init-db.sh").write_text(f'echo "init-db $*" >> {log}\n')
    (tmp_path / "restore.env").write_text("RESTIC_REPOSITORY=s3:example\nRESTIC_PASSWORD=x\n")
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "GSHO_DIR": str(tmp_path), "GSHO_RESTORE_ENV": str(tmp_path / "restore.env"),
           "GSHO_TRANSFER_DIR": str(tmp_path / "transfer")}
    return tmp_path, env, log


def _transfer(env, *args, **extra):
    return subprocess.run(["bash", str(TRANSFER), *args], env={**env, **extra}, capture_output=True, text=True, timeout=60)


def test_transfer_order_and_final_instructions(tbox):
    tmp, env, log = tbox
    p = _transfer(env, "--snapshot=9d720133")
    assert p.returncode == 0, p.stderr
    c = _calls(log)
    idx = lambda needle: next(i for i, x in enumerate(c) if needle in x)   # noqa: E731
    assert idx("restic restore") < idx("pg_restore") < idx("tar -xf") < idx("init-db --existing") < idx("prod_init adopt --yes") < idx("prod_init fx") < idx("db_inventory")
    assert "db.dump: OK" in p.stdout and "ПЕРЕНОС ЗАВЕРШЁН" in p.stdout and "rm -f" in p.stdout
    assert not any("up -d backend" in x or "up -d --build" in x for x in c)            # CRM скрипт не запускает


def test_transfer_refuses_running_crm_non_empty_db_bad_checksum_and_latest(tbox):
    tmp, env, log = tbox
    p = _transfer(env, "--snapshot=9d720133", STUB_BACKEND_ID="abc")
    assert p.returncode == 3 and "уже запущена" in p.stderr and not any("restic" in x or "pg_restore" in x for x in _calls(log))
    log.unlink()
    p = _transfer(env, "--snapshot=9d720133", STUB_TABLES="113")
    assert p.returncode == 3 and "база не пустая" in p.stderr and not any("restic" in x or "pg_restore" in x for x in _calls(log))
    assert _transfer(env, "--snapshot=latest").returncode == 2
    assert _transfer(env, "--snapshot=9d720133", GSHO_RESTORE_ENV=str(tmp / "missing")).returncode == 2
    log.unlink()
    (tmp / "snapdata/var/lib/gsho-backup/staging/gsho-backup/db.dump").write_text("повреждено")
    p = _transfer(env, "--snapshot=9d720133")
    assert p.returncode != 0 and not any("pg_restore" in x or "adopt" in x for x in _calls(log))   # при несовпадении суммы база не трогается
