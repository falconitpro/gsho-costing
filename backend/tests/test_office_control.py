"""D-133/D-134: напоминания о сроках и эскалация, панель «на контроле», выгрузка журнала, ИИ для писем (модель подменена)."""
import io
from datetime import date, datetime, timedelta, timezone

from tests.test_api_flow import client, _ok, H, MGR, GD  # noqa: F401

CLERK = "office@example.com"
PDF = ("скан.pdf", b"%PDF-1.4 test", "application/pdf")


def _m(client):
    return _ok(client.get("/api/office/meta", headers=H(CLERK)))


def _workday(d: date, back: bool = False) -> date:
    from app.calc.calendar import DEFAULT_CALENDAR as C
    while not C.is_workday(d):
        d += timedelta(days=-1 if back else 1)
    return d


def test_due_reminders_escalation_control_export(client):
    from app.calc.calendar import DEFAULT_CALENDAR as C
    from app.db import SessionLocal
    from app.models import NotificationLog as Notification, OfficeDoc
    from app.services import office_watch
    m = _m(client)
    ru = next(e for e in m["entities"] if e["code"] == "ru")
    mgr = next(u["id"] for u in m["users"] if u["name"].startswith("Бочаров"))
    gd = next(u["id"] for u in m["users"] if u["name"].startswith("Добров"))
    d = _ok(client.post("/api/office/docs", headers=H(CLERK), json={"entity_id": ru["id"], "kind": "in", "letter_type": "request", "channel": "email",
                                                                    "subject": "Запрос сроков — контроль"}), 201)
    _ok(client.post(f"/api/office/docs/{d['id']}/files", headers=H(CLERK), files={"file": PDF}, data={"kind": "scan"}), 201)
    _ok(client.post(f"/api/office/docs/{d['id']}/review", headers=H(CLERK), json={"reviewer_id": gd}))
    _ok(client.post(f"/api/office/docs/{d['id']}/resolution", headers=H(GD), json={"kind": "execute", "executor_id": mgr}))
    base = _workday(date.today() + timedelta(days=10))
    due = C.add_working_days(base, 1)
    with SessionLocal() as db:
        db.get(OfficeDoc, d["id"]).due_on = due
        db.commit()

    def run(day: date, hour: int = 10) -> int:
        with SessionLocal() as db:
            n = office_watch.check(db, datetime(day.year, day.month, day.day, hour, tzinfo=timezone(timedelta(hours=3))))
            db.commit()
            return n

    def notes(uid):
        with SessionLocal() as db:
            return [x.text for x in db.query(Notification).filter(Notification.user_id == uid, Notification.event == "office_due", Notification.channel == "app").all()]

    assert run(base, hour=7) == 0                       # до 9:00 — не беспокоим
    assert run(base) >= 1 and any("завтра срок" in t for t in notes(mgr))
    assert run(base) == 0                               # повторно не шлём
    assert run(due) >= 1 and any("сегодня срок" in t for t in notes(mgr))
    late1 = C.add_working_days(due, 1)
    assert run(late1) >= 2
    assert any("просрочено на 1" in t for t in notes(gd)) and any("просрочено на 1" in t for t in notes(mgr))
    assert run(C.add_working_days(due, 2)) == 0          # повтор — раз в 3 рабочих дня
    assert run(C.add_working_days(due, 4)) >= 1
    # панель «на контроле»
    from app.services.office_watch import control
    with SessionLocal() as db:
        c = control(db, today=late1)
    me = next(r for r in c["items"] if r["user_id"] == mgr)
    assert me["overdue"] >= 1 and any(x["id"] == d["id"] for x in me["late_docs"])
    assert _ok(client.get("/api/office/control", headers=H(CLERK)))["total"]["open"] >= 1
    assert client.get("/api/office/control", headers=H(MGR)).status_code == 403
    # выгрузка журнала
    r = client.get("/api/office/export", headers=H(CLERK), params={"kind": "in"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    from openpyxl import load_workbook
    ws = load_workbook(io.BytesIO(r.content)).active
    assert ws["A3"].value == "№" and any(row[10] == "Запрос сроков — контроль" for row in ws.iter_rows(min_row=4, values_only=True))
    assert client.get("/api/office/export", headers=H(MGR)).status_code == 403


def test_office_ai_mocked(client, monkeypatch):
    from app.adapters.llm import LLMResult
    from app.services import office_ai
    m = _m(client)
    ru = next(e for e in m["entities"] if e["code"] == "ru")
    d = _ok(client.post("/api/office/docs", headers=H(CLERK), json={"entity_id": ru["id"], "kind": "in", "letter_type": "other", "channel": "edo",
                                                                    "subject": "Претензия по ковшу"}), 201)
    # без ключа — понятный отказ
    monkeypatch.delenv("OFFICE_AI", raising=False)
    assert client.post(f"/api/office/docs/{d['id']}/ai", headers=H(CLERK), json={"action": "analyze", "text": "x" * 50}).status_code == 409
    monkeypatch.setenv("OFFICE_AI", "on")
    monkeypatch.setenv("LLM_YANDEX_API_KEY", "test")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "b1gtest")
    letter = "Требуем заменить ковш по договору № 45-П от 01.02.2026 и возместить 1 250 000 руб. Ответ просим дать до 05.12.2026."
    answers = {
        "analyze": '{"summary": "Требуют заменить ковш и возместить 1 250 000 руб.", "letter_type": "claim", "due_on": "2026-12-05", '
                   '"due_quote": "до 05.12.2026", "requirements": ["замена ковша"], "amounts": ["1 250 000 руб.", "999 999 руб."], '
                   '"dates": [], "refs": ["45-П"], "suggested_role": "lawyer", "needs_reply": true}',
    }
    seen = {}

    def fake(self, system, user, temperature=0.1, max_tokens=4000):
        seen["model"] = self.model
        if "Входящее письмо" in user:
            return LLMResult(answers["analyze"], "yandex", self.model, {})
        if "Translate" in user:
            return LLMResult("We demand replacement of the bucket under contract No. 45-P and 1,250,000 RUB.", "yandex", self.model, {})
        return LLMResult("Просим заменить ковш по договору № 45-П от 01.02.2026.", "yandex", self.model, {})
    monkeypatch.setattr(office_ai._YandexOpenAI, "complete", fake)
    r = _ok(client.post(f"/api/office/docs/{d['id']}/ai", headers=H(CLERK), json={"action": "analyze", "text": letter}))
    assert r["letter_type"] == "claim" and r["due_on"] == "2026-12-05"
    assert r["amounts"] == ["1 250 000 руб."]                     # выдуманная сумма отброшена
    assert seen["model"] == "gpt://b1gtest/yandexgpt/rc"
    # правка: потерянные числа — предупреждение
    o = _ok(client.post("/api/office/docs", headers=H(CLERK), json={"entity_id": ru["id"], "kind": "out", "subject": "Ответ", "body": letter}), 201)
    e = _ok(client.post(f"/api/office/docs/{o['id']}/ai", headers=H(CLERK), json={"action": "edit", "mode": "softer"}))
    assert e["after"].startswith("Просим") and any("пропали числа" in w for w in e["warnings"])
    t = _ok(client.post(f"/api/office/docs/{o['id']}/ai", headers=H(CLERK), json={"action": "translate", "lang": "zh"}))
    assert "qwen3-235b" in seen["model"] and t["warnings"]
    # конфиденциальное — в ИИ не уходит; документ не изменился
    _ok(client.patch(f"/api/office/docs/{o['id']}", headers=H(CLERK), json={"confidential": True}))
    assert client.post(f"/api/office/docs/{o['id']}/ai", headers=H(CLERK), json={"action": "edit"}).status_code == 409
    assert _ok(client.get(f"/api/office/docs/{o['id']}", headers=H(CLERK)))["body"] == letter
