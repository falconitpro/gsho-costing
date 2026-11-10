"""«Финансы», этап 6: дебиторка/кредиторка, «Объяснить цифру» экономики, экспорт XLSX (FIN-SEC-009, FIN-SEC-012; D-143…D-145)."""
from __future__ import annotations

import io
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
from openpyxl import load_workbook  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AuditLog  # noqa: E402
from scripts.seed import run as seed  # noqa: E402
from tests.test_finance_api import H, CD, FD, LAW, MGR, PDF, RUN  # noqa: E402


def _contract(c, cp_name: str, signed_on: date, amount: float):
    ent = c.get("/api/legal/contracts", headers=H(LAW)).json()["entities"][0]
    k = c.post("/api/legal/contracts", headers=H(LAW), json={"kind": "client_parts", "own_entity_id": ent["id"], "cp_name": cp_name, "cp_inn": "7701000002",
                                                             "subject": "запчасти", "amount": amount, "currency": "RUB"}).json()
    c.put(f"/api/legal/contracts/{k['id']}", headers=H(LAW), json={"payment_terms": [{"share_pct": 50, "event": "contract", "offset_days": 5, "offset_kind": "calendar"},
                                                                                   {"share_pct": 50, "event": "contract", "offset_days": 120, "offset_kind": "calendar"}],
                                                                  "signatory": {"name": "П. П.", "basis": "Устава"}, "law": {"claim_days": 30}, "signed_on": signed_on.isoformat()})
    c.post(f"/api/legal/contracts/{k['id']}/files", headers=H(LAW), files={"file": PDF}, data={"kind": "signed_scan"})
    r = c.post(f"/api/legal/contracts/{k['id']}/status", headers=H(LAW), json={"status": "active"})
    assert r.status_code == 200, r.text
    return r.json()


def _rows(content: bytes, sheet: str) -> list[list]:
    ws = load_workbook(io.BytesIO(content))[sheet]
    return [[c.value for c in row] for row in ws.iter_rows()]


