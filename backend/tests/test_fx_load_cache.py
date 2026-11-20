"""Кэш расчёта (аудит 19.11.2026): курсы и история для расчёта читаются быстро, а результат — прежний.
Сверка нового load_fx с прежним алгоритмом (все курсы ORM-объектами) и сброс кэша при новом курсе."""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from sqlalchemy import delete, select  # noqa: E402

from app.calc.fx_forecast import FxHistory  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import FxRate  # noqa: E402
from app.services.deal_calc import load_fx  # noqa: E402
from scripts.seed import run as seed  # noqa: E402


def _old_load_fx(db, on):
    rows = db.scalars(select(FxRate).where(FxRate.on <= on).order_by(FxRate.on.desc(), FxRate.source)).all()   # manual раньше cbr при равной дате
    rates = {"RUB": 1.0}
    source, latest_on = "cbr", None
    for r in rows:
        if r.currency in rates:
            continue
        if r.source == "manual" and r.on == on:
            rates[r.currency] = r.rate
            source = "manual"
        else:
            rates[r.currency] = r.rate
            latest_on = max(latest_on or r.on, r.on)
    if latest_on and latest_on < on and source != "manual":
        source = "cbr_stale"
    hist = {}
    for r in db.scalars(select(FxRate).where(FxRate.on >= on - timedelta(days=365 * 5), FxRate.source == "cbr")).all():
        hist.setdefault(r.currency, []).append((r.on, r.rate))
    return rates, source, {k: sorted(v) for k, v in hist.items()}


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    seed()


def test_same_as_old_algorithm_on_several_dates():
    with SessionLocal() as db:
        for on in (date.today(), date.today() - timedelta(days=40), date.today() - timedelta(days=400)):
            fx, hist = load_fx(db, on)
            rates, source, old_hist = _old_load_fx(db, on)
            assert fx.rates == rates and fx.source == source, on
            assert {k: sorted(v) for k, v in hist.items()} == old_hist


def test_manual_rate_on_calc_date_wins_and_cache_sees_new_rate():
    on = date.today() + timedelta(days=3)   # дата без данных: курс «устаревший», затем ручной
    try:
        _manual_and_cache(on)
    finally:
        with SessionLocal() as db:
            db.execute(delete(FxRate).where(FxRate.comment == "тест"))
            db.execute(delete(FxRate).where(FxRate.rate == 33.3))
            db.commit()


def _manual_and_cache(on):
    with SessionLocal() as db:
        fx, _ = load_fx(db, on)
        assert fx.source in ("cbr_stale", "cbr")
        db.add(FxRate(on=on, currency="CNY", rate=13.37, source="manual", comment="тест"))
        db.commit()
        fx, _ = load_fx(db, on)
        assert fx.rates["CNY"] == 13.37 and fx.source == "manual"
        # новый курс ЦБ меняет историю — кэш не отдаёт старую
        _, h1 = load_fx(db, date.today())
        n1 = len(h1.get("USD", []))
        db.add(FxRate(on=date.today() - timedelta(days=1000), currency="USD", rate=33.3, source="cbr"))
        db.commit()
        _, h2 = load_fx(db, date.today())
        assert len(h2.get("USD", [])) == n1 + 1
        # возвращаемая история — копия: правка не портит кэш
        h2["USD"].clear()
        _, h3 = load_fx(db, date.today())
        assert len(h3["USD"]) == n1 + 1


def test_fx_history_memo_returns_same_values():
    pts = [(date(2020, 1, 1) + timedelta(days=i), 70 + (i % 30) * 0.1) for i in range(1500)]
    h, fresh = FxHistory(pts), FxHistory(pts)
    as_of = date(2024, 1, 15)
    for _ in range(3):
        assert h.seasonality(as_of, 3) == fresh._seasonality(as_of, 3)
        assert h.annual_trend(as_of, 3) == fresh._annual_trend(as_of, 3)
        assert h.horizon_volatility_pct(as_of, 3, 90) == fresh._horizon_volatility_pct(as_of, 3, 90)
    s = h.seasonality(as_of, 3)
    s[1] = (0, 0, 0)                      # копия: правка результата не портит запомненное
    assert h.seasonality(as_of, 3)[1] != (0, 0, 0)
