"""«Финансы», этап 3 (D-117/D-118): старые отметки оплаты — этап исполнения, отметка в договоре, ручной платёж план/факта —
становятся записями «требует сверки» без счёта: не входят в подтверждённый остаток, не дублируются при повторе,
временно разносятся только при однозначном соответствии; ФД выбирает счёт и проводит либо отзывает запись."""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone

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
from app.models import Deal, ExecutionStage, FinanceTransaction  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email, key=None: {"X-Dev-User": email, **({"Idempotency-Key": key} if key else {})}   # noqa: E731
MGR, CD, GD, FD, LAW = "manager@example.com", "cd@example.com", "evgeniy@dobrov.pro", "fd@example.com", "lawyer@example.com"
PDF = ("scan.pdf", b"%PDF-1.4 test", "application/pdf")
RUN = uuid.uuid4().hex[:10]


def _signed(c, amount, terms, deal_id=None):
    ent = c.get("/api/legal/contracts", headers=H(LAW)).json()["entities"][0]
    body = {"kind": "client_supply", "own_entity_id": ent["id"], "cp_name": f"АО «Legacy {RUN}»", "cp_inn": "7701000002", "subject": "поставка", "amount": amount, "currency": "RUB"}
    if deal_id:
        body["deal_id"] = deal_id
    k = c.post("/api/legal/contracts", headers=H(LAW), json=body).json()
    c.put(f"/api/legal/contracts/{k['id']}", headers=H(LAW), json={"payment_terms": terms, "signatory": {"name": "П. П.", "basis": "Устава"}, "law": {"claim_days": 30},
                                                                  "signed_on": "2026-11-02"})
    c.post(f"/api/legal/contracts/{k['id']}/files", headers=H(LAW), files={"file": PDF}, data={"kind": "signed_scan"})
    r = c.post(f"/api/legal/contracts/{k['id']}/status", headers=H(LAW), json={"status": "active"})
    assert r.status_code == 200, r.text
    return r.json()


def _account(c, ent_id):
    own = c.get("/api/legal/own", headers=H(GD)).json()["items"]
    prof = next(e for e in own if e["id"] == ent_id)["profiles"][0]
    bank = c.post(f"/api/legal/profiles/{prof['id']}/banks", headers=H(GD), json={"title": f"RUB {RUN}", "currency": "RUB", "bank_name": "Банк", "account": f"40702{RUN}"}).json()
    a = c.post("/api/finance/accounts", headers=H(FD), json={"legal_entity_id": ent_id, "bank_profile_id": bank["id"]}).json()
    c.post(f"/api/finance/accounts/{a['id']}/balance-snapshots", headers=H(FD, f"snap-{RUN}"),
           json={"as_of": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(), "amount": "1000000.00"})
    return a


def _balance(c, aid):
    return next(a for a in c.get("/api/finance/accounts", headers=H(FD)).json()["items"] if a["id"] == aid)["balance"]


def _claims(origin_id_like: str):
    with SessionLocal() as db:
        return list(db.scalars(select(FinanceTransaction).where(FinanceTransaction.origin_id.like(origin_id_like))).all())


