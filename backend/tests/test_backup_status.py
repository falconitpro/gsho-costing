"""Статус резервных копий в CRM (docs/operations/03 §5, 09 §3, 12 BKP-04/BKP-09): разбор host-статуса, права, история,
«нет копии дольше 26 ч», уведомления только в системе, отсутствие секретов в ответах."""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BackupRun, IntegrationLog, NotificationLog, User  # noqa: E402
from app.services import backup_status as bs  # noqa: E402
from scripts.seed import TEST_LOGIN, run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
GD, ADMIN, MGR, FD = TEST_LOGIN, "admin@example.com", "manager@example.com", "fd@example.com"
CANARY = "CANARY-restic-Secret-9f8e7d6c5b4a39281706"


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _run(status="success", reason="daily", started=None, finished=None, **kw):
    started = started or _now() - timedelta(minutes=5)
    d = {"schema": 1, "run_key": str(uuid.uuid4()), "reason": reason, "status": status,
         "started_at": started.isoformat().replace("+00:00", "Z"),
         "finished_at": (finished or started + timedelta(minutes=3)).isoformat().replace("+00:00", "Z") if status != "running" else None,
         "snapshot_id": "07fae0f5566a2f9b" if status == "success" else None, "git_commit": "740b25a898663fcedd3ab2e8a131830c64ac971f",
         "schema_revision": "013_backup_operations", "storage_provider": "local", "database_bytes": 1168742, "files_bytes": 30720, "files_count": 2,
         "manifest_sha256": "0d2b8e48640d8ab4eb3aa7d72d6a5c8986e74d594f2924a7c6f0d42182ac496c",
         "check_status": "ok" if status == "success" else None, "checked_at": None, "retention_status": "ok" if status == "success" else None,
         "error_code": None, "error_summary": None}
    d.update(kw)
    return d


def _write(root, last=None, runs=()):
    os.makedirs(os.path.join(root, "runs"), exist_ok=True)
    for r in runs:
        with open(os.path.join(root, "runs", f"{r['started_at'].replace(':', '')}_{r['run_key']}.json"), "w") as f:
            json.dump(r, f)
    if last is not None:
        with open(os.path.join(root, "last-run.json"), "w") as f:
            f.write(last if isinstance(last, str) else json.dumps(last))


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    seed()


@pytest.fixture()
def status_dir(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("BACKUP_STATUS_DIR", d)
    with SessionLocal() as db:   # каждый тест — с чистой историей копий и их уведомлений
        db.execute(delete(BackupRun))
        db.execute(delete(IntegrationLog).where(IntegrationLog.system == "backup"))
        db.execute(delete(NotificationLog).where(NotificationLog.event == "backup"))
        db.commit()
    return d


def _uid(email):
    with SessionLocal() as db:
        return db.scalar(select(User.id).where(User.email == email))


def _backup_notes(email):
    with SessionLocal() as db:
        return db.scalars(select(NotificationLog).where(NotificationLog.event == "backup", NotificationLog.user_id == _uid(email))).all()


# ---------- разбор файла (без БД) ----------

def test_parse_accepts_schema_and_drops_unknown_fields():
    r = _run(extra_field="x", RESTIC_PASSWORD=CANARY)
    out = bs.parse_status(json.dumps(r).encode())
    assert out["status"] == "success" and out["snapshot_id"] == "07fae0f5566a2f9b"
    assert "extra_field" not in out and "RESTIC_PASSWORD" not in out
    assert CANARY not in json.dumps(out, default=str)


@pytest.mark.parametrize("patch,msg", [
    ({"schema": 2}, "схемы"), ({"run_key": "../../etc"}, "run_key"), ({"status": "ok"}, "status"), ({"reason": "cron"}, "reason"),
    ({"snapshot_id": "zz; rm -rf /"}, "snapshot_id"), ({"database_bytes": -1}, "database_bytes"), ({"started_at": "вчера"}, "started_at"),
    ({"started_at": "2026-11-10T19:00:00"}, "часового"), ({"snapshot_id": None}, "успех без snapshot_id"), ({"error_code": "rm -rf"}, "error_code"),
])
def test_parse_rejects_invalid(patch, msg):
    with pytest.raises(bs.StatusFileError, match=msg):
        bs.parse_status(json.dumps(_run(**patch)).encode())


def test_parse_rejects_oversize_and_garbage():
    with pytest.raises(bs.StatusFileError, match="большой"):
        bs.parse_status(b"{" + b" " * (bs.MAX_FILE_BYTES + 10) + b"}")
    with pytest.raises(bs.StatusFileError, match="JSON"):
        bs.parse_status(b"\xff\xfe not json")


def test_sanitize_strips_secrets_urls_and_long_tokens():
    s = bs.sanitize(f"AWS_SECRET_ACCESS_KEY={CANARY} fail https://storage.yandexcloud.net/b/x?X-Amz-Signature=abc s3:https://b/p token {CANARY}")
    assert CANARY not in s and "yandexcloud" not in s and "X-Amz" not in s
    assert len(bs.sanitize("x" * 1000)) <= 300


def test_next_run_is_19_moscow():
    nr = bs.next_run(datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc))   # 18:00 МСК
    assert nr == datetime(2026, 11, 11, 16, 0, tzinfo=timezone.utc)
    nr = bs.next_run(datetime(2026, 11, 11, 16, 0, 1, tzinfo=timezone.utc))  # сразу после 19:00 МСК — завтра, включая выходные
    assert nr == datetime(2026, 11, 12, 16, 0, tzinfo=timezone.utc)
    sat = bs.next_run(datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc))   # суббота
    assert sat.date().isoformat() == "2026-11-14"


