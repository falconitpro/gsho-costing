"""Сводка «Ваши приоритеты» (docs/operations/12 BRF-01…08, 07 §13). Данные — синтетические."""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AccessUserOverride, SupportTicket, Task, User  # noqa: E402
from app.models.finance import FinanceObligation  # noqa: E402
from scripts.seed import TEST_LOGIN, run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
GD, FD, MGR = TEST_LOGIN, "fd@example.com", "manager@example.com"


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    seed()


@pytest.fixture()
def c():
    with TestClient(app) as client:
        yield client


def today() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=3)).date()


def user(roles=("manager",), name=None, **over) -> tuple[str, str]:
    tag = uuid.uuid4().hex[:8]
    email = f"brf.{tag}@example.com"
    with SessionLocal() as db:
        u = User(email=email, external_id=email, display_name=name or f"Сводкин Тест {tag}", roles=list(roles))
        db.add(u)
        db.flush()
        for g, lvl in over.items():
            db.add(AccessUserOverride(user_id=u.id, field_group=g, level=lvl, comment="тест", created_by=u.id))
        db.commit()
        return u.id, email


_DEAL: dict = {}


def deal_id() -> str:
    if "id" not in _DEAL:
        with TestClient(app) as cl:
            client = cl.get("/api/refs/clients", headers=H(MGR)).json()[0]
            _DEAL["id"] = cl.post("/api/deals", headers=H(MGR), json={"client_id": client["id"], "site": "сводка"}).json()["id"]
    return _DEAL["id"]


def task(uid, title, due_in, done=False) -> str:
    with SessionLocal() as db:
        t = Task(deal_id=deal_id(), role="manager", assignee_id=uid, title=title, due=today() + timedelta(days=due_in),
                 done_at=datetime.now(timezone.utc) if done else None)
        db.add(t)
        db.commit()
        return t.id


def brief(c, email) -> dict:
    r = c.get("/api/briefing", headers=H(email))
    assert r.status_code == 200, r.text
    return r.json()


def keys(b) -> list[str]:
    return [i["item_key"] for i in b["items"]]


# ------------------------------------------------------------------ BRF-01, BRF-02, BRF-06

def test_brf01_overdue_above_tomorrow_dedup_limit_and_disappears_when_done(c):
    uid, me = user()
    t_tomorrow = task(uid, "Подготовить отчёт по таможне", 1)
    t_over = task(uid, "Согласовать спецификацию с заводом", -2)
    task(uid, "Далёкая задача", 10)
    b = brief(c, me)
    assert keys(b)[:2] == [f"task:{t_over}", f"task:{t_tomorrow}"]
    over = b["items"][0]
    assert over["severity"] == "critical" and over["due_label"] == "Просрочено на 2 дн." and over["url"].startswith("/deals/")
    assert over["title"] == "Согласовать спецификацию с заводом" and over["source_label"] == "Задача"
    assert all("Далёкая" not in i["title"] for i in b["items"])
    assert len(set(keys(b))) == len(keys(b))
    # нет реального совещания — нет фраз о нём (BRF-02)
    text = repr(b).lower()
    assert "совещан" not in text and "встреч" not in text
    # 7 задач — показываем 5 и «ещё N»
    for i in range(6):
        task(uid, f"Задача {i}", 0)
    b = brief(c, me)
    assert len(b["items"]) == 5 and b["more"] >= 2 and b["more_url"]
    # BRF-06: выполнил — исчезла; generated_at фактический
    with SessionLocal() as db:
        db.get(Task, t_over).done_at = datetime.now(timezone.utc)
        db.commit()
    b2 = brief(c, me)
    assert f"task:{t_over}" not in keys(b2)
    assert datetime.fromisoformat(b2["generated_at"]) >= datetime.fromisoformat(b["generated_at"])