def test_debt_explanation_export():
    seed()
    today = date.today()
    with TestClient(app) as c:
        evil = f"=HYPERLINK(\"http://x\",\"{RUN}\")"
        k = _contract(c, evil, today - timedelta(days=40), 1_000_000)   # первая строка просрочена на 35 дней, вторая — через 80
        d = c.get("/api/finance/debt?direction=in", headers=H(FD)).json()
        assert c.get("/api/finance/debt", headers=H(CD)).status_code == 403
        g = next(x for x in d["groups"] if x["title"] == evil)
        assert sorted(r["bucket"] for r in g["rows"]) == ["current", "d31_60"]
        assert g["max_days_overdue"] == 35 and g["by_currency"]["RUB"]["remaining"] == "1000000.00" and g["by_currency"]["RUB"]["overdue"] == "500000.00"
        rub = d["totals"]["in"]["RUB"]
        assert float(rub["d31_60"]) >= 500000 and [b["code"] for b in d["buckets"]] == ["current", "d1_7", "d8_30", "d31_60", "d61_90", "d90_plus"]

        # экспорт: те же строки, что на экране; формула — текстом; журнал
        assert c.get("/api/finance/export?kind=debt", headers=H(MGR)).status_code == 403
        assert c.get("/api/finance/export?kind=nope", headers=H(FD)).status_code == 422
        r = c.get("/api/finance/export?kind=debt&direction=in", headers=H(FD))
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
        rows = _rows(r.content, "Задолженность")
        screen = sum(x["count"] for x in d["groups"])
        assert len(rows) - 1 == screen
        mine = [x for x in rows[1:] if x[0] == "'" + evil]
        assert len(mine) == 2, rows[:3]                                                  # «=HYPERLINK…» не стала формулой
        assert all(not (isinstance(v, str) and v.startswith("=")) for row in rows for v in row)
        notes = dict((a, b) for a, b in _rows(r.content, "Пояснения"))
        assert notes["Отчёт"] == "Дебиторка и кредиторка" and "Пользователь" in notes
        with SessionLocal() as db:
            a = db.scalars(select(AuditLog).where(AuditLog.entity == "finance_export").order_by(AuditLog.at.desc())).first()
            assert a.after["kind"] == "debt" and a.after["rows"] == screen and "groups" not in a.after
        cal = c.get(f"/api/finance/export?kind=obligations&contract_id={k['id']}", headers=H(FD))
        crow = _rows(cal.content, "Календарь")
        assert len(crow) == 3 and crow[0][0] == "Срок по договору" and crow[1][8] == 500000.0
        assert c.get("/api/finance/export?kind=liquidity", headers=H(FD)).status_code == 200
        rec = c.get("/api/finance/export?kind=reconciliation", headers=H(FD))
        assert len(_rows(rec.content, "Сверка")) - 1 == c.get("/api/finance/reconciliation", headers=H(FD)).json()["total"]

        # экономика: объяснение детерминировано и сходится с экраном; экспорт — только с правами на экономику и раздел
        from tests.test_finance_economy import _deal
        did, var_id, pos_id = _deal(c)
        c.post(f"/api/deals/{did}/finance/recognition-events", headers=H(FD, f"ex-ev1-{RUN}"), json={"kind": "revenue", "amount": "4000000.00", "basis_type": "primary_document",
                                                                                                 "document_type": "УПД", "document_number": f"У-{RUN}", "document_date": today.isoformat()})
        ev2 = c.post(f"/api/deals/{did}/finance/recognition-events", headers=H(FD, f"ex-ev2-{RUN}"), json={"kind": "revenue", "amount": "1.00", "basis_type": "business_event",
                                                                                                       "comment": "ошибочно"}).json()
        c.post(f"/api/finance/recognition-events/{ev2['id']}/reverse", headers=H(FD), json={"reason": "дубль"})
        url = f"/api/finance/deals/{did}/economy/explanation?metric=revenue_fact"
        x1, x2 = c.get(url, headers=H(FD)).json(), c.get(url, headers=H(FD)).json()
        assert x1 == x2                                                                  # повторяемость
        assert x1["value"] == "4000000.00" and len(x1["included"]) == 1 and x1["excluded"][0]["reason"] == "сторно: дубль"
        eco = c.get(f"/api/finance/deals/{did}/economy", headers=H(FD)).json()
        assert float(x1["value"]) == eco["pnl_fact"]["revenue"]
        pl = c.get(f"/api/finance/deals/{did}/economy/explanation?metric=plan&scenario=pessimistic&cost_item_code=factory_price", headers=H(FD)).json()
        assert pl["value"] == 6_600_000 and pl["included"][0]["type"] == "deal_version"
        fi = c.get(f"/api/finance/deals/{did}/economy/explanation?metric=forecast_item&cost_item_code=intl_freight", headers=H(FD)).json()
        assert fi["value"] == next(i for i in eco["items"] if i["code"] == "intl_freight")["forecast"]
        gf = c.get(f"/api/finance/deals/{did}/economy/explanation?metric=cash_gap_forecast", headers=H(FD)).json()
        assert gf["value"] == eco["cash_gap"]["forecast"]["amount_rub"] and min(float(p["balance"]) for p in gf["series"]) == float(gf["value"])
        fr = c.get(f"/api/finance/deals/{did}/economy/explanation?metric=forecast_revenue", headers=H(FD)).json()
        assert fr["value"] == eco["forecast"]["revenue"] and len(fr["included"]) == 2
        assert c.get(f"/api/finance/deals/{did}/economy/explanation?metric=formula", headers=H(FD)).status_code == 422   # произвольную формулу не принимает
        assert c.get(f"/api/finance/deals/{did}/economy/explanation?metric=revenue_fact", headers=H(MGR)).status_code == 403
        e = c.get(f"/api/finance/export?kind=economy&deal_id={did}", headers=H(FD))
        items = _rows(e.content, "Статьи")
        assert len(items) == 19 and items[1][1] == "Цена завода (с опциями)" and items[1][2] == 6_000_000
        assert c.get(f"/api/finance/export?kind=economy&deal_id={did}", headers=H(CD)).status_code == 403   # раздел КД закрыт — файл тоже
        assert c.get(f"/api/finance/export?kind=economy&deal_id={did}", headers=H(MGR)).status_code == 403
        assert c.get("/api/finance/export?kind=economy", headers=H(FD)).status_code == 422
