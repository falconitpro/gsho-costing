"""D-155: подтверждения к поручениям, напоминания о сроках, история сигналов, карточка направления, «устарело» у дебиторки."""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from scripts.seed import run as seed  # noqa: E402
from tests.test_workplace import CD, FD, GD, H, MGR  # noqa: E402

B = "/api/management"


def _id(c, email):
    return c.get("/api/auth/me", headers=H(email)).json()["id"]


def test_files_reminders_signals_direction():
    seed()
    from app.db import SessionLocal
    from app.models import Assignment, NotificationLog, SignalLog
    from app.services import assignments as svc
    from app.services import signals
    today = date.today()
    with TestClient(app) as c:
        mgr, cd = _id(c, MGR), _id(c, CD)
        c.put(f"{B}/directions", headers=H(GD), json={"heads": {"sales": cd}})
        a = c.post(f"{B}/assignments", headers=H(GD), json={"title": "Сверить оплату", "assignee_id": mgr, "due_on": (today + timedelta(days=1)).isoformat()}).json()
        aid = a["id"]
        # файлы: исполнитель прикладывает, руководитель (только видит) — нет, посторонний не скачивает
        up = c.post(f"{B}/assignments/{aid}/files", headers=H(MGR), files=[("files", ("п-п 123.pdf", b"%PDF-1.4 test", "application/pdf"))])
        assert up.status_code == 200, up.text
        f = up.json()["files"][0]
        assert f["name"] == "п-п 123.pdf" and f["can_remove"]
        assert c.post(f"{B}/assignments/{aid}/files", headers=H(CD), files=[("files", ("x.txt", b"x", "text/plain"))]).status_code == 403
        assert c.get(f["url"], headers=H(MGR)).content == b"%PDF-1.4 test"
        assert c.get(f["url"], headers=H(CD)).status_code == 200                 # руководитель видит поручение — видит и файл
        assert c.get(f["url"], headers=H(FD)).status_code == 404
        assert c.delete(f"{B}/assignments/{aid}/files/{f['id']}", headers=H(GD)).status_code == 403   # убирает только приложивший
        assert c.post(f"{B}/assignments/{aid}/files", headers=H(MGR), files=[("files", ("../../etc/passwd", b"x", "text/plain"))]).json()["files"][1]["name"] == "passwd"
        hist = c.get(f"{B}/assignments/{aid}", headers=H(GD)).json()["history"]
        assert [h["action"] for h in hist].count("file") == 2

    # напоминания: накануне — исполнителю один раз; просрочка — исполнителю, автору и руководителю
    with SessionLocal() as db:
        n0 = db.query(NotificationLog).filter(NotificationLog.event == "assignment").count()
        assert svc.check(db, today) >= 1 and svc.check(db, today) == 0
        db.commit()
        a_row = db.get(Assignment, aid)
        assert f"pre:{a_row.due_on}" in a_row.alerts
        assert svc.check(db, today + timedelta(days=3)) >= 1 and svc.check(db, today + timedelta(days=3)) == 0   # просрочка: один сигнал на срок
        db.commit()
        over = db.query(NotificationLog).filter(NotificationLog.event == "assignment", NotificationLog.text.like("%просрочено%"),
                                                NotificationLog.link == f"/assignments?open={aid}").all()
        assert {mgr, cd} <= {x.user_id for x in over}                            # исполнитель и руководитель направления
        assert db.query(NotificationLog).filter(NotificationLog.event == "assignment").count() > n0
        assert all(x.link == f"/assignments?open={aid}" for x in over)

    # история сигналов: ключ ушёл из очереди → устранён; вернулся → снова открыт
    with SessionLocal() as db:
        signals.sync(db, [{"key": "t:1", "priority": "high", "title": "A", "url": "/deals"}, {"key": "t:2", "priority": "normal", "title": "B", "url": "/x"}])
        db.commit()
        signals.sync(db, [{"key": "t:2", "priority": "normal", "title": "B", "url": "/x"}])
        db.commit()
        assert db.get(SignalLog, "t:1").resolved_at is not None and db.get(SignalLog, "t:2").resolved_at is None
        assert any(h["key"] == "t:1" for h in signals.history(db))
        signals.sync(db, [{"key": "t:1", "priority": "high", "title": "A", "url": "/deals"}])
        db.commit()
        r = db.get(SignalLog, "t:1")
        assert r.resolved_at is None and r.times == 2

    with TestClient(app) as c:
        assert c.get(f"{B}/signals/history", headers=H(MGR)).status_code == 403
        assert isinstance(c.get(f"{B}/signals/history", headers=H(GD)).json()["items"], list)
        # карточка направления
        assert c.get("/api/workplace/executive/direction/sales", headers=H(MGR)).status_code == 403
        assert c.get("/api/workplace/executive/direction/nope", headers=H(GD)).status_code == 404
        d = c.get("/api/workplace/executive/direction/sales", headers=H(GD)).json()
        assert d["head"]["id"] == cd and any(s["is_head"] for s in d["staff"]) and any(s["id"] == mgr for s in d["staff"])
        assert any(x["id"] == aid for x in d["assignments"]) and isinstance(d["objects"], list)
        assert any("руководитель" in h["text"] for h in d["history"])
        # дебиторка: качество данных зависит от свежести банковских операций
        ex = c.get("/api/workplace/executive", headers=H(GD)).json()["executive"]
        esc_keys = {e["key"] for e in ex["escalations"]}
        k = next(x for x in ex["kpis"] if x["key"] == "receivable")
        assert k["data_quality"] in ("confirmed", "stale", "unverified")
        if k["data_quality"] != "confirmed":
            assert k["quality_note"]
    with SessionLocal() as db:   # открытие пульта сохраняет журнал сигналов (commit в обработчике)
        open_keys = {r.key for r in db.query(SignalLog).filter(SignalLog.resolved_at.is_(None)).all()}
        assert esc_keys <= open_keys and "t:1" not in open_keys        # «t:1» не в очереди пульта → устранён при сборке