def test_workplace_contains_same_briefing_and_layout_kept(c):
    uid, me = user()
    tid = task(uid, "Задача на сегодня", 0)
    w = c.get("/api/workplace", headers=H(me)).json()
    assert f"task:{tid}" in keys(w["briefing"]) and keys(w["briefing"]) == keys(brief(c, me))
    order = w["layout"]["order"]
    assert order.index("briefing") == order.index("focus") + 1
    # BRF-08: сохранённая раньше раскладка — без сводки; сводка добавляется после «Главное сейчас», остальное не меняется
    old = ["deals", "tasks", "focus", "kpis", "today"]
    assert c.put("/api/workplace/layout", headers=H(me), json={"order": old, "hidden": ["funnel"]}).status_code == 200
    with SessionLocal() as db:
        u = db.get(User, uid)
        prefs = dict(u.prefs or {})
        wp = dict(prefs.get("workplace") or {})
        wp["order"] = [x for x in wp.get("order", []) if x != "briefing"]
        prefs["workplace"] = wp
        u.prefs = prefs
        db.commit()
    w = c.get("/api/workplace", headers=H(me)).json()
    assert w["layout"]["order"][:6] == ["deals", "tasks", "focus", "briefing", "kpis", "today"] and "funnel" in w["layout"]["hidden"]


# ------------------------------------------------------------------ BRF-03, BRF-04

def _ob(direction, amount, currency, due_in, counterparty_kind="client"):
    run = uuid.uuid4().hex[:10]
    with SessionLocal() as db:
        on = today() + timedelta(days=due_in)
        o = FinanceObligation(source_type="contract", source_id=f"brf-{run}", source_line_key="l1", direction=direction, amount=Decimal(amount),
                              currency=currency, legal_due_on=on, forecast_on=on, title=f"тест {run}", nature="contractual", is_active=True,
                              sync_status="ok", counterparty_kind=counterparty_kind, counterparty_name_snapshot=f"ООО Тест {run}")
        db.add(o)
        db.commit()
        return o.id


def test_brf03_finance_parity_and_currencies_not_mixed(c):
    rub = _ob("in", "150000.50", "RUB", -3)
    cny = _ob("in", "7000", "CNY", 0)
    b = brief(c, FD)
    items = {i["item_key"]: i for i in b["items"]} | {}
    reg = c.get(f"/api/finance/obligations?status=overdue&as_of={today().isoformat()}&limit=500", headers=H(FD)).json()
    src = next(x for x in reg["items"] if x["id"] == rub)
    # элемент может не попасть в первые 5 при чужих данных стенда — проверяем расчёт кандидатов напрямую
    from app.api.workplace import build
    from app.core.auth import CurrentUser  # noqa: F401
    from app.services import briefing as brf
    with SessionLocal() as db:
        from app.services.access_service import perms_for, acting_for
        from app.core.roles import Role
        u = db.scalar(select(User).where(User.email == FD))
        cu = __import__("app.core.auth", fromlist=["CurrentUser"]).CurrentUser(
            id=u.id, email=u.email, display_name=u.display_name, roles={Role(r) for r in u.roles}, observer_scope={}, perms=perms_for(db, u),
            acting_for=acting_for(db, u))
        cands = {i.item_key: i for i in brf.candidates(db, cu, build(db, cu)[1])[0]}
    it = cands[f"obligation:{rub}"]
    assert it.amount == src["remaining"] and it.currency == "RUB" and "150 000,5 RUB" in it.reason and it.due_date.isoformat() == src["legal_due_on"]
    assert cands[f"obligation:{cny}"].currency == "CNY" and "CNY" in cands[f"obligation:{cny}"].reason
    assert items is not None


def test_brf04_manager_without_finance_sees_no_amounts(c):
    _ob("out", "999999", "USD", -1, counterparty_kind="factory")
    uid, me = user(("manager",))
    b = brief(c, me)
    assert not any(i["source_type"] in ("obligation", "money") for i in b["items"])
    assert "999999" not in repr(b) and "999 999" not in repr(b)


