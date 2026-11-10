"""«Финансы», этап 4: прогноз ликвидности от подтверждённого остатка и импорт выписки CSV/XLSX (06, этап 4)."""
from __future__ import annotations

import io
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

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import FinanceObligation, FxRate, LegalEntity  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email, key=None: {"X-Dev-User": email, **({"Idempotency-Key": key} if key else {})}   # noqa: E731
MGR, CD, GD, FD = "manager@example.com", "cd@example.com", "evgeniy@dobrov.pro", "fd@example.com"
TODAY = date.today()


def _entity_with_accounts(c, run: str, currencies=("RUB",)) -> tuple[str, dict]:
    with SessionLocal() as db:
        e = LegalEntity(code=f"t{run[:10]}", name=f"Компания {run}", country="RU", currency="RUB")
        db.add(e)
        db.commit()
        eid = e.id
    own = c.get("/api/legal/own", headers=H(GD)).json()["items"]
    prof = next(x for x in own if x["id"] == eid)["profiles"][0]
    accounts = {}
    for cur in currencies:
        bank = c.post(f"/api/legal/profiles/{prof['id']}/banks", headers=H(GD), json={"title": f"{cur} {run}", "currency": cur, "bank_name": "Банк", "account": f"40{run}{cur}"}).json()
        a = c.post("/api/finance/accounts", headers=H(FD), json={"legal_entity_id": eid, "bank_profile_id": bank["id"]})
        assert a.status_code == 201, a.text
        accounts[cur] = a.json()
    return eid, accounts


def _ob(eid, direction, amount, currency, on, run, key):
    with SessionLocal() as db:
        o = FinanceObligation(source_type="contract", source_id=f"t-{run}", source_line_key=key, direction=direction, amount=Decimal(amount), currency=currency,
                              legal_due_on=on, forecast_on=on, legal_entity_id=eid, title=f"тест {key}", nature="contractual", is_active=True, sync_status="ok")
        db.add(o)
        db.commit()
        return o.id


