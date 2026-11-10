"""D-130: «Сводка → Деньги» и рабочее место считаются общим сервисом «Финансов» — одинаковые суммы во всех трёх представлениях
(06, этап 4, gate); КД сохраняет сводку «Деньги», хотя раздела «Финансы» у него нет."""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta
from decimal import Decimal

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

H = lambda email: {"X-Dev-User": email}   # noqa: E731
CD, GD, FD, MGR = "cd@example.com", "evgeniy@dobrov.pro", "fd@example.com", "manager@example.com"


def test_summary_money_and_workplace_use_finance_service():
    seed()
    with TestClient(app) as c:
        today = date.today()
        horizon = (today + timedelta(days=90)).isoformat()
        sm = c.get("/api/summary/money?days=90", headers=H(FD))
        assert sm.status_code == 200, sm.text
        sm = sm.json()
        # прежние ключи на месте
        assert {"rows", "receivables", "guarantees", "curve", "parts", "totals"} <= set(sm)
        assert {"in_rub", "out_rub", "receivable_count", "min_cum"} <= set(sm["totals"]) and "balance_status" in sm["totals"]
        # суммы = реестр «Финансов» с теми же фильтрами (открытые, RUB, срок или ожидаемая дата ≤ горизонта)
        fin_in = c.get(f"/api/finance/obligations?status=open&direction=in&currency=RUB&date_to={horizon}&limit=500", headers=H(FD)).json()
        fin_out = c.get(f"/api/finance/obligations?status=open&direction=out&currency=RUB&date_to={horizon}&limit=500", headers=H(FD)).json()
        assert Decimal(str(sm["totals"]["in_rub"])) == Decimal(fin_in["totals"].get("RUB", {}).get("in_remaining", "0.00"))
        assert Decimal(str(sm["totals"]["out_rub"])) == Decimal(fin_out["totals"].get("RUB", {}).get("out_remaining", "0.00"))
        # кривая = прогноз ликвидности RUB по неделям; неизвестный остаток → null, а не ноль
        liq = c.get("/api/finance/liquidity?group_by=week&horizon_days=90", headers=H(FD)).json()
        rub = next((x for x in liq["currencies"] if x["currency"] == "RUB"), None)
        if rub is not None:
            by_week = {p["on"]: p["balance"] for p in rub["points"]}
            for p in sm["curve"]:
                exp = by_week[p["week"]]
                assert (p["cum"] is None) if exp is None else Decimal(str(p["cum"])) == Decimal(exp)
            assert sm["totals"]["balance_status"] == rub["balance_status"]
            if rub["balance_status"] == "unknown":
                assert sm["totals"]["min_cum"] is None
        # рабочее место — тот же сервис: 8 недель, просроченная дебиторка и минимум
        wp = c.get("/api/workplace", headers=H(FD)).json()["money"]
        assert len(wp["curve"]) == 8 and wp["balance_status"] == sm["totals"]["balance_status"] and wp["url"] == "/finance?tab=liquidity"
        rec56 = [r for r in sm["rows"] if r["direction"] == "in" and r["overdue_days"] > 0 and r["currency"] == "RUB"]
        assert wp["receivable_count"] >= 0 and wp["receivable_sum"] == round(sum(r["amount"] for r in rec56))
        # КД: сводка «Деньги» осталась (finance_analytics), раздела «Финансы» нет; ссылка рабочего места — на сводку
        assert c.get("/api/summary/money", headers=H(CD)).status_code == 200
        assert c.get("/api/finance/overview", headers=H(CD)).status_code == 403
        wcd = c.get("/api/workplace", headers=H(CD)).json()
        if wcd.get("money"):
            assert wcd["money"]["url"] == "/summary?kind=money"
        assert c.get("/api/summary/money", headers=H(MGR)).status_code == 403
