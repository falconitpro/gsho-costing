"""Проверка загружаемых файлов и классификация сред (docs/operations/12 FILE-01…04, FILE-07, 04 §10).

В CI работает поддельный clamd (тот же протокол INSTREAM): «заражён» любой поток со строкой тестовой сигнатуры EICAR.
Если задан CLAMD_TEST_HOST — дополнительно проверка с настоящим clamd. Настоящего вредоносного кода нет."""
from __future__ import annotations

import io
import os
import socket
import struct
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi import Depends, File, UploadFile  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core import environment, upload_gate  # noqa: E402,F401
from app.core.auth import get_current_user  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.core.download_audit import walk_routes  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AppMarker, Attachment, AttachmentScan, HsCode, SecurityAlert, SecurityEvent, User  # noqa: E402
from app.services import file_scanner, scanner_status  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, ADM = "manager@example.com", "cd@example.com", "admin@example.com"
# Тестовая строка EICAR собирается из частей, чтобы сам файл теста не срабатывал у антивирусов
EICAR = ("X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-" + "TEST-FILE!$H+H*").encode()


class FakeClamd:
    """Минимальный clamd: VERSION, INSTREAM. mode: ok | slow (молчит дольше таймаута)."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.mode = "ok"
        self.scanned = 0
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        with c:
            cmd = b""
            while not cmd.endswith(b"\0"):
                part = c.recv(1)
                if not part:
                    return
                cmd += part
            if cmd == b"zVERSION\0":
                c.sendall(b"ClamAV 1.4.3/27450/Wed Nov 11 08:00:00 2026\0")
                return
            data = b""
            while True:
                hdr = b""
                while len(hdr) < 4:
                    hdr += c.recv(4 - len(hdr))
                n = struct.unpack(">I", hdr)[0]
                if n == 0:
                    break
                while n:
                    chunk = c.recv(min(n, 65536))
                    data += chunk
                    n -= len(chunk)
            self.scanned += 1
            if self.mode == "slow":
                time.sleep(3)
            c.sendall(b"stream: Eicar-Test-Signature FOUND\0" if EICAR in data else b"stream: OK\0")


@pytest.fixture(scope="module")
def clamd():
    seed()
    return FakeClamd()


@pytest.fixture()
def scan_on(clamd):
    s = get_settings()
    old = (s.file_scan, s.clamd_host, s.clamd_port, s.clamd_timeout)
    s.file_scan, s.clamd_host, s.clamd_port, s.clamd_timeout = "clamav", "127.0.0.1", clamd.port, 2
    clamd.mode = "ok"
    yield clamd
    s.file_scan, s.clamd_host, s.clamd_port, s.clamd_timeout = old


@pytest.fixture()
def c():
    with TestClient(app) as client:
        yield client


def new_deal(c) -> str:
    cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
    return c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "скан"}).json()["id"]


def upload(c, deal_id, name, body, who=MGR):
    return c.post(f"/api/deals/{deal_id}/documents", headers=H(who), files={"file": (name, body)}, data={"category": "client_spec"})


def att_by_name(deal_id, name) -> list[Attachment]:
    with SessionLocal() as db:
        return list(db.scalars(select(Attachment).where(Attachment.deal_id == deal_id, Attachment.file_name == name)).all())


def scans_since(t0) -> list[AttachmentScan]:
    with SessionLocal() as db:
        return list(db.scalars(select(AttachmentScan).where(AttachmentScan.created_at >= t0).order_by(AttachmentScan.created_at)).all())


def mgr_id():
    with SessionLocal() as db:
        return db.scalar(select(User.id).where(User.email == MGR))


# ------------------------------------------------------------------ FILE-01

def test_file01_clean_upload_scanned_promoted_and_linked(c, scan_on):
    d = new_deal(c)
    t0 = datetime.now(timezone.utc)
    r = upload(c, d, "ТЗ клиента.pdf", b"%PDF-1.4 clean")
    assert r.status_code == 201, r.text
    [a] = att_by_name(d, "ТЗ клиента.pdf")
    assert a.scan_status == "clean" and a.scanned_at is not None
    [s] = scans_since(t0)
    assert s.status == "clean" and s.attachment_id == a.id and s.sha256 == a.sha256 and s.engine_version == "1.4.3" and s.signature_version == "27450"
    assert s.uploaded_by == mgr_id() and s.route == "/api/deals/{id}/documents"
    doc = next(x for x in c.get(f"/api/deals/{d}/documents", headers=H(MGR)).json()["documents"] if x["file_name"] == "ТЗ клиента.pdf")
    r = c.get(doc["url"], headers=H(CD))
    assert r.status_code == 200 and r.content == b"%PDF-1.4 clean" and r.headers["x-content-type-options"] == "nosniff"


# ------------------------------------------------------------------ FILE-02

def test_file02_infected_rejected_not_stored_evidence_kept(c, scan_on):
    d = new_deal(c)
    t0 = datetime.now(timezone.utc)
    name = f"счёт-{uuid.uuid4().hex[:8]}.pdf"
    r = upload(c, d, name, b"%PDF " + EICAR)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "file_infected"
    assert "Eicar-Test-Signature" in r.json()["detail"]["message"]
    assert att_by_name(d, name) == []
    [s] = scans_since(t0)
    assert s.status == "infected" and s.detected_name == "Eicar-Test-Signature" and len(s.sha256) == 64 and s.size > 0 and s.attachment_id is None
    root = get_settings().storage_local_root
    assert not any(name[:-4] in f for _, _, fs in os.walk(root) for f in fs)
    with SessionLocal() as db:
        ev = db.scalar(select(SecurityEvent).where(SecurityEvent.event_type == "file", SecurityEvent.entity_id == s.id))
        assert ev.action == "infected" and ev.result == "denied" and ev.meta["threat"] == "Eicar-Test-Signature" and "счёт" not in repr(ev.meta)
        al = db.get(SecurityAlert, ev.alert_id)
        assert al.rule_key == "file_infected" and al.severity == "critical" and al.subject_user_id == mgr_id()


# ------------------------------------------------------------------ FILE-03

def test_file03_scanner_down_or_slow_fail_closed_import_not_parsed(c, scan_on):
    d = new_deal(c)
    s = get_settings()
    s.clamd_port = 1   # никто не слушает
    r = upload(c, d, "договор.pdf", b"%PDF down")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "scan_unavailable"
    assert att_by_name(d, "договор.pdf") == []
    # импорт не разбирается, пока файл не проверен
    code = "84295" + str(uuid.uuid4().int)[:5]
    csv = f"code;description;duty_pct;vat_pct\n{code};тест;5;20\n".encode()
    r = c.post("/api/admin/hs/upload", headers=H(ADM), files={"file": ("hs.csv", csv)})
    assert r.status_code == 503
    with SessionLocal() as db:
        assert db.scalar(select(HsCode).where(HsCode.code == code)) is None
        assert db.scalar(select(SecurityAlert).where(SecurityAlert.rule_key == "scanner_unavailable", SecurityAlert.status != "resolved")) is not None
    # таймаут
    s.clamd_port = scan_on.port
    scan_on.mode = "slow"
    t0 = datetime.now(timezone.utc)
    r = upload(c, d, "медленно.pdf", b"%PDF slow")
    assert r.status_code == 503 and scans_since(t0)[-1].last_error_code == "timeout"
    # после восстановления — повторная загрузка проходит; автоматического clean нет
    scan_on.mode = "ok"
    assert upload(c, d, "договор.pdf", b"%PDF down").status_code == 201
    assert [a.scan_status for a in att_by_name(d, "договор.pdf")] == ["clean"]


# ------------------------------------------------------------------ FILE-04 и старые файлы

def test_file04_legacy_unscanned_available_and_blocked_status(c, clamd):
    assert get_settings().file_scan == "off"
    d = new_deal(c)
    assert upload(c, d, "старый.pdf", b"%PDF legacy").status_code == 201
    [a] = att_by_name(d, "старый.pdf")
    assert a.scan_status == "legacy_unscanned"
    url = next(x for x in c.get(f"/api/deals/{d}/documents", headers=H(MGR)).json()["documents"] if x["file_name"] == "старый.pdf")["url"]
    assert c.get(url, headers=H(CD)).status_code == 200
    # чужой менеджер по-прежнему не получает
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.add(User(email=f"scan.{tag}@example.com", external_id=f"scan.{tag}@example.com", display_name="Чужой Менеджер", roles=["manager"]))
        db.commit()
    assert c.get(url, headers=H(f"scan.{tag}@example.com")).status_code == 404
    # заражённый по итогам проверки хранилища — не выдаётся
    with SessionLocal() as db:
        db.get(Attachment, a.id).scan_status = "infected"
        db.commit()
    assert c.get(url, headers=H(CD)).status_code == 410


def test_scan_storage_moves_infected_to_quarantine(c, scan_on, monkeypatch, tmp_path):
    from scripts import scan_storage
    d = new_deal(c)
    s = get_settings()
    s.file_scan = "off"     # «старые» файлы: загружены до включения проверки
    assert upload(c, d, "архив_старый.pdf", b"%PDF " + EICAR).status_code == 201
    assert upload(c, d, "норм_старый.pdf", b"%PDF fine").status_code == 201
    s.file_scan = "clamav"
    monkeypatch.setattr(s, "quarantine_dir", str(tmp_path / "q"))
    [bad], [good] = att_by_name(d, "архив_старый.pdf"), att_by_name(d, "норм_старый.pdf")
    assert scan_storage.main([]) in (0, 1)
    with SessionLocal() as db:
        bad, good = db.get(Attachment, bad.id), db.get(Attachment, good.id)
        assert bad.scan_status == "infected" and good.scan_status == "clean"
        row = db.scalar(select(AttachmentScan).where(AttachmentScan.storage_path == bad.storage_path, AttachmentScan.source == "storage"))
    assert not os.path.exists(os.path.join(s.storage_local_root, bad.storage_path))
    q = tmp_path / "q" / row.quarantine_key
    assert q.exists() and oct(q.stat().st_mode)[-3:] == "600"
    url = f"/api/files/{bad.storage_path}"
    assert c.get(url, headers=H(CD)).status_code == 410


# ------------------------------------------------------------------ FILE-07, обход, без входа

def _zip_bomb() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", b"\0" * (30 * 1024 * 1024))
    return buf.getvalue()


def _nested(depth: int) -> bytes:
    data = b"payload"
    name = "a.txt"
    for i in range(depth):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(name, data)
        data, name = buf.getvalue(), f"level{i}.zip"
    return data


def test_file07_zip_bomb_and_depth_rejected_before_parsing(c, clamd):
    d = new_deal(c)
    bomb = _zip_bomb()
    assert len(bomb) < 200 * 1024
    r = upload(c, d, "смета.xlsx", bomb)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "file_rejected" and "zip-бомба" in r.json()["detail"]["message"]
    assert file_scanner.zip_guard(io.BytesIO(_nested(2)), "x.zip") is None
    assert "вложенность" in file_scanner.zip_guard(io.BytesIO(_nested(4)), "x.zip")
    assert file_scanner.zip_guard(io.BytesIO(b"%PDF"), "a.pdf") is None


def test_unauthenticated_upload_not_scanned(c, scan_on):
    before = scan_on.scanned
    r = TestClient(app).post("/api/admin/hs/upload", files={"file": ("hs.csv", b"code;description\n1;x\n")})
    assert r.status_code == 401 and scan_on.scanned == before


def test_new_upload_route_is_gated_without_hooks(c, scan_on):
    """Маршрут с загрузкой, добавленный без единой строчки проверки, всё равно проходит антивирус."""
    if not any(getattr(r, "path", "") == "/api/_probe/upload" for r in walk_routes(app.routes)):
        @app.post("/api/_probe/upload")
        async def _probe(file: UploadFile = File(...), user=Depends(get_current_user)):
            return {"size": len(await file.read())}
    r = c.post("/api/_probe/upload", headers=H(MGR), files={"file": ("x.bin", b"abc" * 1000)})
    assert r.status_code == 200 and r.json()["size"] == 3000     # тело заново отдано приложению целиком
    r = c.post("/api/_probe/upload", headers=H(MGR), files={"file": ("x.bin", EICAR)})
    assert r.status_code == 422


# ------------------------------------------------------------------ состояние антивируса

def test_scanner_status_and_hourly_alerts(c, scan_on):
    st = scanner_status.status(get_settings())
    assert st["available"] and st["engine"] == "1.4.3" and st["signatures"] == 27450
    r = c.get("/api/security/scanner", headers=H("evgeniy@dobrov.pro"))
    assert r.status_code == 200 and r.json()["mode"] == "clamav" and "recent" in r.json() and "storage_path" not in repr(r.json())
    assert c.get("/api/security/scanner", headers=H(MGR)).status_code == 403
    with SessionLocal() as db:
        st = scanner_status.hourly(db, get_settings())      # базы «от 11.11.2026 08:00» — устаревают через 48 ч
        if st["stale"]:
            assert db.scalar(select(SecurityAlert).where(SecurityAlert.rule_key == "scanner_stale", SecurityAlert.status != "resolved"))
    get_settings().clamd_port = 1
    with SessionLocal() as db:
        st = scanner_status.hourly(db, get_settings())
        assert st["available"] is False
        assert db.scalar(select(SecurityAlert).where(SecurityAlert.rule_key == "scanner_unavailable", SecurityAlert.status != "resolved"))


@pytest.mark.skipif(not os.environ.get("CLAMD_TEST_HOST"), reason="настоящий clamd не задан (CLAMD_TEST_HOST)")
def test_real_clamd_detects_eicar():
    host, port = os.environ["CLAMD_TEST_HOST"], int(os.environ.get("CLAMD_TEST_PORT", "3310"))
    assert file_scanner.scan_stream(io.BytesIO(b"plain text"), host, port).status == "clean"
    res = file_scanner.scan_stream(io.BytesIO(EICAR), host, port)
    assert res.status == "infected" and "Eicar" in res.signature


# ------------------------------------------------------------------ классификация сред (04 §10)

def _s(**kw) -> Settings:
    import dataclasses
    return dataclasses.replace(get_settings(), **kw)


def test_unsafe_config_combinations_refused():
    assert environment.config_problems(_s(env="dev")) == []
    assert environment.config_problems(_s(env="dev", data_classification="production"))
    p = environment.config_problems(_s(env="production", auth_provider="dev", file_scan="off"))
    assert any("AUTH_PROVIDER=dev" in x for x in p) and any("FILE_SCAN=clamav" in x for x in p)
    assert environment.config_problems(_s(env="production", auth_provider="password", file_scan="clamav")) == []
    assert environment.config_problems(_s(env="prod", auth_provider="password", file_scan="off"))   # в проекте рабочий режим — prod
    p = environment.config_problems(_s(env="recovery", mail_provider="smtp", messenger_provider="wecom", storage_provider="s3"))
    assert len(p) == 3
    assert environment.config_problems(_s(env="recovery", mail_provider="console", messenger_provider="console", storage_provider="local")) == []


def test_database_marker_blocks_test_env_on_production_data():
    with SessionLocal() as db:
        m = db.get(AppMarker, environment.MARKER)
        saved = m.value if m else None
        if m is None:
            environment.check(db, _s(env="dev"))
            m = db.get(AppMarker, environment.MARKER)
        assert environment.check(db, _s(env="dev")) == "synthetic"
        m.value = "production"
        db.commit()
        with pytest.raises(environment.UnsafeEnvironment, match="боевыми данными"):
            environment.check(db, _s(env="dev"))
        rec = _s(env="recovery", mail_provider="console", messenger_provider="console", storage_provider="local")
        assert environment.check(db, rec) == "recovery"
        # после drill копия помечена recovery — тестовая среда на ней тоже не запустится
        with pytest.raises(environment.UnsafeEnvironment):
            environment.check(db, _s(env="dev"))
        m.value = saved or "synthetic"
        db.commit()


def test_load_smoke_script_passes_and_fails_when_scanner_down(capsys):
    """FILE-08: скрипт нагрузочной проверки для сервера — PASS с антивирусом, код 2 без него; в базу не пишет."""
    from app.core.config import get_settings
    from scripts import load_smoke
    fake, s = FakeClamd(), get_settings()
    old = (s.clamd_host, s.clamd_port)
    s.clamd_host, s.clamd_port = "127.0.0.1", fake.port
    try:
        assert load_smoke.main(["--files", "6", "--mb", "1", "--workers", "3"]) == 0
        out = capsys.readouterr().out
        assert "ИТОГ: PASS" in out and "найдена в 1 из 1" in out and fake.scanned == 6
        fake.sock.close()
        s.clamd_port = 1
        assert load_smoke.main(["--files", "1"]) == 2
    finally:
        s.clamd_host, s.clamd_port = old