# ---------- API и права ----------

def test_permissions_gd_admin_read_others_forbidden(status_dir):
    c = TestClient(app)
    for who in (GD, ADMIN):
        assert c.get("/api/operations/backups/status", headers=H(who)).status_code == 200
        assert c.get("/api/operations/backups/runs", headers=H(who)).status_code == 200
    for who in (MGR, FD, "cd@example.com", "lawyer@example.com", "observer@example.com"):
        assert c.get("/api/operations/backups/status", headers=H(who)).status_code == 403
        assert c.get("/api/operations/backups/runs", headers=H(who)).status_code == 403
    me = c.get("/api/auth/me", headers=H(ADMIN)).json()
    assert me["perms"].get("backup_status") == 1
    assert c.get("/api/auth/me", headers=H(MGR)).json()["perms"].get("backup_status", 0) == 0


def test_not_configured_before_owner_setup(status_dir):
    st = TestClient(app).get("/api/operations/backups/status", headers=H(ADMIN)).json()
    assert st["state"] == "not_configured" and st["stale"] is False and st["last_run"] is None
    assert st["timeweb_cutover_allowed"] is False
    assert _backup_notes(ADMIN) == []   # до настройки — без тревог


def test_success_ingested_idempotently_and_listed(status_dir):
    older = _run(started=_now() - timedelta(days=1, minutes=10))
    last = _run()
    _write(status_dir, last=last, runs=[older, last])
    c = TestClient(app)
    st = c.get("/api/operations/backups/status", headers=H(GD)).json()
    assert st["state"] == "ok" and st["last_success"]["snapshot_id"] == "07fae0f5"
    c.get("/api/operations/backups/status", headers=H(GD))   # повторный проход ничего не дублирует
    runs = c.get("/api/operations/backups/runs?limit=1", headers=H(GD)).json()
    assert runs["total"] == 2 and len(runs["items"]) == 1 and runs["items"][0]["run_key"] == last["run_key"]
    assert c.get("/api/operations/backups/runs?limit=100000", headers=H(GD)).json()["limit"] == 100


def test_running_then_finished_updates_same_row(status_dir):
    r = _run(status="running")
    _write(status_dir, last=r)
    c = TestClient(app)
    assert c.get("/api/operations/backups/status", headers=H(GD)).json()["last_run"]["status"] == "running"
    done = dict(_run(), run_key=r["run_key"], started_at=r["started_at"])
    _write(status_dir, last=done)
    st = c.get("/api/operations/backups/status", headers=H(GD)).json()
    assert st["last_run"]["status"] == "success"
    with SessionLocal() as db:
        assert db.scalar(select(BackupRun).where(BackupRun.run_key == r["run_key"])).status == "success"
        assert len(db.scalars(select(BackupRun)).all()) == 1


