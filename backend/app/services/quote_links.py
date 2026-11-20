"""Ответ логистической компании по ссылке (D-107).

Логист отправляет запрос на расчёт доставки — на каждый адрес получателя создаётся своя ссылка. По ней компания без входа
в CRM видит то же, что в Excel-запросе (номер запроса, адреса, позиции, услуги, срок ответа), и вводит стоимость, валюту,
срок и условия. Ответ попадает в «Ответы логистических компаний» сделки, логисту — уведомление.

Безопасность: в базе хранится только SHA-256 ссылки (192 бита случайности); ссылка действует 14 дней после срока ответа
и закрывается, когда логистика по сделке завершена; повторная отправка заменяет прежний ответ (не больше 10 раз);
файлы по ссылке не принимаются (они пришли бы мимо проверки антивирусом для вошедших сотрудников) — файл компания
присылает письмом.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Deal, QuoteLink

VALID_AFTER_ANSWER_DAYS = 14
MAX_ANSWERS = 10


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def base_url() -> str:
    from ..core.config import get_settings
    return ((get_settings().cors_origins or [""])[0] or "").rstrip("/")


def create(db: Session, deal: Deal, req: dict, emails: list[str], user_id: str) -> list[dict]:
    try:
        answer_by = date.fromisoformat(req.get("answer_by") or "")
    except ValueError:
        answer_by = date.today() + timedelta(days=3)
    expires = datetime.combine(answer_by + timedelta(days=VALID_AFTER_ANSWER_DAYS), datetime.max.time(), tzinfo=timezone.utc)
    out = []
    for e in emails:
        token = secrets.token_urlsafe(24)
        db.add(QuoteLink(token_hash=_hash(token), deal_id=deal.id, request_id=req.get("id") or "", request_no=req.get("no") or "",
                         email=e[:255], created_by=user_id, expires_at=expires))
        out.append({"email": e, "url": f"{base_url()}/q/{token}", "expires": expires.date().isoformat()})
    return out


def find(db: Session, token: str) -> tuple[Optional[QuoteLink], str]:
    """Ссылка и причина, если она не действует ("" — действует)."""
    if not token or len(token) > 64:
        return None, "Ссылка не найдена"
    link = db.scalar(select(QuoteLink).where(QuoteLink.token_hash == _hash(token)))
    if link is None:
        return None, "Ссылка не найдена"
    deal = db.get(Deal, link.deal_id)
    if link.revoked_at is not None or deal is None or deal.deleted_at is not None or deal.archived_at is not None:
        return link, "Запрос отозван"
    if datetime.now(timezone.utc) > link.expires_at:
        return link, "Срок ответа по ссылке истёк — ответьте, пожалуйста, письмом"
    if deal.logistics_done:
        return link, "Расчёт доставки по этому запросу уже завершён — спасибо"
    return link, ""


def public_view(db: Session, link: QuoteLink) -> dict:
    """То же, что в Excel-запросе, без цен оборудования и без данных клиента сверх адреса доставки."""
    from . import logistics_workspace as lw
    deal = db.get(Deal, link.deal_id)
    info = deal.logistics_info or {}
    req = next((r for r in info.get("requests") or [] if r.get("id") == link.request_id), {})
    data = lw.request_data(db, deal)
    ids = set(filter(None, (link.quote_ids or "").split(",")))
    mine = [q for q in info.get("quotes") or [] if q.get("id") in ids]
    pickup = [{"factory": x.get("factory") or "", "address": x.get("address") or ""} for x in data["pickup"]]
    positions = [{"machine_type": x.get("machine_type") or "", "designation": x.get("designation") or "", "qty": x.get("qty") or 1,
                  "basis": x.get("basis") or "", "delivery_point": x.get("delivery_point") or "",
                  "rows": [{"name": r["name"], "unit": r["unit"], "value": r["value"] or r["requirement"]} for r in x.get("rows") or [] if r["value"] or r["requirement"]]}
                 for x in data["positions"]]
    return {"no": link.request_no or data["no"], "answer_by": req.get("answer_by") or data["answer_by"], "pickup": pickup,
            "delivery": data["delivery"], "readiness": data["readiness"], "basis": data["basis"],
            "services": [{"code": c, "title": lw.SERVICE_TITLES[c]} for c in (req.get("services") or data["service_codes"]) if c in lw.SERVICE_TITLES],
            "positions": positions, "currencies": ["RUB", "CNY", "USD", "EUR"],
            "answer": {"carrier": mine[0].get("carrier"), "valid_until": mine[0].get("valid_until"), "comment": mine[0].get("comment_public") or "",
                       "contact": mine[0].get("contact") or "",
                       "lines": [{k: q.get(k) for k in ("scope", "amount", "currency", "transit_days")} for q in mine]} if mine else None}
