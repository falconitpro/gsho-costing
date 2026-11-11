"""Подготовка к qa_briefing_ui.py: у менеджера синтетическая сделка и три задачи — просроченная, на сегодня, на завтра.
    cd backend && DATABASE_URL=... APP_ENV=dev PYTHONPATH=. python ../docs/operations/qa_briefing_prep.py"""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Task, User

H = {"X-Dev-User": "manager@example.com"}
with TestClient(app) as c:
    cl = c.get("/api/refs/clients", headers=H).json()[0]
    deal = c.post("/api/deals", headers=H, json={"client_id": cl["id"], "site": "карьер Север"}).json()
with SessionLocal() as db:
    u = db.scalar(select(User).where(User.email == "manager@example.com"))
    today = (datetime.now(timezone.utc) + timedelta(hours=3)).date()
    for title, dd in (("Отправить КП клиенту «Север»", -2), ("Проверить RFQ от завода", 0), ("Согласовать логистику", 1)):
        db.add(Task(deal_id=deal["id"], role="manager", assignee_id=u.id, title=title, due=today + timedelta(days=dd)))
    db.commit()
print("сделка", deal["number"])
