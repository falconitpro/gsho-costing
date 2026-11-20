"""D-100: причина возврата на доработку из справочника, комментарий, отчёт «Возвраты на доработку», правка списка."""
from __future__ import annotations

import os
import tempfile

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.services import rework_reasons as rr  # noqa: E402
from scripts.seed import DEV_PASSWORD, run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, FD, LOG = "manager@example.com", "cd@example.com", "fd@example.com", "logist@example.com"


@pytest.fixture(scope="module")
def c():
    seed()
    with TestClient(app) as cl:
        yield cl


def _deal_at_cd(c) -> str:
    cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
    did = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "возвраты"}).json()["id"]
    with SessionLocal() as db:
        db.execute(text("update deals set status = 'CD_APPROVAL' where id = :d"), {"d": did})
        db.commit()
    return did


def test_catalog_for_cd_fd_gd_only(c):
    r = c.get("/api/rework-reasons", headers=H(CD))
    assert r.status_code == 200 and any(x["code"] == "other" for x in r.json()["items"]) and any(x["code"] == "freight" for x in r.json()["items"])
    assert c.get("/api/rework-reasons", headers=H(FD)).status_code == 200
    assert c.get("/api/rework-reasons", headers=H(MGR)).status_code == 403


def test_rework_with_code_and_comment_goes_to_task_and_report(c):
    did = _deal_at_cd(c)
    r = c.post(f"/api/deals/{did}/rework", headers=H(CD), json={"block": "logistics", "reason_code": "freight", "comment": "ставка от августа"})
    assert r.status_code == 200 and r.json()["status"] == "rework"
    tasks = [t for t in c.get("/api/tasks/my", headers=H(LOG)).json() if t["deal_id"] == did]
    assert tasks and "нет ставки перевозчика" in tasks[0]["title"] and "ставка от августа" in tasks[0]["title"]
    rep = c.get("/api/reports/rework", headers=H(CD)).json()
    assert any(x["code"] == "freight" and x["count"] >= 1 for x in rep["by_reason"])
    mine = [x for x in rep["rows"] if x["deal_id"] == did]
    assert len(mine) == 1, mine                       # один возврат — одна строка (без дубля «без кода»)
    row = mine[0]
    assert row["block"] == "логист" and row["comment"] == "ставка от августа" and row["by"]
    assert c.get("/api/reports/rework", headers=H(MGR)).status_code == 403


def test_other_requires_comment_and_unknown_code_rejected(c):
    did = _deal_at_cd(c)
    assert c.post(f"/api/deals/{did}/rework", headers=H(CD), json={"block": "manager", "reason_code": "other", "comment": ""}).status_code == 422
    assert c.post(f"/api/deals/{did}/rework", headers=H(CD), json={"block": "manager", "reason_code": "nope"}).status_code == 422
    assert c.post(f"/api/deals/{did}/rework", headers=H(CD), json={"block": "manager", "reason_code": "other", "comment": "клиент сменил объект"}).status_code == 200


def test_legacy_free_text_still_works_and_shown_without_code(c):
    did = _deal_at_cd(c)
    assert c.post(f"/api/deals/{did}/rework", headers=H(FD), json={"block": "gd", "reason": "старый способ"}).status_code == 200
    rep = c.get("/api/reports/rework", headers=H(FD)).json()
    row = next(x for x in rep["rows"] if x["deal_id"] == did)
    assert row["comment"] == "старый способ" and "без кода" in row["title"]
    # повторные возвраты по одной сделке попадают в «по сделкам»
    assert c.post(f"/api/deals/{did}/rework", headers=H(CD), json={"block": "pricing", "reason_code": "price_margin"}).status_code == 200
    rep = c.get("/api/reports/rework", headers=H(CD)).json()
    assert any(x["deal_id"] == did and x["count"] == 2 for x in rep["by_deal"])
    assert rep["total"] == len([1 for _ in rep["rows"]]) or rep["total"] >= len(rep["rows"])


def test_settings_edit_by_cd_normalized(c):
    stepped = TestClient(app)
    assert stepped.post("/api/auth/step-up", headers=H(CD), json={"password": DEV_PASSWORD}).status_code == 200
    rows = [{"code": "freight", "title": "Фрахт", "block": "logistics"}, {"code": "", "title": "Новая причина", "block": ""}]
    r = stepped.put("/api/admin/settings/rework_reasons", headers=H(CD), json={"value": rows, "comment": "тест"})
    assert r.status_code == 200, r.text
    items = c.get("/api/rework-reasons", headers=H(CD)).json()["items"]
    codes = [x["code"] for x in items]
    assert codes[0] == "freight" and "other" in codes and len(codes) == 3       # «Другое» добавляется само
    assert stepped.put("/api/admin/settings/rework_reasons", headers=H(CD), json={"value": [{"code": "a b", "title": "x"}]}).status_code == 422
    assert stepped.put("/api/admin/settings/rework_reasons", headers=H(CD),
                       json={"value": [{"code": "x", "title": "x"}, {"code": "x", "title": "y"}]}).status_code == 422
    with SessionLocal() as db:   # вернуть список по умолчанию для других тестов
        db.execute(text("delete from rate_settings where key = 'rework_reasons'"))
        db.commit()
    assert len(rr.normalize([{"title": "Тест причина"}])) == 2


def test_no_duplicate_when_deal_has_approved_version(c):
    """У утверждённой ранее сделки смена статуса сбрасывается в базу раньше (синхронизация с «Финансами») — дубля «без кода» быть не должно."""
    did = _deal_at_cd(c)
    assert c.post(f"/api/deals/{did}/rework", headers=H(CD), json={"block": "pricing", "reason_code": "fx"}).status_code == 200
    with SessionLocal() as db:
        db.execute(text("update deals set approved_version_id = (select id from deal_versions where deal_id = :d order by version_no desc limit 1), "
                        "status = 'CD_APPROVAL' where id = :d"), {"d": did})
        db.commit()
    r = c.post(f"/api/deals/{did}/rework", headers=H(CD), json={"block": "logistics", "reason_code": "customs", "comment": "код ТН ВЭД"})
    assert r.status_code == 200, r.text
    mine = [x for x in c.get("/api/reports/rework", headers=H(CD)).json()["rows"] if x["deal_id"] == did]
    assert len(mine) == 2 and all("без кода" not in x["title"] for x in mine), mine