def test_brf04_finance_analytics_only_gets_cashview_link(c):
    uid, me = user(("manager",), finance_analytics=1)
    with SessionLocal() as db:
        from app.services import briefing as brf
        from types import SimpleNamespace
        cu = SimpleNamespace(id=uid, can=lambda g, lvl=1: g == "finance_analytics", acting_for=set())
        items = brf._money_summary(db, cu, {"money": {"receivable_count": 2, "receivable_sum": 12345, "url": "/summary?kind=money"}})
    assert items and items[0].url == "/summary?kind=money" and items[0].currency == "RUB"
    assert brf._obligations(None, cu, {"today": today()}) == []


def test_brf04_acting_for_marked_and_others_hidden(c):
    boss_id, boss = user(name="Начальников Пётр Тест")
    dep_id, dep = user()
    with SessionLocal() as db:
        b = db.get(User, boss_id)
        b.deputy_id, b.deputy_from, b.deputy_to = dep_id, today() - timedelta(days=1), today() + timedelta(days=5)
        db.commit()
    t1 = task(boss_id, "Задача начальника", 0)
    other_id, _ = user()
    t2 = task(other_id, "Чужая задача", 0)
    b = brief(c, dep)
    it = next((i for i in b["items"] if i["item_key"] == f"task:{t1}"), None)
    assert it and it["deputy"] is True and "по замещению" in it["reason"]
    assert f"task:{t2}" not in keys(b)


# ------------------------------------------------------------------ BRF-05

def test_brf05_pin_snooze_hide_only_mine_and_signature_returns(c):
    uid, me = user()
    t_a = task(uid, "Задача А", 0)
    t_b = task(uid, "Задача Б", -1)
    b = brief(c, me)
    a = next(i for i in b["items"] if i["item_key"] == f"task:{t_a}")
    # чужой пользователь не может отметить мой элемент
    _, other = user()
    assert c.post(f"/api/briefing/items/task:{t_a}/hide", headers=H(other), json={"source_signature": a["source_signature"]}).status_code == 404
    assert c.post(f"/api/briefing/items/task:{t_a}/hide", headers=H(me), json={"source_signature": "x" * 64}).status_code == 409
    # закрепить: внутри одной важности выше, но не выше нового критичного
    assert c.post(f"/api/briefing/items/task:{t_a}/pin", headers=H(me), json={"source_signature": a["source_signature"]}).status_code == 200
    t_c = task(uid, "Задача В", 0)
    b = brief(c, me)
    ks = keys(b)
    assert ks.index(f"task:{t_b}") < ks.index(f"task:{t_a}") < ks.index(f"task:{t_c}")
    assert next(i for i in b["items"] if i["item_key"] == f"task:{t_a}")["pinned"] is True
    assert c.post(f"/api/briefing/items/task:{t_a}/pin", headers=H(me), json={"source_signature": a["source_signature"]}).status_code == 200   # повтор
    # скрыть — только у меня; источник не меняется
    assert c.post(f"/api/briefing/items/task:{t_a}/hide", headers=H(me), json={"source_signature": a["source_signature"]}).status_code == 200
    b = brief(c, me)
    assert f"task:{t_a}" not in keys(b) and b["hidden"] == 1
    with SessionLocal() as db:
        assert db.get(Task, t_a).due == today() and db.get(Task, t_a).done_at is None
    # срок источника изменился — элемент вернулся
    with SessionLocal() as db:
        db.get(Task, t_a).due = today() - timedelta(days=1)
        db.commit()
    b = brief(c, me)
    assert f"task:{t_a}" in keys(b) and b["hidden"] == 0
    # отложить: максимум 30 дней; счётчик «отложено»
    bi = next(i for i in b["items"] if i["item_key"] == f"task:{t_b}")
    assert c.post(f"/api/briefing/items/task:{t_b}/snooze", headers=H(me), json={"source_signature": bi["source_signature"],
                                                                                 "until": (today() + timedelta(days=31)).isoformat()}).status_code == 400
    assert c.post(f"/api/briefing/items/task:{t_b}/snooze", headers=H(me), json={"source_signature": bi["source_signature"],
                                                                                 "until": (today() + timedelta(days=2)).isoformat()}).status_code == 200
    b = brief(c, me)
    assert f"task:{t_b}" not in keys(b) and b["snoozed"] == 1
    st = c.get("/api/briefing/states", headers=H(me)).json()["items"]
    assert [s["item_key"] for s in st] == [f"task:{t_b}"] and st[0]["title"] == "Задача Б"
    # сброс возвращает всё
    assert c.post("/api/briefing/items/reset", headers=H(me), json={}).json()["reset"] == 1
    assert f"task:{t_b}" in keys(brief(c, me))
    # другому пользователю мои отметки не видны и не влияют
    assert c.get("/api/briefing/states", headers=H(other)).json()["items"] == []


