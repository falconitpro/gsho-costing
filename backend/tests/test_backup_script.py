"""Скрипт резервного копирования deploy/backup/gsho-backup.sh на синтетическом локальном репозитории restic (12 BKP-01…BKP-07).

Запускается, если доступны restic, pg_dump/pg_restore и тестовая PostgreSQL (DATABASE_URL). Реальный Яндекс не используется:
локальный репозиторий restic проверяет те же шаги (шифрование, снимок, check, forget/prune), а отказ S3 моделируется недоступным репозиторием.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/backup/gsho-backup.sh"
VERIFY = ROOT / "deploy/backup/gsho-restore-verify.sh"
TIMER = ROOT / "deploy/backup/gsho-backup.timer"
SERVICE = ROOT / "deploy/backup/gsho-backup.service"
SHELL_FILES = [SCRIPT, VERIFY, ROOT / "deploy/backup/gsho-backup-status.sh", ROOT / "deploy/deploy-remote.sh"]
CANARY = "CANARY-Restic-Pass-4b1e9c0d7a2f65e8"

need = [shutil.which(x) for x in ("restic", "pg_dump", "pg_restore", "flock")]
integration = pytest.mark.skipif(not all(need) or not os.environ.get("DATABASE_URL"), reason="нужны restic, pg_dump/pg_restore, flock и DATABASE_URL")


def _pg_env() -> dict:
    u = urlparse(os.environ["DATABASE_URL"].replace("postgresql+psycopg", "postgresql"))
    env = {"PGHOST": u.hostname or "localhost", "PGPORT": str(u.port or 5432), "PGUSER": u.username or "gsho", "PGDATABASE": (u.path or "/gsho").lstrip("/")}
    if u.password:
        env["PGPASSWORD"] = u.password   # только тестовая БД; скрипт пароль не печатает и аргументом не передаёт
    return env


@pytest.fixture()
def box(tmp_path):
    files = tmp_path / "files"
    (files / "sub").mkdir(parents=True)
    (files / "a.txt").write_text("synthetic attachment")
    (files / "sub" / "b.bin").write_bytes(os.urandom(4096))
    env = {**os.environ, **_pg_env(), "RESTIC_REPOSITORY": str(tmp_path / "repo"), "RESTIC_PASSWORD": CANARY,
           "BACKUP_STATUS_DIR": str(tmp_path / "status"), "BACKUP_STAGING_DIR": str(tmp_path / "staging"), "BACKUP_LOCK_FILE": str(tmp_path / "lock"),
           "BACKUP_COMPOSE_DIR": str(ROOT), "BACKUP_DB_SOURCE": "direct", "BACKUP_FILES_SOURCE": "dir", "BACKUP_FILES_DIR": str(files),
           "BACKUP_MIN_FREE_MB": "1", "RESTIC_CACHE_DIR": str(tmp_path / "cache")}
    (tmp_path / "status").mkdir()
    subprocess.run(["restic", "init", "-q"], env=env, check=True, capture_output=True)
    return tmp_path, env


def _run(env, reason="manual", **extra):
    return subprocess.run([str(SCRIPT), f"--reason={reason}"], env={**env, **extra}, capture_output=True, text=True, timeout=300)


def _last(tmp):
    return json.loads((tmp / "status" / "last-run.json").read_text())


def _snapshots(env):
    out = subprocess.run(["restic", "snapshots", "--json"], env=env, capture_output=True, text=True, check=True).stdout
    return json.loads(out) or []


@integration
def test_success_run_manifest_staging_and_no_secrets(box):
    tmp, env = box
    p = _run(env)
    assert p.returncode == 0, p.stderr
    st = _last(tmp)
    assert st["status"] == "success" and st["reason"] == "manual" and st["storage_provider"] == "local"
    assert re.fullmatch(r"[0-9a-f]{16}", st["snapshot_id"]) and st["check_status"] == "ok" and st["retention_status"] == "ok"
    assert st["files_count"] == 2 and st["database_bytes"] > 0 and re.fullmatch(r"\d{3}_[a-z_]+", st["schema_revision"])
    snaps = _snapshots(env)
    assert len(snaps) == 1 and snaps[0]["id"].startswith(st["snapshot_id"]) and f"run={st['run_key']}" in snaps[0]["tags"]
    assert not any((tmp / "staging").iterdir())           # staging удалён
    runs = list((tmp / "status" / "runs").glob("*.json"))
    assert len(runs) == 2 or len(runs) == 1                 # running перезаписан финальным тем же файлом
    for f in [tmp / "status" / "last-run.json", *runs]:
        assert CANARY not in f.read_text() and oct(f.stat().st_mode & 0o777) == "0o644"
    assert CANARY not in p.stdout + p.stderr
    # шифрование на клиенте: в репозитории нет открытого текста вложений
    blob = b"".join(f.read_bytes() for f in (tmp / "repo").rglob("*") if f.is_file())
    assert b"synthetic attachment" not in blob and b"PGDMP" not in blob


@integration
def test_restore_verify_roundtrip_and_wrong_password(box):
    tmp, env = box
    assert _run(env).returncode == 0
    snap = _last(tmp)["snapshot_id"]
    target = tmp / "restore"
    db = f"gsho_restore_test_{os.getpid()}"
    try:
        p = subprocess.run([str(VERIFY), f"--snapshot={snap[:8]}", f"--target={target}", f"--pg-restore-to={db}"], env=env, capture_output=True, text=True, timeout=300)
        assert p.returncode == 0, p.stderr + p.stdout
        assert p.stdout.count(": OK") == 3 and "файлов распаковано: 2" in p.stdout and "таблиц:" in p.stdout
        assert CANARY not in p.stdout + p.stderr
        bad = subprocess.run(["restic", "snapshots"], env={**env, "RESTIC_PASSWORD": "wrong-password"}, capture_output=True, text=True)
        assert bad.returncode != 0                           # неверный пароль не открывает репозиторий
        again = subprocess.run([str(VERIFY), f"--snapshot={snap[:8]}", f"--target={target}"], env=env, capture_output=True, text=True)
        assert again.returncode == 2 and "не пустой" in again.stderr
    finally:
        subprocess.run(["dropdb", "--if-exists", db], env=env, capture_output=True)


@integration
def test_repository_unreachable_fails_and_keeps_previous_snapshot(box):
    tmp, env = box
    assert _run(env).returncode == 0
    prev = _last(tmp)
    p = _run(env, RESTIC_REPOSITORY=str(tmp / "missing-repo"))
    assert p.returncode == 1
    st = _last(tmp)
    assert st["status"] == "failed" and st["error_code"] == "restic_backup_failed" and st["snapshot_id"] is None
    assert len(_snapshots(env)) == 1 and _snapshots(env)[0]["id"].startswith(prev["snapshot_id"])
    assert not any((tmp / "staging" / "gsho-backup").glob("*")) if (tmp / "staging" / "gsho-backup").exists() else True


@integration
def test_wrong_password_fails_without_leak(box):
    tmp, env = box
    p = _run(env, RESTIC_PASSWORD="another-" + CANARY)
    assert p.returncode == 1 and _last(tmp)["error_code"] == "restic_backup_failed"
    assert CANARY not in (tmp / "status" / "last-run.json").read_text()


@integration
def test_config_missing_names_not_values(box):
    tmp, env = box
    env = {k: v for k, v in env.items() if k != "RESTIC_PASSWORD" and not k.startswith("AWS_")}
    p = _run(env, RESTIC_REPOSITORY="s3:https://storage.yandexcloud.net/b/gsho-prod", AWS_ACCESS_KEY_ID=CANARY)
    assert p.returncode == 1
    st = _last(tmp)
    assert st["error_code"] == "config_missing" and "RESTIC_PASSWORD" in st["error_summary"] and "AWS_SECRET_ACCESS_KEY" in st["error_summary"]
    assert CANARY not in json.dumps(st) + p.stderr


@integration
def test_concurrent_run_is_blocked_by_lock(box):
    tmp, env = box
    with open(env["BACKUP_LOCK_FILE"], "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        p = _run(env)
    assert p.returncode == 75 and "already_running" in p.stderr
    assert not (tmp / "status" / "last-run.json").exists() and _snapshots(env) == []


@integration
@pytest.mark.parametrize("extra,code", [
    ({"BACKUP_STORAGE_PROVIDER": "s3"}, "unsupported_storage"),
    ({"BACKUP_MIN_FREE_MB": str(10**9)}, "disk_full"),
    ({"PGDATABASE": "no_such_db_for_backup"}, "db_dump_failed"),
    ({"BACKUP_FILES_DIR": "/nonexistent/files"}, "files_failed"),
])
def test_failures_are_failed_not_success(box, extra, code):
    tmp, env = box
    p = _run(env, **extra)
    assert p.returncode == 1
    st = _last(tmp)
    assert st["status"] == "failed" and st["error_code"] == code and st["snapshot_id"] is None
    assert _snapshots(env) == []
    assert not (tmp / "staging" / "gsho-backup").exists()   # частичные артефакты не остаются и не считаются копией


@integration
def test_retention_daily_and_monthly(box):
    """BKP-07 на уменьшенной политике (10 daily / 3 monthly; в бою 90/12 — значения по умолчанию в скрипте)."""
    tmp, env = box
    src = tmp / "tiny"
    src.mkdir()
    (src / "x").write_text("x")
    env = {**env, "TZ": "Europe/Moscow"}   # скрипт считает дни по Москве — синтетические снимки тоже
    now = datetime.now(ZoneInfo("Europe/Moscow")).replace(hour=12, minute=0, second=0, microsecond=0)
    times = [now - timedelta(days=d) for d in range(1, 16)] + [now - timedelta(days=d) for d in (60, 75, 100, 130, 160, 200)]
    for t in times:
        subprocess.run(["restic", "backup", str(src), "--host", "gsho-prod", "--tag", "gsho", "--time", t.strftime("%Y-%m-%d %H:%M:%S"), "-q"],
                       env=env, check=True, capture_output=True)
    p = _run(env, reason="daily", BACKUP_KEEP_DAILY="10", BACKUP_KEEP_MONTHLY="3")
    assert p.returncode == 0, p.stderr
    kept = {s["time"][:10] for s in _snapshots(env)}
    alltimes = sorted([now] + times, reverse=True)   # + снимок самого запуска (сегодня)
    days = []
    for t in alltimes:
        if t.date() not in days:
            days.append(t.date())
    months = []
    for t in alltimes:
        if (t.year, t.month) not in [m[0] for m in months]:
            months.append(((t.year, t.month), t.date()))
    expect = {d.isoformat() for d in days[:10]} | {d.isoformat() for _, d in months[:3]}
    assert kept == expect, " ".join(sorted(kept)) + " | " + " ".join(sorted(expect))
    assert _last(tmp)["retention_status"] == "ok"
    text = SCRIPT.read_text()
    assert 'KEEP_DAILY="${BACKUP_KEEP_DAILY:-90}"' in text and 'KEEP_MONTHLY="${BACKUP_KEEP_MONTHLY:-12}"' in text


@integration
def test_status_file_is_accepted_by_backend_parser(box):
    tmp, env = box
    assert _run(env).returncode == 0
    from app.services.backup_status import parse_status, read_host
    parse_status((tmp / "status" / "last-run.json").read_bytes())
    host = read_host(str(tmp / "status"))
    assert host.last["status"] == "success" and not host.errors and host.runs


def test_timer_is_19_moscow_daily_persistent():
    t = TIMER.read_text()
    assert "OnCalendar=*-*-* 19:00:00 Europe/Moscow" in t and "Persistent=true" in t
    assert not re.search(r"^Timezone=", t, re.M)   # такой директивы в systemd нет — она молча игнорируется (D-025)
    s = SERVICE.read_text()
    assert "EnvironmentFile=/etc/gsho/backup.env" in s and "--reason=daily" in s and "Type=oneshot" in s
    if shutil.which("systemd-analyze"):
        # вывод зависит от пояса машины — считаем в UTC: 19:00 МСК = 16:00 UTC (летнего времени в Москве нет)
        p = subprocess.run(["systemd-analyze", "calendar", "*-*-* 19:00:00 Europe/Moscow"], capture_output=True, text=True, env={**os.environ, "TZ": "UTC"})
        assert p.returncode == 0 and "16:00:00 UTC" in p.stdout, p.stdout
        d = tempfile.mkdtemp()
        Path(d, "x.service").write_text("[Service]\nType=oneshot\nExecStart=/bin/true\n")
        Path(d, "x.timer").write_text(t.replace("Unit=gsho-backup.service", "Unit=x.service"))
        v = subprocess.run(["systemd-analyze", "verify", str(Path(d, "x.timer"))], capture_output=True, text=True)
        assert "Unknown key" not in v.stderr


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck не установлен")
def test_shell_scripts_pass_bash_n_and_shellcheck():
    for f in SHELL_FILES:
        assert subprocess.run(["bash", "-n", str(f)]).returncode == 0, f
    p = subprocess.run(["shellcheck", "-f", "gcc", *map(str, SHELL_FILES)], capture_output=True, text=True, env={**os.environ, "LC_ALL": "C.UTF-8"})
    assert p.returncode == 0, p.stdout


def test_example_env_has_only_placeholders():
    t = (ROOT / "deploy/backup/gsho-backup.env.example").read_text()
    for k in ("RESTIC_PASSWORD", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        line = next(x for x in t.splitlines() if x.startswith(k + "="))
        assert line.split("=", 1)[1].startswith("<"), line
