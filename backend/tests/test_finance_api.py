"""Раздел «Финансы», вариант Б — первый вертикальный срез (docs/finance/06_План_внедрения.md, этапы 1–3):
подписанный договор → отдельные строки обязательств → счёт и подтверждённый остаток → банковская операция → частичное разнесение →
вторая операция → закрытие остатка → сторно → конфликт при изменении источника → права и аудит."""
from __future__ import annotations

import os
import tempfile
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

from app.main import app  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

# Договор в тесте подписан 02.11.2026, срок первого платежа — 10.11.2026. Статусы «скоро срок / частично оплачено» проверяем на этот день,
# а не на сегодняшний: иначе тест «протухает» после срока (D-030, docs/operations/15).
AS_OF = "2026-11-10"
H = lambda email, key=None: {"X-Dev-User": email, **({"Idempotency-Key": key} if key else {})}   # noqa: E731
MGR, CD, GD, FD, LAW, LOG = "manager@example.com", "cd@example.com", "evgeniy@dobrov.pro", "fd@example.com", "lawyer@example.com", "logist@example.com"
PDF = ("scan.pdf", b"%PDF-1.4 test", "application/pdf")
RUN = datetime.now(timezone.utc).strftime("%H%M%S%f")   # уникальность ключей между прогонами на одной БД


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _signed_client_contract(c: TestClient, amount: float, terms: list[dict]) -> dict:
    ent = c.get("/api/legal/contracts", headers=H(LAW)).json()["entities"][0]
    k = c.post("/api/legal/contracts", headers=H(LAW), json={"kind": "client_parts", "own_entity_id": ent["id"], "cp_name": f"АО «Рудник {RUN}»", "cp_inn": "7701000001",
                                                             "subject": "поставка запасных частей", "amount": amount, "currency": "RUB"}).json()
    k = c.put(f"/api/legal/contracts/{k['id']}", headers=H(LAW), json={"payment_terms": terms, "signatory": {"name": "Петров П. П.", "basis": "Устава"},
                                                                     "law": {"claim_days": 30}, "signed_on": "2026-11-02"}).json()
    assert k["check"]["ok"], k["check"]
    c.post(f"/api/legal/contracts/{k['id']}/files", headers=H(LAW), files={"file": PDF}, data={"kind": "signed_scan"})
    r = c.post(f"/api/legal/contracts/{k['id']}/status", headers=H(LAW), json={"status": "active"})
    assert r.status_code == 200, r.text
    return r.json()


