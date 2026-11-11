"""Помощник CRM (docs/operations/06 §9, 09 «Assistant»): свои диалоги, вопросы, отзывы, техническая статистика без содержимого."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.config import get_settings
from ..db import get_db
from ..models import AiConversation, AiFeedback, AiMessage, AiToolAudit, AiUsageDaily
from ..services import assistant as ai

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


def _user(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.can("assistant_use", 1):
        raise HTTPException(403, "Помощник CRM вам недоступен")
    return user


def _conv(db: Session, user: CurrentUser, cid: str) -> AiConversation:
    c = db.get(AiConversation, cid)
    if c is None or c.owner_id != user.id or c.deleted_at is not None:
        raise HTTPException(404, "Диалог не найден")
    return c


def _msg_dto(m: AiMessage) -> dict:
    return {"id": m.id, "role": m.role, "mode": m.mode, "text": m.content, "status": m.status, "provider": m.provider, "sources": m.sources or [],
            "warnings": m.warnings or [], "facts": m.facts or [], "at": m.created_at.isoformat() if m.created_at else None, "purged": m.purged_at is not None}


def _conv_dto(c: AiConversation) -> dict:
    iso = lambda d: d.isoformat() if d else None   # noqa: E731
    return {"id": c.id, "title": c.title or "Диалог", "default_mode": c.default_mode, "created_at": iso(c.created_at), "last_message_at": iso(c.last_message_at)}


@router.get("/status")
def status(user: CurrentUser = Depends(_user)):
    s = get_settings()
    st = ai.provider_state(s)
    modes = {"crm": True, "help": True, "general": st["configured"] and st["external_context_allowed"]}
    return {"modes": modes, "mode_titles": ai.MODES, **st, "retention_days": s.assistant_retention_days, "daily_limit": s.assistant_daily_limit,
            "minute_limit": s.assistant_minute_limit, "general_off_text": ai.GENERAL_OFF, "max_text": ai.MAX_TEXT,
            "notice": ("Помощник отвечает только по данным, которые доступны вам в CRM, и по справке. "
                       + ("Внешняя модель не используется: ответы собираются из данных CRM по шаблону. " if not st["external_context_allowed"]
                          else "Для переформулирования используется внешняя модель — ей уходит только готовый текст ответа, без истории и страницы. ")
                       + f"История хранится {s.assistant_retention_days} дн. Не вводите пароли, коды и ключи.")}


@router.get("/conversations")
def conversations(db: Session = Depends(get_db), user: CurrentUser = Depends(_user)):
    rows = db.scalars(select(AiConversation).where(AiConversation.owner_id == user.id, AiConversation.deleted_at.is_(None))
                      .order_by(AiConversation.last_message_at.desc().nulls_last(), AiConversation.created_at.desc()).limit(50)).all()
    return {"items": [_conv_dto(c) for c in rows]}


class ConvIn(BaseModel):
    mode: str = "crm"


@router.post("/conversations", status_code=201)
def create_conversation(body: ConvIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_user)):
    c = AiConversation(owner_id=user.id, default_mode=body.mode if body.mode in ai.MODES else "crm")
    db.add(c)
    db.commit()
    return _conv_dto(c)


@router.get("/conversations/{cid}")
def conversation(cid: str, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db), user: CurrentUser = Depends(_user)):
    c = _conv(db, user, cid)
    msgs = db.scalars(select(AiMessage).where(AiMessage.conversation_id == c.id).order_by(AiMessage.created_at.desc(), AiMessage.id).limit(limit)).all()
    return {**_conv_dto(c), "messages": [_msg_dto(m) for m in reversed(msgs)]}


@router.delete("/conversations/{cid}", status_code=204)
def delete_conversation(cid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_user)):
    c = _conv(db, user, cid)
    c.deleted_at = datetime.now(timezone.utc)
    db.commit()     # содержимое стирается фоновой очисткой (purge), из выдачи диалог исчезает сразу


class MessageIn(BaseModel):
    conversation_id: Optional[str] = None
    mode: str = "crm"
    text: str
    client_request_id: Optional[str] = None


@router.post("/messages", status_code=201)
def message(body: MessageIn, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(_user)):
    s = get_settings()
    q = (body.text or "").strip()
    if not q:
        raise HTTPException(400, "Напишите вопрос")
    if len(q) > ai.MAX_TEXT:
        raise HTTPException(400, f"Вопрос — не длиннее {ai.MAX_TEXT} символов")
    if body.mode not in ai.MODES:
        raise HTTPException(400, "Неизвестный режим")
    crid = re.sub(r"[^\w\-]", "", body.client_request_id or "")[:64] or None
    c = _conv(db, user, body.conversation_id) if body.conversation_id else None
    if c is not None and crid:   # повтор того же запроса (сеть оборвалась) — прежний ответ, лимит второй раз не расходуется
        prev = db.scalar(select(AiMessage).where(AiMessage.conversation_id == c.id, AiMessage.client_request_id == crid))
        if prev is not None:
            ans = db.scalar(select(AiMessage).where(AiMessage.reply_to_id == prev.id))
            return {"conversation_id": c.id, "question": _msg_dto(prev), "answer": _msg_dto(ans) if ans else None, "repeated": True}
    ai.take_quota(db, user.id, s)
    if c is None:
        c = AiConversation(owner_id=user.id, default_mode=body.mode, title=q[:60])
        db.add(c)
        db.flush()
    elif not c.title:
        c.title = q[:60]
    from ..services.security_events import request_id
    rid = request_id(request)
    qm = AiMessage(conversation_id=c.id, role="user", mode=body.mode, content=q, request_id=rid, client_request_id=crid)
    db.add(qm)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Этот вопрос уже отправлен — обновите диалог")
    token = ai.CURRENT.set((c.id, qm.id))
    try:
        ans, provider, fb = _answer(db, user, body.mode, q, s)
    finally:
        ai.CURRENT.reset(token)
    am = AiMessage(conversation_id=c.id, role="assistant", mode=body.mode, content=ans.text, status="fallback" if fb and ans.status == "fallback" else ans.status,
                   provider=provider, sources=[x.dto() for x in ans.sources], warnings=ans.warnings, facts=ans.facts, request_id=rid, reply_to_id=qm.id)
    db.add(am)
    c.last_message_at = datetime.now(timezone.utc)
    db.commit()
    return {"conversation_id": c.id, "question": _msg_dto(qm), "answer": {**_msg_dto(am), "fallback": fb, "generated_at": am.created_at.isoformat() if am.created_at else None},
            "repeated": False}


def _answer(db: Session, user: CurrentUser, mode: str, q: str, s):
    if mode == "general":
        st = ai.provider_state(s)
        if not (st["configured"] and st["external_context_allowed"]):
            ans, provider, fb = ai.Answer(ai.GENERAL_OFF, status="fallback", tool="general_off"), "none", True
        else:   # общий режим: модели уходит только сам вопрос — без страницы, истории CRM и результатов инструментов (AI-09)
            from ..adapters.llm import make_llm
            try:
                r = make_llm(s.assistant_provider, s.assistant_model).complete("Ты помощник сотрудника. Отвечай кратко по-русски.", q, max_tokens=800)
                ans, provider, fb = ai.Answer(r.text.strip() or ai.GENERAL_OFF), s.assistant_provider, False
            except Exception:   # noqa: BLE001
                ans, provider, fb = ai.Answer("Внешняя модель сейчас недоступна — попробуйте позже. Вопросы по CRM и справка работают.", status="fallback"), "none", True
        return ans, provider, fb
    base = ai.answer_help(db, user, q) if mode == "help" else ai.answer_crm(db, user, q)
    return ai.rewrite(base, s)


class FeedbackIn(BaseModel):
    rating: str
    comment: str = ""


@router.post("/messages/{mid}/feedback")
def feedback(mid: str, body: FeedbackIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_user)):
    m = db.get(AiMessage, mid)
    c = db.get(AiConversation, m.conversation_id) if m else None
    if m is None or c is None or c.owner_id != user.id or c.deleted_at is not None or m.role != "assistant":
        raise HTTPException(404, "Сообщение не найдено")
    if body.rating not in ("positive", "negative"):
        raise HTTPException(400, "Оценка: positive или negative")
    f = db.scalar(select(AiFeedback).where(AiFeedback.message_id == mid, AiFeedback.user_id == user.id))
    if f is None:
        f = AiFeedback(message_id=mid, user_id=user.id, rating=body.rating)
        db.add(f)
    f.rating, f.comment = body.rating, (body.comment or "")[:1000]
    db.commit()
    return {"ok": True, "rating": f.rating}


@router.get("/admin/metrics")
def metrics(days: int = Query(7, ge=1, le=90), db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Только счётчики (assistant_admin): запросы, инструменты, отказы, оценки — без текстов вопросов и ответов."""
    if not user.can("assistant_admin", 1):
        raise HTTPException(403, "Статистика помощника доступна администратору помощника")
    from datetime import timedelta
    since = datetime.now(timezone.utc) - timedelta(days=days)
    tools = dict(db.execute(select(AiToolAudit.tool_name, func.count()).where(AiToolAudit.created_at >= since).group_by(AiToolAudit.tool_name)).all())
    denied = db.scalar(select(func.count()).select_from(AiToolAudit).where(AiToolAudit.created_at >= since, AiToolAudit.result_status == "denied")) or 0
    req = db.scalar(select(func.coalesce(func.sum(AiUsageDaily.request_count), 0)).where(AiUsageDaily.usage_date >= since.date())) or 0
    users = db.scalar(select(func.count(func.distinct(AiUsageDaily.user_id))).where(AiUsageDaily.usage_date >= since.date())) or 0
    fb = dict(db.execute(select(AiFeedback.rating, func.count()).where(AiFeedback.created_at >= since).group_by(AiFeedback.rating)).all())
    return {"days": days, "requests": int(req), "users": int(users), "tools": {k: int(v) for k, v in tools.items()}, "denied": int(denied),
            "feedback": {k: int(v) for k, v in fb.items()}, **ai.provider_state(get_settings())}
