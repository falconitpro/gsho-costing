"""Журнал скачиваний и выгрузок, предупреждения безопасности, короткие ссылки S3 (docs/operations/12 AUD-01…04, FILE-06).

Сотрудники и сделки — синтетические; пороги правил проверяются на границе (5/6 выгрузок, 20/21 скачиваний)."""
from __future__ import annotations

import dataclasses
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core import download_audit  # noqa: E402
from app.core.auth import get_current_user, hash_password  # noqa: E402
from app.core.files import file_response  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AuthSession, NotificationLog, SecurityAlert, SecurityEvent, User  # noqa: E402
from app.services import security_alerts  # noqa: E402
from scripts.seed import DEV_PASSWORD, TEST_LOGIN, TEST_PASSWORD, run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
GD, ADM, MGR = TEST_LOGIN, "admin@example.com", "manager@example.com"
CANARY = "Canary-Secret-Pass-9137"


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    seed()


@pytest.fixture()
def c():
    with TestClient(app) as client:
        yield client


def make_user(roles=("manager",), name=None) -> tuple[str, str]:
    tag = uuid.uuid4().hex[:8]
    email = f"aud.{tag}@example.com"
    with SessionLocal() as db:
        u = User(email=email, external_id=email, display_name=name or f"Аудитов Тест {tag}", roles=list(roles),
                 password_hash=hash_password(CANARY), password_set_at=datetime.now(timezone.utc))
        db.add(u)
        db.commit()
        return u.id, email


def step_up(c, email):
    r = c.post("/api/auth/step-up", headers=H(email), json={"password": TEST_PASSWORD if email == TEST_LOGIN else DEV_PASSWORD})
    assert r.status_code == 200, r.text


def expire_step_up(email):
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.email == email))
        for s in db.scalars(select(AuthSession).where(AuthSession.user_id == uid)).all():
            s.last_step_up_at = None
        db.commit()


def events(actor=None, subject=None, etype=None, since=None) -> list[SecurityEvent]:
    with SessionLocal() as db:
        q = select(SecurityEvent)
        if actor:
            q = q.where(SecurityEvent.actor_user_id == actor)
        if subject:
            q = q.where(SecurityEvent.subject_user_id == subject)
        if etype:
            q = q.where(SecurityEvent.event_type == etype)
        if since:
            q = q.where(SecurityEvent.occurred_at >= since)
        return list(db.scalars(q.order_by(SecurityEvent.occurred_at)).all())


def alerts_for(subject, rule=None) -> list[SecurityAlert]:
    with SessionLocal() as db:
        q = select(SecurityAlert).where(SecurityAlert.subject_user_id == subject)
        if rule:
            q = q.where(SecurityAlert.rule_key == rule)
        return list(db.scalars(q.order_by(SecurityAlert.first_seen_at)).all())


def deal_with_doc(c) -> tuple[dict, dict]:
    cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
    d = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "аудит"}).json()
    r = c.post(f"/api/deals/{d['id']}/documents", headers=H(MGR), files={"file": ("Паспорт Иванова.pdf", b"%PDF-1.4 secret body")},
               data={"category": "client_spec"})
    assert r.status_code == 201, r.text
    doc = next(x for x in c.get(f"/api/deals/{d['id']}/documents", headers=H(MGR)).json()["documents"] if x["file_name"] == "Паспорт Иванова.pdf")
    return d, doc


# ------------------------------------------------------------------ AUD-01

