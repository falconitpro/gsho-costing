"""«Финансы», этап 6: очереди сверки и уточнения (FIN-SEC-007, FIN-SEC-008; D-140…D-142)."""
from __future__ import annotations

import os
import tempfile
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

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import NotificationLog, User  # noqa: E402
from scripts.seed import run as seed  # noqa: E402
from tests.test_finance_api import H, _own_bank, _signed_client_contract, CD, FD, LAW, LOG, MGR, RUN  # noqa: E402


def _uid(email: str) -> str:
    with SessionLocal() as db:
        return db.scalar(select(User.id).where(User.email == email))


def test_reconciliation_queues_and_question_cycle():
    seed()
    today = date.today()
    with TestClient(app) as c:
        k = _signed_client_contract(c, 500000, [{"share_pct": 40, "event": "contract", "offset_days": 3, "offset_kind": "working"},
                                                {"share_pct": 60, "event": "contract", "offset_days": 20, "offset_kind": "calendar"}])
        obs = c.get(f"/api/finance/obligations?contract_id={k['id']}", headers=H(FD)).json()["items"]
        o1 = next(o for o in obs if o["source"]["line_no"] == 1)
        # старая отметка оплаты в договоре → запись «требует сверки»
        assert c.post(f"/api/legal/contracts/{k['id']}/facts", headers=H(FD), json={"kind": "pay", "key": "2", "paid_on": today.isoformat()}).status_code == 200
        # счёт без остатка и проведённая операция без разнесения
        ent_id = c.get("/api/legal/contracts", headers=H(LAW)).json()["entities"][0]["id"]
        acct = c.post("/api/finance/accounts", headers=H(FD), json={"legal_entity_id": ent_id, "bank_profile_id": _own_bank(c, ent_id, "RUB"), "title": f"Сверка {RUN}"}).json()
        t = c.post("/api/finance/transactions", headers=H(FD, f"rq-tx-{RUN}"), json={"account_id": acct["id"], "direction": "in", "amount": "77000.00", "currency": "RUB",
                                                                                    "booked_at": datetime.now(timezone.utc).isoformat(), "counterparty_name": "АО Рудник",
                                                                                    "external_ref": f"RQ-{RUN}"}).json()
        assert c.post(f"/api/finance/transactions/{t['id']}/post", headers=H(FD, f"rq-post-{RUN}")).json()["status"] == "posted"

        assert c.get("/api/finance/reconciliation", headers=H(CD)).status_code == 403
        assert c.get("/api/finance/reconciliation?queue=nope", headers=H(FD)).status_code == 422
        r = c.get("/api/finance/reconciliation", headers=H(FD)).json()
        counts = {q["code"]: q["count"] for q in r["queues"]}
        assert counts["unallocated"] >= 1 and counts["manual_marks"] >= 1 and counts["stale_balance"] >= 1
        un = [i for i in r["items"] if i["queue"] == "unallocated" and i["object"]["id"] == t["id"]]
        assert len(un) == 1 and un[0]["amount"] == "77000.00" and un[0]["action"]["url"] == f"/finance?tab=liquidity&tx={t['id']}" and un[0]["can_ask"]
        assert any(i["object"]["id"] == acct["id"] and i["severity"] == "high" for i in r["items"] if i["queue"] == "stale_balance")
        only = c.get("/api/finance/reconciliation?queue=manual_marks", headers=H(FD)).json()
        assert only["items"] and {i["queue"] for i in only["items"]} == {"manual_marks"} and only["queues"] == r["queues"]   # счётчики — по всем очередям

        # --- вопрос ответственному без права на раздел (менеджер)
        mgr = _uid(MGR)
        prev = c.get(f"/api/finance/questions/context?subject_type=obligation&subject_id={o1['id']}", headers=H(FD)).json()
        assert prev["fields"]["Сумма"] == "200 000,00 RUB" and "Договор" in prev["fields"]
        assert c.get(f"/api/finance/questions/context?subject_type=obligation&subject_id={o1['id']}", headers=H(MGR)).status_code == 403
        bad = c.post("/api/finance/questions", headers=H(FD), json={"subject_type": "obligation", "subject_id": "00000000-0000-0000-0000-000000000000", "assignee_id": mgr, "question": "когда оплата?"})
        assert bad.status_code == 404
        assert c.post("/api/finance/questions", headers=H(FD), json={"subject_type": "obligation", "subject_id": o1["id"], "assignee_id": mgr, "question": "?"}).status_code == 422
        q = c.post("/api/finance/questions", headers=H(FD), json={"subject_type": "obligation", "subject_id": o1["id"], "assignee_id": mgr,
                                                                  "question": "<b>Клиент</b> подтвердил дату оплаты?", "due_on": (today + timedelta(days=2)).isoformat()})
        assert q.status_code == 201, q.text
        q = q.json()
        assert q["status"] == "open" and q["subject_url"] == f"/finance?tab=calendar&open={o1['id']}" and q["question"].startswith("<b>")   # хранится как текст
        # «Мои задачи» менеджера: task-like элемент, без сумм и ссылок в раздел
        tasks = c.get("/api/tasks/my", headers=H(MGR)).json()
        fq = [x for x in tasks if x.get("kind") == "finance_question" and x["id"] == f"fq:{q['id']}"]
        assert len(fq) == 1 and fq[0]["url"] == f"/finance/questions/{q['id']}" and not fq[0]["can_close"] and "200 000" not in str(fq[0])
        with SessionLocal() as db:
            n = db.scalars(select(NotificationLog).where(NotificationLog.user_id == mgr, NotificationLog.event == "finance_question")).all()
            assert n and all("200 000" not in x.text for x in n)
        # scoped доступ: менеджер видит свой вопрос и разрешённый контекст, но не список и не финансовые ссылки
        assert c.get("/api/finance/questions", headers=H(MGR)).status_code == 403
        mine = c.get(f"/api/finance/questions/{q['id']}", headers=H(MGR)).json()
        assert mine["context"]["fields"]["Сумма"] == "200 000,00 RUB" and "subject_url" not in mine and "history" not in mine
        assert c.get("/api/finance/questions/my", headers=H(MGR)).json()["items"][0]["id"] == q["id"]
        assert c.get(f"/api/finance/questions/{q['id']}", headers=H(LOG)).status_code == 404                 # чужой — без содержимого
        assert c.post(f"/api/finance/questions/{q['id']}/answer", headers=H(LOG), json={"answer": "да"}).status_code == 404
        assert c.post(f"/api/finance/questions/{q['id']}/answer", headers=H(FD), json={"answer": "да"}).status_code == 404   # отвечает только ответственный
        up = c.post(f"/api/finance/questions/{q['id']}/files", headers=H(MGR), files={"file": ("письмо.pdf", b"%PDF-1.4 ok", "application/pdf")})
        assert up.status_code == 201 and up.json()["files"][0]["name"] == "письмо.pdf"
        fid = up.json()["files"][0]["id"]
        assert c.get(f"/api/finance/questions/{q['id']}/files/{fid}", headers=H(FD)).content == b"%PDF-1.4 ok"
        assert c.get(f"/api/finance/questions/{q['id']}/files/{fid}", headers=H(LOG)).status_code == 404
        a = c.post(f"/api/finance/questions/{q['id']}/answer", headers=H(MGR), json={"answer": "Клиент обещал оплату 20-го"})
        assert a.status_code == 200 and a.json()["status"] == "answered"
        assert not [x for x in c.get("/api/tasks/my", headers=H(MGR)).json() if x["id"] == f"fq:{q['id']}"]
        assert c.post(f"/api/finance/questions/{q['id']}/answer", headers=H(MGR), json={"answer": "ещё"}).status_code == 409

        # проверка ответа: только ФД/ЗГД; отклонение возвращает вопрос ответственному
        assert c.post(f"/api/finance/questions/{q['id']}/review", headers=H(MGR), json={"decision": "verified"}).status_code == 403
        assert c.post(f"/api/finance/questions/{q['id']}/review", headers=H(FD), json={"decision": "rejected"}).status_code == 422
        rj = c.post(f"/api/finance/questions/{q['id']}/review", headers=H(FD), json={"decision": "rejected", "review_comment": "нужна дата в письме"}).json()
        assert rj["status"] == "rejected"
        assert c.get(f"/api/finance/questions/{q['id']}", headers=H(MGR)).json()["review_comment"] == "нужна дата в письме"
        assert [x for x in c.get("/api/tasks/my", headers=H(MGR)).json() if x["id"] == f"fq:{q['id']}"]
        c.post(f"/api/finance/questions/{q['id']}/answer", headers=H(MGR), json={"answer": "Письмо: оплата 20.11"})
        rec_q = [i for i in c.get("/api/finance/reconciliation?queue=questions", headers=H(FD)).json()["items"] if i["object"]["id"] == q["id"]]
        assert rec_q and rec_q[0]["action"]["title"] == "Проверить ответ" and rec_q[0]["responsible"]
        v = c.post(f"/api/finance/questions/{q['id']}/review", headers=H(FD), json={"decision": "verified", "review_comment": "ок"}).json()
        assert v["status"] == "verified" and v["source_update_ref"] == "" and v["source_updated_at"] is None   # без ссылки — не «данные исправлены»
        assert c.post(f"/api/finance/questions/{q['id']}/close", headers=H(MGR), json={"reason": "x"}).status_code == 404
        cl = c.post(f"/api/finance/questions/{q['id']}/close", headers=H(FD), json={"reason": "дата учтена в договоре"}).json()
        assert cl["status"] == "closed" and [h["action"] for h in cl["history"]] == ["create", "answer", "rejected", "answer", "verified", "close"]

        # вопрос по признанию расхода: сумма расхода (себестоимость) ответственному не передаётся
        from tests.test_finance_economy import _deal
        did, _, _ = _deal(c)
        ev = c.post(f"/api/deals/{did}/finance/recognition-events", headers=H(FD, f"rq-ev-{RUN}"), json={
            "kind": "expense", "cost_item_code": "finance", "amount": "12345.00", "basis_type": "business_event", "comment": "проценты банка"}).json()
        ctx = c.get(f"/api/finance/questions/context?subject_type=recognition&subject_id={ev['id']}", headers=H(FD)).json()
        assert "Сумма" not in ctx["fields"] and "12 345" not in str(ctx) and "12345" not in str(ctx)