def test_legacy_payment_marks_go_to_reconciliation():
    seed()
    with TestClient(app) as c:
        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        d = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": f"legacy-{RUN}"}).json()
        with SessionLocal() as db:
            deal = db.get(Deal, d["id"])
            deal.status = DealStatus.CONTRACT
            st = ExecutionStage(deal_id=deal.id, code="client_advance", title="Аванс от клиента", sort=30, amount=500000, currency="RUB", responsible_roles=["fd"],
                                planned_on=date(2026, 11, 9))
            db.add(st)
            db.commit()
            stage_id = st.id
        k = _signed(c, 500_000, [{"share_pct": 100, "event": "contract", "offset_days": 3, "offset_kind": "calendar"}], deal_id=d["id"])
        ob = c.get(f"/api/finance/obligations?contract_id={k['id']}", headers=H(FD)).json()["items"][0]
        acct = _account(c, c.get("/api/legal/contracts", headers=H(LAW)).json()["entities"][0]["id"])
        bal0 = _balance(c, acct["id"])

        # --- факт платёжного этапа → «требует сверки», однозначная строка → временное разнесение; остаток не меняется
        url = f"/api/deals/{d['id']}/execution/stages/{stage_id}/actual"
        r = c.post(url, headers=H(FD), json={"actual_on": "2026-11-05", "actual_amount": 500000, "note": "по выписке"})
        assert r.status_code == 200, r.text
        fin = r.json()["finance"]
        assert fin["status"] == "needs_reconciliation" and "не входит" in fin["warning"] and fin["url"].startswith("/finance?tab=liquidity&tx=")
        tx_id = fin["url"].split("tx=")[1]
        tx = c.get(f"/api/finance/transactions/{tx_id}", headers=H(FD)).json()
        assert tx["status"] == "needs_reconciliation" and tx["account_id"] is None and tx["origin_type"] == "execution_legacy" and tx["origin_title"]
        assert [a["status"] for a in tx["allocations"]] == ["provisional"] and tx["allocations"][0]["obligation_id"] == ob["id"]
        row = c.get(f"/api/finance/obligations/{ob['id']}", headers=H(FD)).json()
        assert row["confirmed_paid"] == "0.00" and row["provisional_paid"] == "500000.00" and "needs_reconciliation" in row["flags"]
        assert _balance(c, acct["id"]) == bal0                                                     # не банковский факт
        # повтор той же отметки — без дубля; без права «Финансы» ссылки нет
        c.post(url, headers=H(FD), json={"actual_on": "2026-11-05", "actual_amount": 500000})
        assert len(_claims(f"stage:{stage_id}")) == 1
        stages = c.get(f"/api/deals/{d['id']}/execution/stages", headers=H(FD)).json()
        assert next(s for s in stages["stages"] if s["id"] == stage_id)["finance"]["status"] == "needs_reconciliation"
        assert stages["finance_url"] == f"/finance?tab=calendar&deal_id={d['id']}"
        assert "finance_url" not in c.get(f"/api/deals/{d['id']}/execution/stages", headers=H(MGR)).json() or \
            c.get(f"/api/deals/{d['id']}/execution/stages", headers=H(MGR)).json()["finance_url"] is None
        # изменили сумму — прежнее разнесение сторнировано; сумма не совпадает со строкой → без авто-разнесения (ручная сверка)
        c.post(url, headers=H(FD), json={"actual_on": "2026-11-06", "actual_amount": 400000})
        tx = c.get(f"/api/finance/transactions/{tx_id}", headers=H(FD)).json()
        assert tx["amount"] == "400000.00" and {a["status"] for a in tx["allocations"]} == {"reversed"}
        # сняли факт — запись отозвана (история остаётся), из сверки ушла
        c.post(url, headers=H(FD), json={"actual_on": None, "actual_amount": None})
        tx = c.get(f"/api/finance/transactions/{tx_id}", headers=H(FD)).json()
        assert tx["withdrawn"] is True and tx["unallocated"] == "0.00"
        assert tx_id not in [t["id"] for t in c.get("/api/finance/transactions?status=needs_reconciliation", headers=H(FD)).json()["items"]]
        # снова отметили — та же запись, снова в сверке и снова однозначно разнесена
        c.post(url, headers=H(FD), json={"actual_on": "2026-11-05", "actual_amount": 500000})
        tx = c.get(f"/api/finance/transactions/{tx_id}", headers=H(FD)).json()
        assert tx["withdrawn"] is False and any(a["status"] == "provisional" for a in tx["allocations"])

        # --- ФД сверяет: выбирает реальный счёт, проводит, подтверждает разнесение → факт и остаток
        assert c.post(f"/api/finance/transactions/{tx_id}/post", headers=H(FD, f"lp0-{RUN}")).status_code == 422   # без счёта не проводится
        assert c.post(f"/api/finance/transactions/{tx_id}/assign-account", headers=H(CD), json={"account_id": acct["id"]}).status_code == 403
        r = c.post(f"/api/finance/transactions/{tx_id}/assign-account", headers=H(FD), json={"account_id": acct["id"], "external_ref": f"pp-{RUN}"})
        assert r.status_code == 200 and r.json()["account_id"] == acct["id"] and r.json()["status"] == "needs_reconciliation"
        assert c.post(f"/api/finance/transactions/{tx_id}/post", headers=H(FD, f"lp1-{RUN}")).json()["status"] == "posted"
        al = next(a for a in c.get(f"/api/finance/transactions/{tx_id}", headers=H(FD)).json()["allocations"] if a["status"] == "provisional")
        assert c.post(f"/api/finance/allocations/{al['id']}/confirm", headers=H(FD)).json()["status"] == "confirmed"
        assert c.get(f"/api/finance/obligations/{ob['id']}", headers=H(FD)).json()["status"] == "paid"
        assert _balance(c, acct["id"]) == f"{float(bal0) + 500000:.2f}"
        # старая отметка после подтверждения не переписывает факт
        r = c.post(url, headers=H(FD), json={"actual_on": "2026-11-07", "actual_amount": 1})
        assert r.json()["finance"]["status"] == "confirmed_in_finance"
        assert c.get(f"/api/finance/transactions/{tx_id}", headers=H(FD)).json()["amount"] == "500000.00"
        assert c.post(f"/api/finance/transactions/{tx_id}/withdraw", headers=H(FD), json={"reason": "дубль"}).status_code == 409

        # --- отметка «оплачено» в договоре без сделки: явная строка → временное разнесение; снятие → отзыв
        k2 = _signed(c, 300_000, [{"share_pct": 50, "event": "contract", "offset_days": 3, "offset_kind": "calendar"},
                                  {"share_pct": 50, "event": "contract", "offset_days": 10, "offset_kind": "calendar"}])
        obs = c.get(f"/api/finance/obligations?contract_id={k2['id']}", headers=H(FD)).json()["items"]
        r = c.post(f"/api/legal/contracts/{k2['id']}/facts", headers=H(FD), json={"kind": "pay", "key": "2", "paid_on": "2026-11-08"})
        assert r.status_code == 200 and r.json()["finance"]["status"] == "needs_reconciliation", r.text
        line2 = next(o for o in obs if o["source"]["line_key"] == "payment:2")
        assert c.get(f"/api/finance/obligations/{line2['id']}", headers=H(FD)).json()["provisional_paid"] == "150000.00"
        c.post(f"/api/legal/contracts/{k2['id']}/facts", headers=H(FD), json={"kind": "pay", "key": "2", "clear": True})
        assert c.get(f"/api/finance/obligations/{line2['id']}", headers=H(FD)).json()["provisional_paid"] == "0.00"
        assert _claims(f"{k2['id']}:pay:2")[0].withdrawn_at is not None

        # --- ручной платёж план/факта → запись сверки; удаление записи → отзыв; платёж этапа второй записи не создаёт
        r = c.post(f"/api/deals/{d['id']}/fact", headers=H(FD), json={"kind": "payment_in", "amount": 12345, "currency": "RUB", "on": "2026-11-09", "comment": "вручную"})
        assert r.status_code == 201 and r.json()["finance"]["status"] == "needs_reconciliation", r.text
        eid = r.json()["entry"]["id"]
        assert _claims(eid)[0].status == "needs_reconciliation"
        assert c.delete(f"/api/deals/{d['id']}/fact/{eid}", headers=H(FD)).status_code == 200
        assert _claims(eid)[0].withdrawn_at is not None
        assert not _claims("%stage:%") or all(t.origin_type == "execution_legacy" for t in _claims("%stage:%"))

        # --- ручной отзыв записи сверки ФД (не платёж) и обзор без отозванных
        r = c.post(f"/api/deals/{d['id']}/fact", headers=H(FD), json={"kind": "payment_out", "amount": 777, "currency": "RUB", "on": "2026-11-09"})
        t2 = _claims(r.json()["entry"]["id"])[0].id
        assert c.post(f"/api/finance/transactions/{t2}/withdraw", headers=H(FD), json={"reason": ""}).status_code == 422
        assert c.post(f"/api/finance/transactions/{t2}/withdraw", headers=H(FD), json={"reason": "внутренний перевод, не платёж"}).json()["withdrawn"] is True
        assert c.post(f"/api/finance/transactions/{t2}/post", headers=H(FD, f"lp2-{RUN}")).status_code == 409
        assert t2 in [t["id"] for t in c.get("/api/finance/transactions?include_withdrawn=true&origin=legacy", headers=H(FD)).json()["items"]]
