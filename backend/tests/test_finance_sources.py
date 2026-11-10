"""«Финансы», этап 2: источники помимо договора — заказ поставщику, логистическая партия, запчасти без даты (D-121…D-124)."""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import date, datetime, timezone

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.workflow import DealStatus  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Deal, Factory, FinanceTransaction, LegalEntity, PurchaseOrder  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email, key=None: {"X-Dev-User": email, **({"Idempotency-Key": key} if key else {})}   # noqa: E731
MGR, CD, GD, FD, LOG = "manager@example.com", "cd@example.com", "evgeniy@dobrov.pro", "fd@example.com", "logist@example.com"
RUN = uuid.uuid4().hex[:8]


def _po(currency="CNY") -> str:
    with SessionLocal() as db:
        sup = db.scalars(select(Factory)).first()
        ent = db.scalar(select(LegalEntity).where(LegalEntity.code == "cn")) or db.scalars(select(LegalEntity)).first()
        po = PurchaseOrder(number=f"ЗК-Ф-{RUN}-{currency}", kind="external", supplier_id=sup.id, buyer_entity_id=ent.id, currency=currency, status="sent")
        db.add(po)
        db.commit()
        return po.id


def _rows(c, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    return c.get(f"/api/finance/obligations?limit=500&include_inactive=true&{qs}", headers=H(FD)).json()["items"]


def test_purchase_order_payments_become_obligations():
    seed()
    pid = _po()
    with TestClient(app) as c:
        # до подтверждения поставщиком строк нет; подтверждение с графиком — каждая строка отдельно, срок — due
        assert not [o for o in _rows(c, source_type="purchase_order") if o["source"]["id"] == pid]
        r = c.post(f"/api/parts/po/{pid}/confirm", headers=H(GD), json={"supplier_ref": "PI-1", "payments": [
            {"due": "2026-11-20", "amount": 30000, "comment": "аванс 30 %"}, {"due": "2026-12-20", "amount": 70000, "comment": "до отгрузки"}]})
        assert r.status_code == 200, r.text
        rows = [o for o in _rows(c, source_type="purchase_order") if o["source"]["id"] == pid]
        assert [(o["amount"], o["currency"], o["legal_due_on"], o["direction"]) for o in rows] == [
            ("30000.00", "CNY", "2026-11-20", "out"), ("70000.00", "CNY", "2026-12-20", "out")]
        assert rows[0]["counterparty"]["kind"] == "factory" and rows[0]["source"]["number"].startswith("ЗК-Ф-") and rows[0]["source"]["url"] == "/supply?tab=po"
        assert "аванс" in rows[0]["title"]
        # повторное подтверждение без изменений — без дублей; изменение второй строки — обновление только её
        c.post(f"/api/parts/po/{pid}/confirm", headers=H(GD), json={"payments": [
            {"due": "2026-11-20", "amount": 30000, "comment": "аванс 30 %"}, {"due": "2026-12-25", "amount": 70000, "comment": "до отгрузки"}]})
        rows = [o for o in _rows(c, source_type="purchase_order") if o["source"]["id"] == pid]
        assert len(rows) == 2 and rows[1]["legal_due_on"] == "2026-12-25"
        # «оплачено» в заказе — старая отметка: запись «требует сверки» с временным разнесением на эту строку, не факт
        c.post(f"/api/parts/po/{pid}/confirm", headers=H(GD), json={"payments": [
            {"due": "2026-11-20", "amount": 30000, "comment": "аванс 30 %", "paid_on": "2026-11-19"}, {"due": "2026-12-25", "amount": 70000, "comment": "до отгрузки"}]})
        row = c.get(f"/api/finance/obligations/{rows[0]['id']}", headers=H(FD)).json()
        assert row["confirmed_paid"] == "0.00" and row["provisional_paid"] == "30000.00" and "needs_reconciliation" in row["flags"]
        with SessionLocal() as db:
            claims = list(db.scalars(select(FinanceTransaction).where(FinanceTransaction.origin_type == "purchase_order_legacy", FinanceTransaction.origin_id.like(f"{pid}:%"))).all())
        assert len(claims) == 1 and claims[0].status == "needs_reconciliation" and claims[0].account_id is None
        # строку графика убрали — обязательство не удалено, а выведено из итогов; заказ отменён — строки исключены
        c.post(f"/api/parts/po/{pid}/confirm", headers=H(GD), json={"payments": [{"due": "2026-11-20", "amount": 30000, "comment": "аванс 30 %", "paid_on": "2026-11-19"}]})
        rows = {o["source"]["line_key"]: o for o in _rows(c, source_type="purchase_order") if o["source"]["id"] == pid}
        assert rows["payment:2"]["sync_status"] == "orphaned" and rows["payment:2"]["is_active"] is False and rows["payment:1"]["is_active"] is True
        assert c.post(f"/api/parts/po/{pid}/cancel", headers=H(GD)).status_code == 200
        rows = {o["source"]["line_key"]: o for o in _rows(c, source_type="purchase_order") if o["source"]["id"] == pid}
        assert rows["payment:1"]["is_active"] is False and rows["payment:1"]["excluded_reason"] == "заказ отменён"
        # повторная полная сборка ничего не меняет
        assert c.post("/api/finance/sync?dry_run=false", headers=H(FD)).status_code == 200
        dry = c.post("/api/finance/sync?dry_run=true", headers=H(FD)).json()
        assert dry["created"] == 0 and dry["updated"] == 0, dry


def test_batch_payments_need_explicit_currency_and_parts_undated_block():
    seed()
    with TestClient(app) as c:
        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        d = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": f"партия-{RUN}"}).json()
        with SessionLocal() as db:
            db.get(Deal, d["id"]).status = DealStatus.LOGISTICS_CALC
            db.commit()
        r = c.post(f"/api/deals/{d['id']}/logistics/batches", headers=H(GD), json={"name": "Партия 1", "payments": [
            {"due": "2026-12-01", "amount": 450000, "currency": "rub", "payee": "ООО «Брокер»", "comment": "брокер и СВХ"},
            {"due": "2026-12-05", "amount": 12000, "payee": "ТК «Восток»", "comment": "фрахт"}]})
        assert r.status_code == 201, r.text
        rows = {o["source"]["line_key"]: o for o in _rows(c, source_type="batch", deal_id=d["id"])}
        assert rows["payment:1"]["amount"] == "450000.00" and rows["payment:1"]["currency"] == "RUB" and rows["payment:1"]["is_active"] is True
        assert rows["payment:1"]["source"]["url"] == f"/deals/{d['id']}?tab=logistics" and rows["payment:1"]["deal"]["id"] == d["id"]
        # валюта в строке не указана — не подставляем RUB: конфликт «валюта не определена», вне итогов
        assert rows["payment:2"]["currency"] == "XXX" and rows["payment:2"]["sync_status"] == "conflict" and rows["payment:2"]["is_active"] is False
        assert "валюта" in rows["payment:2"]["excluded_reason"]
        bid = r.json()["id"]
        c.patch(f"/api/deals/{d['id']}/logistics/batches/{bid}", headers=H(GD), json={"name": "Партия 1", "payments": [
            {"due": "2026-12-01", "amount": 450000, "currency": "RUB", "payee": "ООО «Брокер»", "comment": "брокер и СВХ"},
            {"due": "2026-12-05", "amount": 12000, "currency": "CNY", "payee": "ТК «Восток»", "comment": "фрахт"}]})
        rows = {o["source"]["line_key"]: o for o in _rows(c, source_type="batch", deal_id=d["id"])}
        assert rows["payment:2"]["currency"] == "CNY" and rows["payment:2"]["sync_status"] == "ok" and rows["payment:2"]["is_active"] is True
        # запчасти без согласованной даты — отдельный блок; в датированный календарь не попадают
        und = c.get("/api/finance/undated", headers=H(FD))
        assert und.status_code == 200 and und.json()["parts"]["currency"] == "RUB" and "не входят" in und.json()["parts"]["note"]
        assert c.get("/api/finance/undated", headers=H(CD)).status_code == 403
        assert not [o for o in _rows(c) if o["source"]["type"] == "part_request"]