def test_aud01_download_and_export_events_without_content(c):
    d, doc = deal_with_doc(c)
    gd_id, gd = make_user(("gd",))
    t0 = datetime.now(timezone.utc)
    r = c.get(doc["url"], headers=H(gd))
    assert r.status_code == 200 and r.content == b"%PDF-1.4 secret body"
    ev = events(actor=gd_id, etype="download", since=t0)
    assert len(ev) == 1
    e = ev[0]
    assert e.result == "success" and e.action == "download" and e.entity_type == "attachment" and e.deal_id == d["id"]
    assert e.format == "pdf" and e.byte_size == len(b"%PDF-1.4 secret body") and re.fullmatch(r"[0-9a-f-]{36}", e.request_id)
    assert e.session_id and e.meta["route"] == "/api/files/{path:path}"
    blob = repr(e.meta) + e.entity_id
    assert "Иванова" not in blob and "secret body" not in blob and "/api/files/" + doc["url"] not in blob

    # чужая сделка: менеджер не участник → 404 и событие denied без данных файла
    m_id, m = make_user(("manager",))
    r = c.get(doc["url"], headers=H(m))
    assert r.status_code == 404 and b"secret" not in r.content
    den = events(actor=m_id, etype="download", since=t0)
    assert [x.result for x in den] == ["denied"] and den[0].byte_size is None and den[0].format == ""

    # выгрузка финансов: число строк, фильтры по белому списку, текст поиска не хранится
    fd_id, fd = make_user(("fd",))
    r = c.get("/api/finance/export?kind=debt&direction=in&search=" + CANARY, headers=H(fd))
    assert r.status_code == 200
    ex = events(actor=fd_id, etype="export", since=t0)
    assert len(ex) == 1 and ex[0].format == "xlsx" and ex[0].row_count is not None and ex[0].entity_type == "finance_debt"
    assert ex[0].meta["filters"]["direction"] == "in" and ex[0].meta["filters"]["search"] is True
    assert CANARY not in repr(ex[0].meta)
    # отказ в выгрузке
    r = c.get("/api/finance/export?kind=debt", headers=H(m))
    assert r.status_code == 403
    assert [x.result for x in events(actor=m_id, etype="export", since=t0)] == ["denied"]


def test_aud01_unauthenticated_denied_logged(c):
    t0 = datetime.now(timezone.utc)
    r = TestClient(app).get("/api/admin/hs/export")
    assert r.status_code == 401
    ev = [e for e in events(etype="export", since=t0) if e.meta.get("route") == "/api/admin/hs/export"]
    assert ev and ev[-1].result == "denied" and ev[-1].actor_user_id is None