def test_liquidity_starts_from_confirmed_balance_not_zero():
    seed()
    run = uuid.uuid4().hex[:10]
    with TestClient(app) as c:
        eid, acc = _entity_with_accounts(c, run, ("RUB", "CNY"))
        _ob(eid, "in", "500000", "RUB", TODAY + timedelta(days=10), run, "in1")
        _ob(eid, "out", "2000000", "RUB", TODAY + timedelta(days=20), run, "out1")
        late = _ob(eid, "in", "100000", "RUB", TODAY - timedelta(days=5), run, "late")
        _ob(eid, "in", "70000", "RUB", None, run, "nodate")
        _ob(eid, "out", "10000", "CNY", TODAY + timedelta(days=3), run, "cny")
        url = f"/api/finance/liquidity?legal_entity_id={eid}&group_by=day&horizon_days=30"
        # остаток не подтверждён — не ноль, а «неизвестен»; RUB-консолидация без этой валюты
        liq = c.get(url, headers=H(FD)).json()
        rub = next(x for x in liq["currencies"] if x["currency"] == "RUB")
        assert rub["balance_status"] == "unknown" and rub["opening_balance"] is None and all(p["balance"] is None for p in rub["points"])
        assert rub["minimum"] is None and rub["cash_gaps"] == []
        assert {"currency": "RUB", "reason": "входящий остаток не подтверждён"} in liq["rub"]["excluded"]
        assert rub["undated"]["in"] == "70000.00" and rub["undated"]["count"] == 1
        # подтверждаем остаток 1 000 000 — кривая от него; просрочка — в первой точке; без даты — вне кривой
        c.post(f"/api/finance/accounts/{acc['RUB']['id']}/balance-snapshots", headers=H(FD, f"ls-{run}"),
               json={"as_of": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), "amount": "1000000.00"})
        liq = c.get(url, headers=H(FD)).json()
        rub = next(x for x in liq["currencies"] if x["currency"] == "RUB")
        pts = {p["on"]: p for p in rub["points"]}
        assert rub["balance_status"] == "confirmed" and rub["opening_balance"] == "1000000.00"
        assert pts[TODAY.isoformat()]["inflow"] == "100000.00" and pts[TODAY.isoformat()]["late_in_point"] is True
        assert pts[TODAY.isoformat()]["balance"] == "1100000.00"
        assert pts[(TODAY + timedelta(days=10)).isoformat()]["balance"] == "1600000.00"
        assert pts[(TODAY + timedelta(days=20)).isoformat()]["balance"] == "-400000.00"
        assert rub["minimum"] == "-400000.00" and rub["minimum_on"] == (TODAY + timedelta(days=20)).isoformat()
        assert rub["cash_gaps"] and rub["cash_gaps"][0]["from"] == (TODAY + timedelta(days=20)).isoformat() and rub["cash_gaps"][0]["min"] == "-400000.00"
        # порог: разрыв считается и ниже заданного минимума
        rub_t = next(x for x in c.get(url + "&threshold=1200000", headers=H(FD)).json()["currencies"] if x["currency"] == "RUB")
        assert rub_t["cash_gaps"][0]["from"] == TODAY.isoformat()
        # входящий остаток ниже первой точки (просроченное поступление может не прийти) — минимум считается и от него
        with SessionLocal() as db:
            db.get(FinanceObligation, late).amount = Decimal("3000000")
            db.commit()
        rub_m = next(x for x in c.get(url + "&horizon_days=15", headers=H(FD)).json()["currencies"] if x["currency"] == "RUB")
        assert rub_m["minimum"] == "1000000.00" and rub_m["minimum_on"] == TODAY.isoformat()
        with SessionLocal() as db:
            db.get(FinanceObligation, late).amount = Decimal("100000")
            db.commit()
        # деньги пришли и разнесены (даже временно) — прогнозом не повторяются
        t = c.post("/api/finance/transactions", headers=H(FD, f"lt-{run}"), json={"account_id": acc["RUB"]["id"], "direction": "in", "amount": "100000.00", "currency": "RUB",
                                                                                 "booked_at": datetime.now(timezone.utc).isoformat()}).json()
        c.post(f"/api/finance/transactions/{t['id']}/post", headers=H(FD, f"lp-{run}"))
        c.post(f"/api/finance/transactions/{t['id']}/allocations", headers=H(FD, f"la-{run}"), json={"items": [{"obligation_id": late, "amount_tx": "100000.00"}]})
        rub = next(x for x in c.get(url, headers=H(FD)).json()["currencies"] if x["currency"] == "RUB")
        assert rub["opening_balance"] == "1100000.00" and {p["on"]: p for p in rub["points"]}[TODAY.isoformat()]["balance"] == "1100000.00"
        # объяснение точки: состав и входящий остаток
        ex = c.get(f"/api/finance/liquidity/explanation?currency=RUB&on={(TODAY + timedelta(days=20)).isoformat()}&group_by=day&horizon_days=30", headers=H(FD)).json()
        assert any(i["amount"] == "2000000.00" and i["direction"] == "out" for i in ex["items"])
        # CNY: курс ЦБ на дату → в RUB-консолидации; без курса — исключено с причиной
        c.post(f"/api/finance/accounts/{acc['CNY']['id']}/balance-snapshots", headers=H(FD, f"lc-{run}"),
               json={"as_of": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), "amount": "50000.00"})
        liq = c.get(url, headers=H(FD)).json()
        cny = next(x for x in liq["currencies"] if x["currency"] == "CNY")
        assert cny["opening_balance"] == "50000.00" and {p["on"]: p for p in cny["points"]}[(TODAY + timedelta(days=3)).isoformat()]["balance"] == "40000.00"
        if cny["fx"] is None:
            assert any(x["currency"] == "CNY" and "курса" in x["reason"] for x in liq["rub"]["excluded"])
            with SessionLocal() as db:
                db.add(FxRate(on=TODAY, currency="CNY", rate=12.5, source="cbr"))
                db.commit()
            liq = c.get(url, headers=H(FD)).json()
        assert "CNY" in liq["rub"]["included"] and any(f["currency"] == "CNY" and f["source"] == "ЦБ РФ" for f in liq["rub"]["fx"])
        rate = Decimal(next(f for f in liq["rub"]["fx"] if f["currency"] == "CNY")["rate"])
        cons = {p["on"]: Decimal(p["balance"]) for p in liq["rub"]["points"]}
        assert cons[(TODAY + timedelta(days=20)).isoformat()] == Decimal("-400000.00") + Decimal("40000.00") * rate
        # права
        assert c.get(url, headers=H(CD)).status_code == 403 and c.get(url, headers=H(MGR)).status_code == 403


CSV = ("Дата;Номер документа;Контрагент;Назначение платежа;Поступление;Списание;Валюта\n"
       "05.11.2026;101;АО «Рудник»;Аванс по договору К-1;1 250 000,50;;RUB\n"
       "06.11.2026;102;ООО «Брокер»;Услуги СВХ;;45 000,00;RUB\n"
       "07.11.2026;102;ООО «Брокер»;повтор номера;;1,00;RUB\n"
       "32.11.2026;103;Ошибка;плохая дата;10,00;;RUB\n"
       "08.11.2026;104;Банк;Комиссия;;350,00;USD\n"
       "08.11.2026;;Банк;Комиссия за обслуживание;;990,00;\n"
       "Итого;;;;1 250 010,50;46 341,00;\n")


