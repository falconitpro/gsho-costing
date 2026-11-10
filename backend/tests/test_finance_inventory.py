"""«Финансы», этап 7: инвентаризация отметок оплаты, сделанных до включения раздела (02, раздел 10; D-147)."""
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
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Contract, FinanceAllocation, FinanceTransaction  # noqa: E402
from scripts.seed import run as seed  # noqa: E402
from tests.test_finance_api import H, FD, _signed_client_contract  # noqa: E402


def _claims(contract_id: str) -> list[FinanceTransaction]:
    with SessionLocal() as db:
        return db.scalars(select(FinanceTransaction).where(FinanceTransaction.origin_type == "contract_legacy",
                                                           FinanceTransaction.origin_id.like(f"{contract_id}:pay:%"))).all()


def test_pre_rollout_marks_inventory():
    seed()
    with TestClient(app) as c:
        k = _signed_client_contract(c, 300000, [{"share_pct": 50, "event": "contract", "offset_days": 3, "offset_kind": "working"},
                                                {"share_pct": 50, "event": "contract", "offset_days": 30, "offset_kind": "calendar"}])
        # отметка «оплачено», сделанная до «Финансов»: пишем в договор напрямую, минуя адаптер
        with SessionLocal() as db:
            ct = db.get(Contract, k["id"])
            ct.facts = {**(ct.facts or {}), "pay": {"1": {"paid_on": (date.today() - timedelta(days=1)).isoformat()}}}
            db.commit()
        assert _claims(k["id"]) == []
        dry = c.post("/api/finance/sync?dry_run=true", headers=H(FD)).json()
        assert dry["legacy_marks"]["created"] >= 1 and dry["legacy_marks"]["linked"] >= 1 and _claims(k["id"]) == []   # dry-run: тот же отчёт, без записи
        real = c.post("/api/finance/sync?dry_run=false", headers=H(FD)).json()
        assert real["legacy_marks"]["created"] == dry["legacy_marks"]["created"] and real["legacy_marks"]["linked"] == dry["legacy_marks"]["linked"]
        cl = _claims(k["id"])
        assert len(cl) == 1 and cl[0].status == "needs_reconciliation" and cl[0].account_id is None and cl[0].amount == 150000
        with SessionLocal() as db:
            al = db.scalars(select(FinanceAllocation).where(FinanceAllocation.transaction_id == cl[0].id)).all()
            assert len(al) == 1 and al[0].status == "provisional"                     # однозначная строка договора — временное разнесение
        again = c.post("/api/finance/sync?dry_run=false", headers=H(FD)).json()
        assert again["legacy_marks"]["created"] == 0 and len(_claims(k["id"])) == 1
        # ФД отозвал запись как дубль — повторная инвентаризация её не воскрешает
        assert c.post(f"/api/finance/transactions/{cl[0].id}/withdraw", headers=H(FD), json={"reason": "дубль банковской операции"}).status_code == 200
        c.post("/api/finance/sync?dry_run=false", headers=H(FD))
        cl = _claims(k["id"])
        assert len(cl) == 1 and cl[0].withdrawn_at is not None