def test_fail_closed_when_journal_unavailable(c, monkeypatch):
    a_id, adm = make_user(("admin",))

    def broken(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(download_audit, "_write", broken)
    r = c.get("/api/admin/hs/export", headers=H(adm))
    assert r.status_code == 503 and r.json()["detail"]["code"] == "audit_unavailable"
    assert b"code;description" not in r.content
    monkeypatch.undo()
    assert c.get("/api/admin/hs/export", headers=H(adm)).status_code == 200


# ------------------------------------------------------------------ AUD-02

def _install_probe_routes():
    if any(getattr(r, "path", "") == "/api/_probe/attach" for r in download_audit.walk_routes(app.routes)):
        return
    from fastapi import Depends
    p = Path(tempfile.mkdtemp()) / "probe.png"
    p.write_bytes(b"\x89PNG probe")

    @app.get("/api/_probe/attach")
    def _attach(user=Depends(get_current_user)):
        return file_response(str(p), "probe.docx", inline=False)

    @app.get("/api/_probe/inline")
    def _inline(user=Depends(get_current_user)):
        return file_response(str(p), "probe.png", inline=True)
    download_audit.reset_cache()


def test_aud02_any_new_file_route_is_audited_without_hooks(c, monkeypatch):
    """Маршрут без единой строчки журнала всё равно попадает в журнал: прослойка ловит любой файл."""
    _install_probe_routes()
    u_id, u = make_user(("manager",))
    t0 = datetime.now(timezone.utc)
    assert c.get("/api/_probe/attach", headers=H(u)).status_code == 200
    assert c.get("/api/_probe/inline", headers=H(u)).status_code == 200
    ev = events(actor=u_id, etype="download", since=t0)
    assert [(e.action, e.meta.get("inline", False), e.format) for e in ev] == [("download", False, "docx"), ("view", True, "png")]
    # D-046: просмотр картинки inline не блокируется сбоем журнала, закрытый файл — блокируется
    monkeypatch.setattr(download_audit, "_write", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert c.get("/api/_probe/inline", headers=H(u)).status_code == 200
    assert c.get("/api/_probe/attach", headers=H(u)).status_code == 503


def test_aud02_inventory_routes_covered_for_denials():
    """Каждый маршрут выдачи файлов из инвентаризации этапа 0 распознан прослойкой (успех ловится по заголовку везде,
    а отказы — по этому списку)."""
    report = Path(__file__).resolve().parents[2] / "docs/operations/reports/00_inventory_routes.md"
    rows = [ln.split("|") for ln in report.read_text(encoding="utf-8").splitlines() if ln.startswith("| GET")]
    paths = {r[2].strip().strip("`") for r in rows if any(k in r[4] for k in ("download", "export", "presign"))}
    assert len(paths) >= 30
    known = download_audit.file_routes(app)
    live = {getattr(r, "path", "") for r in download_audit.walk_routes(app.routes)}
    missing = sorted(p for p in paths if p in live and p not in known)
    assert not missing, missing


# ------------------------------------------------------------------ AUD-03

def test_aud03_export_burst_threshold_dedup_and_recipients(c):
    a_id, adm = make_user(("admin",))
    for _ in range(5):
        assert c.get("/api/admin/hs/export", headers=H(adm)).status_code == 200
    assert alerts_for(a_id, "export_burst") == []
    assert c.get("/api/admin/hs/export", headers=H(adm)).status_code == 200
    al = alerts_for(a_id, "export_burst")
    assert len(al) == 1 and al[0].occurrence_count == 1 and al[0].severity == "high" and al[0].status == "new"
    first_seen = al[0].last_seen_at
    assert c.get("/api/admin/hs/export", headers=H(adm)).status_code == 200
    al = alerts_for(a_id, "export_burst")
    assert len(al) == 1 and al[0].occurrence_count == 2 and al[0].last_seen_at >= first_seen
    # событие ссылается на предупреждение; уведомление — только держателям security_audit, один раз
    ev = events(actor=a_id, etype="export")
    assert ev[-1].alert_id == al[0].id and ev[0].alert_id is None
    with SessionLocal() as db:
        name = db.get(User, a_id).display_name
        notes = db.scalars(select(NotificationLog).where(NotificationLog.event == "security_alert", NotificationLog.text.contains(name))).all()
        mgr = db.scalar(select(User.id).where(User.email == MGR))
        gd = db.scalar(select(User.id).where(User.email == GD))
    recipients = [n.user_id for n in notes]
    assert gd in recipients and a_id in recipients and mgr not in recipients and all(n.channel == "app" for n in notes)
    assert recipients.count(gd) == 1


def test_aud03_download_burst_and_denied_burst(c):
    _, doc = deal_with_doc(c)
    g_id, g = make_user(("gd",))
    for _ in range(20):
        assert c.get(doc["url"], headers=H(g)).status_code == 200
    assert alerts_for(g_id, "download_burst") == []
    assert c.get(doc["url"], headers=H(g)).status_code == 200
    assert len(alerts_for(g_id, "download_burst")) == 1
    # картинки «как страница» в порог не входят
    _install_probe_routes()
    v_id, v = make_user(("gd",))
    for _ in range(22):
        c.get("/api/_probe/inline", headers=H(v))
    assert alerts_for(v_id, "download_burst") == []
    # 10 отказов подряд → предупреждение «повторные отказы», нарушитель уведомления не получает
    m_id, m = make_user(("manager",))
    for _ in range(9):
        assert c.get(doc["url"], headers=H(m)).status_code == 404
    assert alerts_for(m_id, "denied_burst") == []
    c.get(doc["url"], headers=H(m))
    assert len(alerts_for(m_id, "denied_burst")) == 1
    with SessionLocal() as db:
        assert not db.scalars(select(NotificationLog).where(NotificationLog.user_id == m_id, NotificationLog.event == "security_alert")).all()


def test_large_export_rule_unit():
    with SessionLocal() as db:
        uid = make_user(("fd",))[0]
        ev = SecurityEvent(id=str(uuid.uuid4()), event_type="export", action="export", result="success", actor_user_id=uid, row_count=999, meta={})
        assert [h[0] for h in security_alerts.match(db, ev)] == []
        ev.row_count = 1000
        assert [h[:2] for h in security_alerts.match(db, ev)] == [("large_export", "medium")]
        ev.row_count = 10000
        assert [h[:2] for h in security_alerts.match(db, ev)] == [("large_export", "high")]


# ------------------------------------------------------------------ AUD-04

def test_aud04_access_changes_raise_alerts(c):
    expire_step_up(GD)
    u_id, u = make_user(("manager",))
    # исключение прав сотруднику
    r = c.post("/api/admin/access/overrides", headers=H(GD), json={"user_id": u_id, "group": "images", "level": 2, "comment": "проект"})
    assert r.status_code == 201, r.text
    al = alerts_for(u_id, "access_change")
    assert len(al) == 1 and al[0].severity == "high" and al[0].details_sanitized["group"] == "images"
    ov = r.json()["id"]
    assert c.delete(f"/api/admin/access/overrides/{ov}", headers=H(GD)).status_code == 204
    assert alerts_for(u_id, "access_change")[0].occurrence_count == 2
    # повышение прав безопасности — только с подтверждением
    r = c.post("/api/admin/access/overrides", headers=H(GD), json={"user_id": u_id, "group": "security_audit", "level": 1, "comment": "аудит"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "step_up_required"
    step_up(c, GD)
    assert c.post("/api/admin/access/overrides", headers=H(GD), json={"user_id": u_id, "group": "security_audit", "level": 1, "comment": "аудит"}).status_code == 201
    # роли сотрудника
    assert c.patch(f"/api/admin/users/{u_id}", headers=H(GD), json={"roles": ["logistician"]}).status_code == 200
    ev = [e for e in events(subject=u_id, etype="access") if e.action == "roles_changed"]
    assert ev and ev[0].meta["roles_added"] == ["logistician"] and ev[0].meta["roles_removed"] == ["manager"]
    # стартовый уровень роли — общая запись без сотрудника
    rules = c.get("/api/admin/access", headers=H(GD)).json()
    cur = next(r for r in rules["roles"] if r["role"] == "observer")["levels"]["images"]
    r = c.put("/api/admin/access/observer/images", headers=H(GD), json={"level": 0 if cur else 1})
    assert r.status_code == 200, r.text
    c.put("/api/admin/access/observer/images", headers=H(GD), json={"level": cur})
    with SessionLocal() as db:
        assert db.scalar(select(SecurityAlert).where(SecurityAlert.rule_key == "access_change", SecurityAlert.subject_user_id.is_(None),
                                                    SecurityAlert.status != "resolved")) is not None
    # сброс TOTP — critical, отключение доступа — high; в деталях нет пароля
    assert c.post(f"/api/security/users/{u_id}/mfa/reset", headers=H(GD)).status_code == 200
    crit = alerts_for(u_id, "mfa_reset")
    assert len(crit) == 1 and crit[0].severity == "critical"
    with SessionLocal() as db:
        name = db.get(User, u_id).display_name
    r = c.post(f"/api/security/users/{u_id}/offboard", headers=H(GD), json={"confirm_name": name, "reason": "тест"})
    assert r.status_code == 200, r.text
    assert alerts_for(u_id, "offboard")[0].severity == "high"
    for a in alerts_for(u_id):
        assert CANARY not in repr(a.details_sanitized) and "password" not in repr(a.details_sanitized)
    expire_step_up(GD)


# ------------------------------------------------------------------ работа с предупреждениями

def test_alert_ack_resolve_rules(c):
    expire_step_up(GD)
    u_id, _ = make_user(("manager",))
    step_up(c, GD)
    assert c.post(f"/api/security/users/{u_id}/mfa/reset", headers=H(GD)).status_code == 200
    expire_step_up(GD)
    a = alerts_for(u_id, "mfa_reset")[0]
    # менеджеру журнал закрыт
    assert c.get("/api/security/alerts", headers=H(MGR)).status_code == 403
    # аудитор только читает
    aud_id, aud = make_user(("observer",))
    with SessionLocal() as db:
        from app.models import AccessUserOverride
        db.add(AccessUserOverride(user_id=aud_id, field_group="security_audit", level=1, comment="тест", created_by=aud_id))
        db.commit()
    lst = c.get("/api/security/alerts?status=open&severity=critical", headers=H(aud)).json()
    assert any(x["id"] == a.id for x in lst["items"]) and lst["can_act"] is False
    assert c.post(f"/api/security/alerts/{a.id}/ack", headers=H(aud), json={"comment": "смотрю"}).status_code == 403
    card = c.get(f"/api/security/alerts/{a.id}", headers=H(aud)).json()
    assert card["events"] and card["events"][0]["action"] == "admin_reset"
    # комментарий обязателен
    assert c.post(f"/api/security/alerts/{a.id}/ack", headers=H(GD), json={"comment": " "}).status_code == 422
    r = c.post(f"/api/security/alerts/{a.id}/ack", headers=H(GD), json={"comment": "Сотрудник потерял телефон, подтверждено"})
    assert r.status_code == 200 and r.json()["status"] == "acknowledged"
    # критическое закрывается с подтверждением
    r = c.post(f"/api/security/alerts/{a.id}/resolve", headers=H(GD), json={"comment": "Настроил новый телефон"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "step_up_required"
    step_up(c, GD)
    r = c.post(f"/api/security/alerts/{a.id}/resolve", headers=H(GD), json={"comment": "Настроил новый телефон"})
    assert r.status_code == 200 and r.json()["status"] == "resolved" and r.json()["resolution_comment"] == "Настроил новый телефон"
    assert c.post(f"/api/security/alerts/{a.id}/resolve", headers=H(GD), json={"comment": "ещё раз"}).status_code == 409
    # действия журналируются; после закрытия новый случай — новая запись, история остаётся
    acts = [e.action for e in events(etype="alert") if e.entity_id == a.id]
    assert acts == ["acknowledged", "resolved"]
    assert c.post(f"/api/security/users/{u_id}/mfa/reset", headers=H(GD)).status_code == 200
    al = alerts_for(u_id, "mfa_reset")
    assert len(al) == 2 and al[0].status == "resolved" and al[1].status == "new"
    expire_step_up(GD)
    # журнал событий с фильтрами
    j = c.get(f"/api/security/events?user_id={u_id}&event_type=mfa", headers=H(aud)).json()
    assert j["total"] >= 2 and all(x["type"] == "mfa" for x in j["items"]) and j["items"][0]["actor_name"]


# ------------------------------------------------------------------ FILE-06

class _FakeS3:
    def __init__(self):
        self.calls = []

    def generate_presigned_url(self, op, Params, ExpiresIn):
        self.calls.append((op, Params, ExpiresIn))
        return f"https://storage.example/{Params['Key']}?X-Amz-Signature=deadbeef&X-Amz-Expires={ExpiresIn}"


def test_file06_s3_short_link_after_authorization(c, monkeypatch):
    from app import adapters
    from app.adapters.storage import S3Storage
    from app.api import tasks_files_planfact as tfp
    from app.core.config import get_settings
    d, doc = deal_with_doc(c)
    fake = _FakeS3()
    s3 = dataclasses.replace(get_settings(), storage_provider="s3")
    st = S3Storage(s3)
    monkeypatch.setattr(st, "_client", lambda: fake)
    assert st.link("a/b.pdf") == "/api/files/a/b.pdf"   # постоянный адрес — маршрут системы, не подписанная ссылка
    monkeypatch.setattr(tfp, "get_settings", lambda: s3)
    monkeypatch.setattr(adapters, "storage", lambda s=None: st)
    # нет прав → ссылки не создаётся
    m_id, m = make_user(("manager",))
    assert c.get(doc["url"], headers=H(m), follow_redirects=False).status_code == 404 and fake.calls == []
    g_id, g = make_user(("gd",))
    t0 = datetime.now(timezone.utc)
    r = c.get(doc["url"], headers=H(g), follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].startswith("https://storage.example/")
    op, params, ttl = fake.calls[-1]
    assert ttl <= 300 and params["ResponseContentDisposition"].startswith("attachment") and op == "get_object"
    ev = events(actor=g_id, etype="download", since=t0)
    assert len(ev) == 1 and ev[0].meta["delivery"] == "presigned_300s" and "X-Amz" not in repr(ev[0].meta) and "storage.example" not in repr(ev[0].meta)