def test_bank_statement_import_preview_confirm_idempotent():
    seed()
    run = uuid.uuid4().hex[:10]
    with TestClient(app) as c:
        eid, acc = _entity_with_accounts(c, run, ("RUB",))
        a = acc["RUB"]
        c.post(f"/api/finance/accounts/{a['id']}/balance-snapshots", headers=H(FD, f"is-{run}"), json={"as_of": "2026-11-01T00:00:00+03:00", "amount": "100000.00"})
        f = ("выписка.csv", CSV.encode("cp1251"), "text/csv")   # счёт новый на каждый прогон — номера документов не пересекаются
        assert c.post("/api/finance/imports/preview", headers=H(CD), data={"account_id": a["id"]}, files={"file": f}).status_code == 403
        assert c.post("/api/finance/imports/preview", headers=H(FD), data={"account_id": a["id"]}, files={"file": ("x.pdf", b"%PDF", "application/pdf")}).status_code == 422
        r = c.post("/api/finance/imports/preview", headers=H(FD), data={"account_id": a["id"]}, files={"file": f})
        assert r.status_code == 201, r.text
        p = r.json()
        st = {row["n"]: (row["status"], row["direction"], row["amount"]) for row in p["rows"]}
        assert st[2] == ("valid", "in", "1250000.50") and st[3] == ("valid", "out", "45000.00")
        assert st[4][0] == "error" and st[5][0] == "error" and st[6][0] == "error" and st[7] == ("valid", "out", "990.00")
        assert 8 not in st                                                                       # строка «Итого» пропущена
        assert p["rows_valid"] == 3 and p["rows_errors"] == 3 and p["totals"] == {"in": "1250000.50", "out": "45990.00"}
        assert any("повторяется" in e for e in next(x for x in p["rows"] if x["n"] == 4)["errors"])
        assert any("валюта строки USD" in e for e in next(x for x in p["rows"] if x["n"] == 6)["errors"])
        bal0 = next(x for x in c.get("/api/finance/accounts", headers=H(FD)).json()["items"] if x["id"] == a["id"])["balance"]
        assert bal0 == "100000.00"                                                               # preview ничего не проводит
        assert c.post(f"/api/finance/imports/{p['id']}/confirm", headers=H(FD), json={}).status_code == 400
        done = c.post(f"/api/finance/imports/{p['id']}/confirm", headers=H(FD, f"ic-{run}"), json={}).json()
        assert done["status"] == "confirmed" and done["result"]["created"] == 3 and done["result"]["errors"] == 3
        assert next(x for x in c.get("/api/finance/accounts", headers=H(FD)).json()["items"] if x["id"] == a["id"])["balance"] == "1304010.50"
        txs = c.get(f"/api/finance/transactions?account_id={a['id']}", headers=H(FD)).json()["items"]
        assert {t["origin_type"] for t in txs} == {"bank_import"} and all(t["status"] == "posted" for t in txs) and len(txs) == 3
        # повтор подтверждения — тот же результат; повторная загрузка того же файла — все строки дубли
        again = c.post(f"/api/finance/imports/{p['id']}/confirm", headers=H(FD, f"ic-{run}"), json={}).json()
        assert again["result"]["created"] == 3 and len(c.get(f"/api/finance/transactions?account_id={a['id']}", headers=H(FD)).json()["items"]) == 3
        p2 = c.post("/api/finance/imports/preview", headers=H(FD), data={"account_id": a["id"]}, files={"file": f}).json()
        assert p2["rows_valid"] == 0 and p2["rows_duplicates"] == 3 and p2["result"]["same_file_confirmed_at"]
        d2 = c.post(f"/api/finance/imports/{p2['id']}/confirm", headers=H(FD, f"ic2-{run}"), json={}).json()
        assert d2["result"]["created"] == 0 and d2["result"]["skipped_duplicates"] == 3

        # XLSX с колонками «Сумма» (±) и явным сопоставлением
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["Выписка по счёту"])
        ws.append(["Дата операции", "Сумма", "Плательщик / получатель", "Описание", "ID операции"])
        ws.append([datetime(2026, 11, 9), 5000, "ООО «Клиент»", "оплата счёта", f"X-{run}-1"])
        ws.append([datetime(2026, 11, 9), -1200.5, "ТК", "перевозка", f"X-{run}-2"])
        buf = io.BytesIO()
        wb.save(buf)
        r = c.post("/api/finance/imports/preview", headers=H(FD), data={"account_id": a["id"]}, files={"file": ("bank.xlsx", buf.getvalue(), "application/vnd.ms-excel")})
        assert r.status_code == 201, r.text
        x = r.json()
        assert [(row["direction"], row["amount"], row["external_ref"]) for row in x["rows"]] == [("in", "5000.00", f"X-{run}-1"), ("out", "1200.50", f"X-{run}-2")]
        part = c.post(f"/api/finance/imports/{x['id']}/confirm", headers=H(FD, f"ix-{run}"), json={"rows": [x["rows"][0]["n"]]}).json()
        assert part["result"]["created"] == 1
        # явное сопоставление колонок, если заголовки нестандартные
        bad = "a;b;c\n09.11.2026;777,00;Клиент\n"
        r = c.post("/api/finance/imports/preview", headers=H(FD), data={"account_id": a["id"]}, files={"file": ("odd.csv", bad.encode(), "text/csv")})
        assert r.status_code == 422 and "заголов" in r.text
        r = c.post("/api/finance/imports/preview", headers=H(FD), data={"account_id": a["id"], "mapping": '{"header_row": 0, "columns": {"date": 0, "amount": 1, "counterparty": 2}}'},
                   files={"file": ("odd.csv", bad.encode(), "text/csv")})
        assert r.status_code == 201 and r.json()["rows"][0]["amount"] == "777.00" and r.json()["rows"][0]["direction"] == "in"