def test_failed_run_alerts_once_app_only_without_secrets(status_dir, monkeypatch):
    ok = _run(started=_now() - timedelta(hours=3))
    bad = _run(status="failed", error_code="restic_backup_failed", error_summary=f"AWS_SECRET_ACCESS_KEY={CANARY} https://storage.yandexcloud.net/x")
    _write(status_dir, last=bad, runs=[ok, bad])
    sent_external = []
    import app.adapters as adapters
    monkeypatch.setattr(adapters, "mail", lambda *a, **k: sent_external.append("mail"))
    monkeypatch.setattr(adapters, "messenger", lambda *a, **k: sent_external.append("messenger"))
    c = TestClient(app)
    r = c.get("/api/operations/backups/status", headers=H(ADMIN))
    st = r.json()
    assert st["state"] == "failed" and st["last_run"]["error_code"] == "restic_backup_failed"
    assert st["last_run"]["error_text"].startswith("Не удалось отправить копию")
    assert st["last_success"]["run_key"] == ok["run_key"]   # прежний успешный снимок по-прежнему виден
    assert CANARY not in r.text and "yandexcloud" not in r.text
    c.get("/api/operations/backups/status", headers=H(ADMIN))
    for who in (ADMIN, GD):
        notes = _backup_notes(who)
        assert len(notes) == 1 and notes[0].channel == "app" and "не создана" in notes[0].text and CANARY not in notes[0].text
    assert _backup_notes(MGR) == [] and sent_external == []
    bell = c.get("/api/notifications", headers=H(ADMIN)).json()["items"]
    assert any(x["event"] == "backup" and x["url"] == "/settings?s=backup" and x["event_title"] == "Резервные копии" for x in bell)


def test_stale_after_26_hours_alerts_once(status_dir):
    old = _run(started=_now() - timedelta(hours=30))
    _write(status_dir, last=old, runs=[old])
    c = TestClient(app)
    st = c.get("/api/operations/backups/status", headers=H(GD)).json()
    assert st["state"] == "stale" and st["stale"] is True
    assert st["last_success"]["run_key"] == old["run_key"]   # устаревший успех не выдаётся за свежий
    c.get("/api/operations/backups/status", headers=H(GD))
    assert len([n for n in _backup_notes(GD) if "дольше 26" in n.text]) == 1


def test_fresh_success_after_stale_clears_state(status_dir):
    old = _run(started=_now() - timedelta(hours=30))
    _write(status_dir, last=old, runs=[old])
    c = TestClient(app)
    assert c.get("/api/operations/backups/status", headers=H(GD)).json()["state"] == "stale"
    new = _run()
    _write(status_dir, last=new, runs=[new])
    assert c.get("/api/operations/backups/status", headers=H(GD)).json()["state"] == "ok"


def test_retention_failure_is_warning_not_hidden(status_dir):
    r = _run(retention_status="failed", error_code="retention_failed", error_summary="forget --prune завершился с ошибкой")
    _write(status_dir, last=r)
    st = TestClient(app).get("/api/operations/backups/status", headers=H(GD)).json()
    assert st["state"] == "warning" and st["last_run"]["retention_status"] == "failed"
    assert any("очистка старых снимков" in n.text for n in _backup_notes(GD))


def test_broken_status_file_is_reported_not_success(status_dir):
    _write(status_dir, last='{"schema":1,"status":"success"}')
    st = TestClient(app).get("/api/operations/backups/status", headers=H(GD)).json()
    assert st["host_status_available"] is False and st["status_errors"]
    assert st["state"] == "stale"   # файл есть, успеха нет — тревога, а не «всё хорошо»


def test_symlinked_status_file_ignored(status_dir):
    target = os.path.join(tempfile.mkdtemp(), "evil.json")
    with open(target, "w") as f:
        json.dump(_run(), f)
    os.symlink(target, os.path.join(status_dir, "last-run.json"))
    st = TestClient(app).get("/api/operations/backups/status", headers=H(GD)).json()
    assert st["host_status_available"] is False
