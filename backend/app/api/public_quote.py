"""Ответ логистической компании по ссылке без входа в CRM (D-107). Только эти два маршрута доступны без входа."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Deal, FeedEntry, User
from ..services import logistics_workspace as lw
from ..services import quote_links as ql
from ..services.deal_service import audit, notify

router = APIRouter(prefix="/api/public/quote", tags=["public"])
CURRENCIES = ("RUB", "CNY", "USD", "EUR")
_HITS: dict[str, deque] = defaultdict(deque)
LIMIT, WINDOW = 60, 600            # не больше 60 обращений с одного адреса за 10 минут


def _throttle(request: Request) -> None:
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip()
    q, now = _HITS[ip], time.monotonic()
    while q and now - q[0] > WINDOW:
        q.popleft()
    if len(q) >= LIMIT:
        raise HTTPException(429, "Слишком много обращений. Повторите через несколько минут")
    q.append(now)
    if len(_HITS) > 5000:
        _HITS.clear()


def _link(db: Session, token: str, request: Request):
    _throttle(request)
    link, why = ql.find(db, token)
    if link is None:
        raise HTTPException(404, why)
    if why:
        raise HTTPException(410, why)
    return link


@router.get("/{token}")
def view(token: str, request: Request, db: Session = Depends(get_db)):
    return ql.public_view(db, _link(db, token, request))


class LineIn(BaseModel):
    scope: str
    amount: float = Field(gt=0, lt=1e11)
    currency: str = "RUB"
    transit_days: Optional[int] = Field(default=None, ge=1, le=365)


class AnswerIn(BaseModel):
    carrier: str = Field(min_length=2, max_length=255)
    contact: str = Field(default="", max_length=255)
    valid_until: str = ""
    comment: str = Field(default="", max_length=2000)
    lines: list[LineIn] = Field(min_length=1, max_length=6)


@router.post("/{token}")
def answer(token: str, body: AnswerIn, request: Request, db: Session = Depends(get_db)):
    link = _link(db, token, request)
    if link.answers >= ql.MAX_ANSWERS:
        raise HTTPException(429, "По этой ссылке ответ уже изменяли много раз — пришлите, пожалуйста, уточнение письмом")
    scopes = [ln.scope for ln in body.lines]
    if any(sc not in lw.SERVICE_TITLES for sc in scopes) or len(set(scopes)) != len(scopes):
        raise HTTPException(422, "Укажите стоимость по каждой услуге один раз")
    if any(ln.currency.upper() not in CURRENCIES for ln in body.lines):
        raise HTTPException(422, f"Валюта: {', '.join(CURRENCIES)}")
    valid = ""
    if body.valid_until:
        try:
            valid = date.fromisoformat(body.valid_until[:10]).isoformat()
        except ValueError:
            raise HTTPException(422, "Срок действия предложения — дата")
    deal = db.get(Deal, link.deal_id)
    old = [x for x in (link.quote_ids or "").split(",") if x]
    for qid in old:                          # повторная отправка по ссылке заменяет прежний ответ
        lw.remove_quote(deal, qid)
    carrier, contact, comment = body.carrier.strip(), body.contact.strip(), body.comment.strip()
    full = (comment + (f"\nКонтакт: {contact}" if contact else "") + f"\nОтвет по ссылке ({link.email}), запрос {link.request_no}").strip()[:2000]
    rows = [lw.add_quote(deal, None, {"carrier": carrier, "amount": round(ln.amount, 2), "currency": ln.currency.upper(), "transit_days": ln.transit_days,
                                      "valid_until": valid, "scope": ln.scope, "comment": full, "comment_public": comment, "contact": contact,
                                      "storage_path": "", "source": "link", "email": link.email, "request_no": link.request_no}, None, "")
            for ln in body.lines]
    link.quote_ids, link.answers, link.answered_at = ",".join(r["id"] for r in rows), link.answers + 1, datetime.now(timezone.utc)
    summary = "; ".join(f"{lw.SERVICE_TITLES[r['scope']].split(' (')[0]}: {r['amount']:,.0f} {r['currency']}".replace(",", " ") for r in rows)
    title = f"Ответ логистической компании по ссылке{' (изменён)' if old else ''}: {carrier}"
    db.add(FeedEntry(deal_id=deal.id, kind="system", subject=title, body=f"{summary}\n{full}"))
    audit(db, None, deal.id, "deal", deal.id, "logistics_quote_link", after={"carrier": carrier, "lines": [r["id"] for r in rows], "link_id": link.id, "replaced": bool(old)})
    if link.created_by and db.get(User, link.created_by) is not None:
        notify(db, deal, link.created_by, "logistics_quote", None, f"{deal.number}: {title} — {summary}")
    db.commit()
    return {"ok": True, "replaced": bool(old)}
