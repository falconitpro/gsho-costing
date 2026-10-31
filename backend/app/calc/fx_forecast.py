"""Прогнозный курс на дату платежа (раздел 8 ТЗ).

K_прогноз(T) = w_A·K_ЦБ·(1 + r_A·T/365) + w_B·K_ЦБ·(1 + r_B·T/365)·s_m(T) + w_G·K_ФД(T)

  A — форвардный компонент: r_A = ключевая ставка ЦБ − ставка иностранного ЦБ (НБК для CNY)
  Б — исторический тренд: среднегодовое изменение курса за N лет по данным ЦБ
  В — сезонность: средний коэффициент отклонения курса месяца от годового среднего за те же N лет
  Г — экспертный прогноз ФД (таблица дата → курс, интерполяция)

Если компонент Г не задан, его вес распределяется на A и Б×В пропорционально.
Волатильность ΔK — стандартное отклонение изменения курса за горизонт T по тем же N годам.
"""
from __future__ import annotations

import math
from bisect import bisect_left
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional, Sequence

from .types import FxForecastSettings, FxRates, ScenarioName, ScenarioParams


class FxHistory:
    """Дневная история курса одной валюты (RUB за единицу), отсортирована по дате."""

    def __init__(self, points: Sequence[tuple[date, float]]):
        pts = sorted((d, r) for d, r in points if r and r > 0)
        self.dates = [d for d, _ in pts]
        self.rates = [r for _, r in pts]

    def __len__(self) -> int:
        return len(self.dates)

    def rate_on(self, on: date) -> Optional[float]:
        """Курс на дату или последний известный до неё."""
        if not self.dates:
            return None
        i = bisect_left(self.dates, on)
        if i < len(self.dates) and self.dates[i] == on:
            return self.rates[i]
        if i == 0:
            return None
        return self.rates[i - 1]

    def window(self, start: date, end: date) -> list[tuple[date, float]]:
        i = bisect_left(self.dates, start)
        j = bisect_left(self.dates, end + timedelta(days=1))
        return list(zip(self.dates[i:j], self.rates[i:j]))

    # --- компонент Б: годовой тренд -------------------------------------------------
    def annual_trend(self, as_of: date, years: int) -> Optional[float]:
        start = date(as_of.year - years, as_of.month, min(as_of.day, 28))
        r0 = self.rate_on(start)
        r1 = self.rate_on(as_of)
        if not r0 or not r1:
            return None
        # фактическая глубина истории, если данных меньше N лет
        first = self.dates[0]
        if first > start:
            r0 = self.rates[0]
            span_years = max((as_of - first).days / 365.25, 0.5)
        else:
            span_years = years
        return (r1 / r0) ** (1 / span_years) - 1

    # --- компонент В: сезонность -----------------------------------------------------
    def seasonality(self, as_of: date, years: int) -> dict[int, tuple[float, float, float]]:
        """Для каждого месяца: (средний коэффициент, min, max) по годам.

        Коэффициент года y для месяца m = среднее курса в m / среднее курса за скользящий год.
        """
        start = date(as_of.year - years, 1, 1)
        pts = self.window(start, as_of)
        by_month: dict[tuple[int, int], list[float]] = defaultdict(list)
        for d, r in pts:
            by_month[(d.year, d.month)].append(r)
        monthly_avg = {k: sum(v) / len(v) for k, v in by_month.items()}
        years_in_data = sorted({y for y, _ in monthly_avg})
        coeffs: dict[int, list[float]] = defaultdict(list)
        for y in years_in_data:
            months = [monthly_avg[(y, m)] for m in range(1, 13) if (y, m) in monthly_avg]
            if len(months) < 6:
                continue
            year_avg = sum(months) / len(months)
            for m in range(1, 13):
                if (y, m) in monthly_avg:
                    coeffs[m].append(monthly_avg[(y, m)] / year_avg)
        out: dict[int, tuple[float, float, float]] = {}
        for m in range(1, 13):
            vals = coeffs.get(m) or [1.0]
            out[m] = (sum(vals) / len(vals), min(vals), max(vals))
        return out

    # --- волатильность на горизонте T -------------------------------------------------
    def horizon_volatility_pct(self, as_of: date, years: int, t_days: int) -> Optional[float]:
        """Стандартное отклонение относительного изменения курса за T дней, в %."""
        t_days = max(t_days, 1)
        start = date(as_of.year - years, as_of.month, min(as_of.day, 28))
        pts = self.window(start, as_of)
        if len(pts) < 60:
            return None
        changes: list[float] = []
        step = max(1, t_days // 10)   # перекрывающиеся окна с шагом
        for i in range(0, len(pts), step):
            d0, r0 = pts[i]
            target = d0 + timedelta(days=t_days)
            if target > as_of:
                break
            r1 = self.rate_on(target)
            if r1:
                changes.append(math.log(r1 / r0))
        if len(changes) < 5:
            return None
        mean = sum(changes) / len(changes)
        var = sum((c - mean) ** 2 for c in changes) / (len(changes) - 1)
        return math.sqrt(var) * 100.0


def _expert_rate(points, on: date) -> Optional[float]:
    if not points:
        return None
    pts = sorted(points, key=lambda p: p.on)
    if on <= pts[0].on:
        return pts[0].rate
    if on >= pts[-1].on:
        return pts[-1].rate
    for a, b in zip(pts, pts[1:]):
        if a.on <= on <= b.on:
            span = (b.on - a.on).days or 1
            w = (on - a.on).days / span
            return a.rate + (b.rate - a.rate) * w
    return pts[-1].rate


class ForecastResult:
    __slots__ = (
        "k_cbr", "a", "b", "c", "c_spread", "g", "weights", "forecast",
        "dk_pct", "rate_by_scenario", "t_days", "fixed",
    )

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def forecast_rate(
    currency: str,
    on: date,
    fx: FxRates,
    settings: FxForecastSettings,
    history: Optional[FxHistory],
    scenarios: list[ScenarioParams],
    fixed_rate: Optional[float] = None,
) -> ForecastResult:
    """Прогноз курса валюты на дату платежа по сценариям."""
    k_cbr = fx.rate(currency)
    t = max((on - fx.as_of).days, 0)
    if currency == "RUB":
        rs = {s.name.value: 1.0 for s in scenarios}
        return ForecastResult(k_cbr=1.0, a=1.0, b=1.0, c=1.0, c_spread=(1.0, 1.0), g=None,
                              weights=(1, 0, 0), forecast=1.0, dk_pct={k: 0.0 for k in rs},
                              rate_by_scenario=rs, t_days=t, fixed=False)

    # A — форвард
    foreign = settings.foreign_rate_pct.get(currency, settings.pboc_rate_pct)
    r_a = (settings.key_rate_pct - foreign) / 100.0
    comp_a = k_cbr * (1 + r_a * t / 365)

    # Б — тренд, В — сезонность
    r_b = None
    season = (1.0, 1.0, 1.0)
    if history and len(history) > 0:
        r_b = history.annual_trend(fx.as_of, settings.history_years)
        season = history.seasonality(fx.as_of, settings.history_years).get(on.month, season)
    if r_b is None:
        r_b = r_a   # без истории тренд приравниваем к форварду
    comp_b = k_cbr * (1 + r_b * t / 365)
    # сезонность «включается» плавно на первых 30 днях горизонта, чтобы при T=0 прогноз = K_ЦБ
    comp_c = 1.0 + (season[0] - 1.0) * min(t, 30) / 30.0

    # Г — эксперт
    comp_g = _expert_rate(settings.expert_points.get(currency), on)

    w_a, w_b, w_g = settings.weight_forward, settings.weight_trend, settings.weight_expert
    if comp_g is None:
        total = w_a + w_b
        w_a, w_b, w_g = w_a / total, w_b / total, 0.0
        comp_g_val = 0.0
    else:
        comp_g_val = comp_g
    forecast = w_a * comp_a + w_b * comp_b * comp_c + w_g * comp_g_val

    # ΔK по сценариям
    vol = None
    if settings.use_historical_volatility and history:
        vol = history.horizon_volatility_pct(fx.as_of, settings.history_years, t)
    dk: dict[str, float] = {}
    rates: dict[str, float] = {}
    for s in scenarios:
        if s.name == ScenarioName.BASE:
            d = 0.0
        elif settings.use_historical_volatility and vol is not None:
            mult = settings.vol_multiplier_pessimistic if s.name == ScenarioName.PESSIMISTIC else settings.vol_multiplier_optimistic
            d = vol * mult * (1 if s.name == ScenarioName.PESSIMISTIC else -1)
        else:
            d = s.dk_pct or 0.0
        dk[s.name.value] = round(d, 4)
        if fixed_rate:
            rates[s.name.value] = fixed_rate
        else:
            rates[s.name.value] = forecast * (1 + d / 100.0)

    return ForecastResult(
        k_cbr=k_cbr, a=comp_a, b=comp_b, c=comp_c, c_spread=(season[1], season[2]), g=comp_g,
        weights=(w_a, w_b, w_g), forecast=fixed_rate or forecast, dk_pct=dk,
        rate_by_scenario=rates, t_days=t, fixed=bool(fixed_rate),
    )