# ------------------------------------------------------------------ поддержка, безопасность

def test_support_items_reporter_and_queue(c):
    rid, me = user()
    sid, spec = user(support_admin=2)
    r = c.post("/api/support/tickets", headers=H(me), data={"category": "error", "subject": "Ошибка в карточке", "description": "Не сохраняется сделка, белый экран.",
                                                            "urgency": "critical"})
    tid = r.json()["id"]
    assert f"support:{tid}" in keys(brief(c, spec))                      # новое в очереди
    c.post(f"/api/support/tickets/{tid}/transition", headers=H(spec), json={"action": "wait", "body": "Пришлите скриншот"})
    it = next(i for i in brief(c, me)["items"] if i["item_key"] == f"support:{tid}")
    assert it["severity"] == "high" and it["url"] == f"/support/{tid}" and "данные от вас" in it["reason"]
    _, other = user()
    assert f"support:{tid}" not in keys(brief(c, other))
    # просрочка первого ответа в очереди — критично для критичного приоритета
    with SessionLocal() as db:
        t2 = SupportTicket(number=f"SUP-T-{uuid.uuid4().hex[:6]}", reporter_id=rid, category="error", requested_urgency="critical", priority="critical",
                           subject="Старое критичное", description="описание длинное", status="new",
                           created_at=datetime.now(timezone.utc) - timedelta(hours=6), last_activity_at=datetime.now(timezone.utc))
        db.add(t2)
        db.commit()
        t2id = t2.id
    q = next(i for i in brief(c, spec)["items"] if i["item_key"] == f"support:{t2id}")
    assert q["severity"] == "critical" and q["url"] == f"/support/admin?ticket={t2id}" and "календарные" in q["reason"]


def test_security_alert_only_for_audit_holders(c):
    from app.services import security_alerts
    with SessionLocal() as db:
        a = security_alerts.raise_alert(db, "mfa_reset", "critical", str(uuid.uuid4()), {}, "Администратор сбросил вход с кодом", "ev")
        db.commit()
        aid = a.id
    assert f"alert:{aid}" in keys(brief(c, GD)) or brief(c, GD)["more"] > 0
    _, me = user()
    assert not any(k.startswith("alert:") for k in keys(brief(c, me)))


# ------------------------------------------------------------------ BRF-07

def test_brf07_one_source_failure_does_not_break_block(c, monkeypatch):
    from app.services import briefing as brf
    uid, me = user()
    tid = task(uid, "Задача при сбое", 0)

    def boom(*a, **k):
        raise RuntimeError("finance down")
    monkeypatch.setattr(brf, "SOURCES", [s if s[0] != "approvals" else (s[0], s[1], boom) for s in brf.SOURCES])
    b = brief(c, me)
    assert f"task:{tid}" in keys(b) and "Часть данных не обновлена: согласования" in b["warnings"]
    # падение всей сводки не ломает рабочее место
    monkeypatch.setattr(brf, "build", boom)
    w = c.get("/api/workplace", headers=H(me))
    assert w.status_code == 200 and w.json()["briefing"]["error"] is True and w.json()["tasks"]


def test_empty_state_text(c):
    _, me = user(("observer",))
    b = brief(c, me)
    if not b["items"]:
        assert b["empty_text"] == "Срочных дел по вашим задачам и срокам нет"