def _own_bank(c: TestClient, entity_id: str, currency: str) -> str:
    own = c.get("/api/legal/own", headers=H(GD)).json()["items"]
    prof = next(e for e in own if e["id"] == entity_id)["profiles"][0]
    r = c.post(f"/api/legal/profiles/{prof['id']}/banks", headers=H(GD), json={"title": f"Расчётный {currency} {RUN}", "currency": currency, "bank_name": "ПАО Банк",
                                                                                "account": f"40702810{RUN[:12]:0>12}", "bik": "044525225"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_finance_vertical_slice():
    seed()
    with TestClient(app) as c:
        # --- права на раздел: по умолчанию ФД и ЗГД; менеджер, логист и КД не видят (D-116)
        assert c.get("/api/finance/obligations", headers=H(MGR)).status_code == 403
        assert c.get("/api/finance/overview", headers=H(LOG)).status_code == 403
        assert c.get("/api/finance/meta", headers=H(CD)).status_code == 403
        assert c.get("/api/finance/obligations", headers=H(CD)).status_code == 403
        assert c.get("/api/finance/meta", headers=H(GD)).json()["can_edit"] is True
        assert c.get("/api/finance/meta", headers=H(FD)).json()["can_edit"] is True

        # --- подписанный договор → каждая строка графика — отдельное обязательство (два аванса не объединяются)
        k = _signed_client_contract(c, 1_000_000, [
            {"share_pct": 20, "event": "contract", "offset_days": 5, "offset_kind": "working"},
            {"share_pct": 10, "event": "contract", "offset_days": 10, "offset_kind": "calendar"},
            {"share_pct": 70, "event": "act_signed", "offset_days": 10, "offset_kind": "calendar"}])
        reg = c.get(f"/api/finance/obligations?contract_id={k['id']}&as_of={AS_OF}", headers=H(FD)).json()
        assert reg["total"] == 3, reg
        a1, a2, fin = reg["items"][0], reg["items"][1], next(x for x in reg["items"] if x["event"]["code"] == "act_signed")
        assert (a1["amount"], a2["amount"], fin["amount"]) == ("200000.00", "100000.00", "700000.00")
        assert a1["direction"] == "in" and a1["nature"] == "contractual" and a1["currency"] == "RUB"
        assert a1["legal_due_on"] == "2026-11-10" and a1["status"] == "due_soon"                 # 5 рабочих дней от подписания 02.11
        assert fin["legal_due_on"] is None and fin["status"] == "no_date" and "акт" in fin["legal_due_text"].lower()   # событие не наступило — срок не выдуман
        assert a1["source"]["url"] == f"/legal?tab=contracts&open={k['id']}" and a1["contract"]["number"] == k["number"]
        assert reg["totals"]["RUB"]["in"] == "1000000.00"
        # повторная сборка не плодит строк; dry-run ничего не меняет
        before = c.get("/api/finance/obligations?limit=500", headers=H(FD)).json()["total"]
        dry = c.post("/api/finance/sync?dry_run=true", headers=H(FD)).json()     # первичная сборка ранее подписанных договоров — только отчёт
        assert dry["dry_run"] is True
        assert c.get("/api/finance/obligations?limit=500", headers=H(FD)).json()["total"] == before
        real = c.post("/api/finance/sync?dry_run=false", headers=H(FD)).json()
        assert real["created"] == dry["created"] and real["active_sums"] == dry["active_sums"]
        dry2 = c.post("/api/finance/sync?dry_run=true", headers=H(FD)).json()
        assert dry2["created"] == 0 and dry2["updated"] == 0 and dry2["conflicts"] == real["conflicts"] and dry2["active_sums"] == real["active_sums"]
        assert c.post("/api/finance/sync?dry_run=true", headers=H(CD)).status_code == 403
        assert c.get(f"/api/finance/obligations?contract_id={k['id']}", headers=H(FD)).json()["total"] == 3
        # финансовый API не редактирует договор
        assert c.put(f"/api/finance/obligations/{a1['id']}", headers=H(FD), json={"amount": "1"}).status_code == 405

        # --- счёт на существующем банковском профиле компании группы; остаток без снимка — неизвестен, не ноль
        ent_id = c.get("/api/legal/contracts", headers=H(LAW)).json()["entities"][0]["id"]
        bank = _own_bank(c, ent_id, "RUB")
        assert c.post("/api/finance/accounts", headers=H(CD), json={"legal_entity_id": ent_id, "bank_profile_id": bank}).status_code == 403
        assert c.post("/api/finance/accounts", headers=H(FD), json={"legal_entity_id": ent_id, "bank_profile_id": bank, "currency": "USD"}).status_code == 422
        other = next(e for e in c.get("/api/finance/meta", headers=H(FD)).json()["entities"] if e["id"] != ent_id)
        assert c.post("/api/finance/accounts", headers=H(FD), json={"legal_entity_id": other["id"], "bank_profile_id": bank}).status_code == 422
        r = c.post("/api/finance/accounts", headers=H(FD), json={"legal_entity_id": ent_id, "bank_profile_id": bank, "title": "Расчётный RUB"})
        assert r.status_code == 201, r.text
        acct = r.json()
        assert acct["balance"] is None and acct["balance_status"] == "unknown" and acct["account_masked"].startswith("…") and "account" not in acct
        assert c.post("/api/finance/accounts", headers=H(FD), json={"legal_entity_id": ent_id, "bank_profile_id": bank}).status_code == 409
        opts = c.get("/api/finance/accounts/options", headers=H(FD)).json()["items"]
        assert next(o for o in opts if o["bank_profile_id"] == bank)["has_account"] is True

        snap_at = datetime.now(timezone.utc) - timedelta(days=1)
        assert c.post(f"/api/finance/accounts/{acct['id']}/balance-snapshots", headers=H(FD), json={"as_of": _iso(snap_at), "amount": "5000000"}).status_code == 400
        r = c.post(f"/api/finance/accounts/{acct['id']}/balance-snapshots", headers=H(FD, f"snap-{RUN}"), json={"as_of": _iso(snap_at), "amount": "5000000.00", "external_ref": "выписка 09.11"})
        assert r.status_code == 201 and r.json()["account"]["balance"] == "5000000.00"
        again = c.post(f"/api/finance/accounts/{acct['id']}/balance-snapshots", headers=H(FD, f"snap-{RUN}"), json={"as_of": _iso(snap_at), "amount": "5000000.00"})
        assert again.json()["id"] == r.json()["id"]                                                   # идемпотентно

        # --- первая операция: черновик → проведение (ключ обязателен, повтор — тот же результат)
        t1 = c.post("/api/finance/transactions", headers=H(FD, f"tx1-{RUN}"), json={"account_id": acct["id"], "direction": "in", "amount": "150000.00", "currency": "RUB",
                                                                                    "booked_at": _iso(datetime.now(timezone.utc)), "counterparty_name": "АО Рудник", "purpose": "аванс",
                                                                                    "external_ref": f"PP-{RUN}-1"}).json()
        assert t1["status"] == "draft" and t1["unallocated"] == "150000.00"
        assert c.post("/api/finance/transactions", headers=H(FD, f"tx1-{RUN}"), json={"account_id": acct["id"], "direction": "in", "amount": "150000.00", "currency": "RUB",
                                                                                      "booked_at": _iso(datetime.now(timezone.utc))}).json()["id"] == t1["id"]
        assert c.post("/api/finance/transactions", headers=H(FD), json={"account_id": acct["id"], "direction": "in", "amount": "-5", "currency": "RUB",
                                                                        "booked_at": _iso(datetime.now(timezone.utc))}).status_code == 422
        assert c.post("/api/finance/transactions", headers=H(FD), json={"account_id": acct["id"], "direction": "in", "amount": "5", "currency": "USD",
                                                                        "booked_at": _iso(datetime.now(timezone.utc))}).status_code == 422
        assert c.post(f"/api/finance/transactions/{t1['id']}/post", headers=H(FD)).status_code == 400
        assert c.post(f"/api/finance/transactions/{t1['id']}/post", headers=H(CD, "x")).status_code == 403
        p1 = c.post(f"/api/finance/transactions/{t1['id']}/post", headers=H(FD, f"post1-{RUN}")).json()
        assert p1["status"] == "posted"
        assert c.post(f"/api/finance/transactions/{t1['id']}/post", headers=H(FD, f"post1-{RUN}")).json()["status"] == "posted"
        bal = c.get("/api/finance/accounts", headers=H(FD)).json()["items"]
        assert next(a for a in bal if a["id"] == acct["id"])["balance"] == "5150000.00"

        # --- частичное разнесение одной операции на две строки: временное → не входит в оплату, подтверждение — отдельным действием
        r = c.post(f"/api/finance/transactions/{t1['id']}/allocations", headers=H(FD, f"al1-{RUN}"),
                   json={"items": [{"obligation_id": a1["id"], "amount_tx": "120000.00"}, {"obligation_id": a2["id"], "amount_tx": "30000.00"}]})
        assert r.status_code == 201, r.text
        tx = r.json()
        assert tx["unallocated"] == "0.00" and len(tx["allocations"]) == 2 and {x["status"] for x in tx["allocations"]} == {"provisional"}
        again = c.post(f"/api/finance/transactions/{t1['id']}/allocations", headers=H(FD, f"al1-{RUN}"),
                       json={"items": [{"obligation_id": a1["id"], "amount_tx": "120000.00"}, {"obligation_id": a2["id"], "amount_tx": "30000.00"}]}).json()
        assert len(again["allocations"]) == 2                                                          # повтор ключа — без дублей
        card = c.get(f"/api/finance/obligations/{a1['id']}?as_of={AS_OF}", headers=H(FD)).json()
        assert card["confirmed_paid"] == "0.00" and card["provisional_paid"] == "120000.00" and card["remaining"] == "200000.00" and "needs_reconciliation" in card["flags"]
        for al in tx["allocations"]:
            assert c.post(f"/api/finance/allocations/{al['id']}/confirm", headers=H(CD)).status_code == 403
            assert c.post(f"/api/finance/allocations/{al['id']}/confirm", headers=H(FD)).json()["status"] == "confirmed"
        card = c.get(f"/api/finance/obligations/{a1['id']}?as_of={AS_OF}", headers=H(FD)).json()
        assert card["confirmed_paid"] == "120000.00" and card["remaining"] == "80000.00" and card["status"] == "partially_paid" and card["flags"] == []
        assert card["allocations"][0]["transaction"]["external_ref"] == f"PP-{RUN}-1" and "договоре" in card["readonly_note"]
        ex = c.get(f"/api/finance/obligations/{a1['id']}/explanation", headers=H(FD)).json()
        assert ex["value"] == "80000.00" and len(ex["included"]) == 1 and ex["source"]["line_key"] == "payment:1"

        # --- вторая операция закрывает остаток; превышение и встречное направление — отказ
        t2 = c.post("/api/finance/transactions", headers=H(FD, f"tx2-{RUN}"), json={"account_id": acct["id"], "direction": "in", "amount": "150000.00", "currency": "RUB",
                                                                                    "booked_at": _iso(datetime.now(timezone.utc)), "external_ref": f"PP-{RUN}-2"}).json()
        c.post(f"/api/finance/transactions/{t2['id']}/post", headers=H(FD, f"post2-{RUN}"))
        over = c.post(f"/api/finance/transactions/{t2['id']}/allocations", headers=H(FD, f"al2x-{RUN}"), json={"items": [{"obligation_id": a1["id"], "amount_tx": "90000.00"}], "status": "confirmed"})
        assert over.status_code == 409 and "остаток" in over.text
        too_much = c.post(f"/api/finance/transactions/{t2['id']}/allocations", headers=H(FD, f"al2y-{RUN}"),
                          json={"items": [{"obligation_id": a1["id"], "amount_tx": "80000.00"}, {"obligation_id": fin["id"], "amount_tx": "80000.00"}], "status": "confirmed"})
        assert too_much.status_code == 409 and "превышает" in too_much.text
        r = c.post(f"/api/finance/transactions/{t2['id']}/allocations", headers=H(FD, f"al2-{RUN}"),
                   json={"items": [{"obligation_id": a1["id"], "amount_tx": "80000.00"}, {"obligation_id": a2["id"], "amount_tx": "70000.00"}], "status": "confirmed"})
        assert r.status_code == 201, r.text
        rows = {x["id"]: x for x in c.get(f"/api/finance/obligations?contract_id={k['id']}", headers=H(FD)).json()["items"]}
        assert rows[a1["id"]]["status"] == "paid" and rows[a1["id"]]["remaining"] == "0.00"
        assert rows[a2["id"]]["status"] == "paid" and rows[a2["id"]]["confirmed_paid"] == "100000.00"
        out = c.post("/api/finance/transactions", headers=H(FD, f"tx3-{RUN}"), json={"account_id": acct["id"], "direction": "out", "amount": "1000.00", "currency": "RUB",
                                                                                     "booked_at": _iso(datetime.now(timezone.utc))}).json()
        c.post(f"/api/finance/transactions/{out['id']}/post", headers=H(FD, f"post3-{RUN}"))
        assert c.post(f"/api/finance/transactions/{out['id']}/allocations", headers=H(FD, f"al3-{RUN}"), json={"items": [{"obligation_id": fin["id"], "amount_tx": "1000.00"}]}).status_code == 422

        # --- дубль банковского номера по счёту не проводится
        dup = c.post("/api/finance/transactions", headers=H(FD, f"tx4-{RUN}"), json={"account_id": acct["id"], "direction": "in", "amount": "150000.00", "currency": "RUB",
                                                                                     "booked_at": _iso(datetime.now(timezone.utc)), "external_ref": f"PP-{RUN}-2"}).json()
        assert c.post(f"/api/finance/transactions/{dup['id']}/post", headers=H(FD, f"post4-{RUN}")).status_code == 409

        # --- сторно второй операции: запись не удаляется, разнесения сторнируются, остаток счёта и долг возвращаются
        assert c.post(f"/api/finance/transactions/{t2['id']}/reverse", headers=H(FD, f"rev2-{RUN}"), json={"reason": ""}).status_code == 422
        rv = c.post(f"/api/finance/transactions/{t2['id']}/reverse", headers=H(FD, f"rev2-{RUN}"), json={"reason": "ошибочно разнесено"})
        assert rv.status_code == 200, rv.text
        rv = rv.json()
        assert rv["original"]["status"] == "reversed" and rv["reversal"]["direction"] == "out" and rv["reversal"]["origin_type"] == "reversal"
        assert {a["status"] for a in rv["original"]["allocations"]} == {"reversed"}
        assert c.post(f"/api/finance/transactions/{t2['id']}/reverse", headers=H(FD, f"rev2-{RUN}"), json={"reason": "ошибочно разнесено"}).json()["reversal"]["id"] == rv["reversal"]["id"]
        assert c.post(f"/api/finance/transactions/{t2['id']}/reverse", headers=H(FD, f"rev2b-{RUN}"), json={"reason": "ещё раз"}).status_code == 409
        assert c.post(f"/api/finance/transactions/{rv['reversal']['id']}/allocations", headers=H(FD, f"al5-{RUN}"),
                      json={"items": [{"obligation_id": a1["id"], "amount_tx": "1.00"}]}).status_code == 409
        rows = {x["id"]: x for x in c.get(f"/api/finance/obligations?contract_id={k['id']}&as_of={AS_OF}", headers=H(FD)).json()["items"]}
        assert rows[a1["id"]]["remaining"] == "80000.00" and rows[a1["id"]]["status"] == "partially_paid"
        acc_now = next(a for a in c.get("/api/finance/accounts", headers=H(FD)).json()["items"] if a["id"] == acct["id"])
        assert acc_now["balance"] == "5149000.00"                                                    # 5 000 000 + 150 000 + 150 000 − 150 000 (сторно) − 1 000

        # --- источник изменился после разнесения: перезаписи нет, строка уходит в сверку
        r = c.put(f"/api/legal/contracts/{k['id']}", headers=H(GD), json={"amount": 1_100_000, "correction_reason": "опечатка в цене"})
        assert r.status_code == 200, r.text
        rows = {x["id"]: x for x in c.get(f"/api/finance/obligations?contract_id={k['id']}", headers=H(FD)).json()["items"]}
        assert rows[a1["id"]]["sync_status"] == "conflict" and rows[a1["id"]]["amount"] == "200000.00" and "conflict" in rows[a1["id"]]["flags"]
        assert rows[fin["id"]]["sync_status"] == "ok" and rows[fin["id"]]["amount"] == "770000.00"   # без разнесений — обновлено
        assert c.get(f"/api/finance/obligations/{a1['id']}", headers=H(FD)).json()["pending"]["amount"] == "220000.00"
        t5 = c.post("/api/finance/transactions", headers=H(FD, f"tx5-{RUN}"), json={"account_id": acct["id"], "direction": "in", "amount": "10.00", "currency": "RUB",
                                                                                    "booked_at": _iso(datetime.now(timezone.utc))}).json()
        assert c.post(f"/api/finance/transactions/{t5['id']}/allocations", headers=H(FD, f"al6-{RUN}"),
                      json={"items": [{"obligation_id": a1["id"], "amount_tx": "10.00"}]}).status_code == 409   # сначала сверка
        assert len(c.get("/api/finance/obligations?reconciliation=true", headers=H(FD)).json()["items"]) >= 1

        # --- аудит и обзор
        aud = c.get(f"/api/admin/audit?entity=finance_transaction&limit=1000&q={t2['id']}", headers=H(GD)).json()
        assert {"create", "post", "reverse"} <= {x["action"] for x in aud if x["entity_id"] == t2["id"]}
        post_rec = next(x for x in aud if x["entity_id"] == t2["id"] and x["action"] == "post")
        assert post_rec["before"]["status"] == "draft" and post_rec["after"]["status"] == "posted" and post_rec["after"]["idempotency_key"] == f"post2-{RUN}"
        al_aud = c.get("/api/admin/audit?entity=finance_allocation&limit=50", headers=H(GD)).json()
        assert any(x["action"] == "reverse" for x in al_aud) and any(x["action"] == "confirm" for x in al_aud)
        ov = c.get("/api/finance/overview", headers=H(GD)).json()
        rub = next(x for x in ov["available"] if x["currency"] == "RUB")
        assert rub["status"] in ("confirmed", "partial") and ov["reconciliation"]["obligation_conflicts"] >= 1
        assert c.get("/api/finance/overview", headers=H(MGR)).status_code == 403


def test_finance_hidden_object_is_404_and_money_is_decimal():
    from app.services.finance.money import dec, s
    assert dec(0.1) + dec(0.2) == Decimal("0.30") and s(dec("1 250 000,5")) == "1250000.50"
    with pytest.raises(ValueError):
        dec("abc")
    with TestClient(app) as c:
        assert c.get("/api/finance/obligations/00000000-0000-0000-0000-000000000000", headers=H(FD)).status_code == 404
        assert c.get("/api/finance/transactions/00000000-0000-0000-0000-000000000000", headers=H(FD)).status_code == 404


def test_finance_allocation_race_does_not_overpay():
    """Два параллельных разнесения на одну строку: блокировка FOR UPDATE пропускает только одно, второе — 409."""
    import threading
    seed()
    with TestClient(app) as c:
        k = _signed_client_contract(c, 100_000, [{"share_pct": 100, "event": "contract", "offset_days": 3, "offset_kind": "calendar"}])
        ob = c.get(f"/api/finance/obligations?contract_id={k['id']}", headers=H(FD)).json()["items"][0]
        ent_id = c.get("/api/legal/contracts", headers=H(LAW)).json()["entities"][0]["id"]
        acct = c.post("/api/finance/accounts", headers=H(FD), json={"legal_entity_id": ent_id, "bank_profile_id": _own_bank(c, ent_id, "RUB")}).json()
        tids = []
        for i in range(2):
            t = c.post("/api/finance/transactions", headers=H(FD, f"race-tx{i}-{RUN}"), json={"account_id": acct["id"], "direction": "in", "amount": "80000.00",
                                                                                            "currency": "RUB", "booked_at": _iso(datetime.now(timezone.utc))}).json()
            c.post(f"/api/finance/transactions/{t['id']}/post", headers=H(FD, f"race-post{i}-{RUN}"))
            tids.append(t["id"])
    codes: list[int] = []
    barrier = threading.Barrier(2)

    def go(i: int) -> None:
        with TestClient(app) as cc:
            barrier.wait()
            r = cc.post(f"/api/finance/transactions/{tids[i]}/allocations", headers=H(FD, f"race-al{i}-{RUN}"),
                        json={"items": [{"obligation_id": ob["id"], "amount_tx": "80000.00"}], "status": "confirmed"})
            codes.append(r.status_code)

    th = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    [t.start() for t in th]
    [t.join() for t in th]
    assert sorted(codes) == [201, 409], codes
    with TestClient(app) as c:
        row = c.get(f"/api/finance/obligations/{ob['id']}", headers=H(FD)).json()
        assert row["confirmed_paid"] == "80000.00" and row["remaining"] == "20000.00"


def test_finance_section_closed_for_cd_by_default_and_grantable():
    """D-116: раздел «Финансы» по умолчанию закрыт КД; администратор/ЗГД выдаёт право исключением — тогда КД читает,
    но банковский факт не вводит (execution_payments у КД нет). Суммы в сделках и «Сводка → Деньги» у КД остаются."""
    seed()
    with TestClient(app) as c:
        me = c.get("/api/auth/me", headers=H(CD)).json()
        assert me["perms"]["finance_section"] == 0 and me["perms"]["finance_analytics"] >= 1
        assert c.get("/api/summary/money", headers=H(CD)).status_code == 200           # старая сводка КД не затронута
        assert c.get("/api/finance/meta", headers=H(CD)).status_code == 403
        ov = c.post("/api/admin/access/overrides", headers=H(GD), json={"user_id": me["id"], "group": "finance_section", "level": 1, "comment": "доступ к разделу Финансы"})
        assert ov.status_code == 201, ov.text
        try:
            meta = c.get("/api/finance/meta", headers=H(CD))
            assert meta.status_code == 200 and meta.json()["can_edit"] is False
            assert c.post("/api/finance/transactions", headers=H(CD, f"cd-{RUN}"), json={"direction": "in", "amount": "1", "currency": "RUB",
                                                                                         "booked_at": _iso(datetime.now(timezone.utc))}).status_code == 403
        finally:
            assert c.delete(f"/api/admin/access/overrides/{ov.json()['id']}", headers=H(GD)).status_code == 204
        assert c.get("/api/finance/meta", headers=H(CD)).status_code == 403
