"""Запись событий безопасности (docs/operations/02 §5, 04 §7.2); каждое событие проверяется правилами предупреждений (04 §7.3).

Без паролей, TOTP- и recovery-кодов, токенов, cookie, подписанных URL и содержимого документов: metadata проходит фильтр
по ключам и значениям. IP хранится префиксом (/24 для IPv4, /48 для IPv6).
"""
from __future__ import annotations

import ipaddress
import re
import uuid
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from ..models import SecurityEvent

_BAD_KEYS = re.compile(r"(?i)(pass|secret|token|cookie|authorization|code|otp|totp|recovery|session_key|signature|key$)")
_BAD_VALUES = [re.compile(r"https?://\S*[?&](X-Amz-|Signature=|token=)\S*"), re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}")]
_SAFE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


def ip_prefix(request: Optional[Request]) -> str:
    host = (request.client.host if request is not None and request.client else "") or ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return ""
    net = ipaddress.ip_network(f"{ip}/{24 if ip.version == 4 else 48}", strict=False)
    return str(net)


def request_id(request: Optional[Request]) -> str:
    if request is None:
        return str(uuid.uuid4())
    rid = getattr(request.state, "request_id", None)
    if not rid:
        hdr = request.headers.get("x-request-id", "")
        rid = hdr if re.fullmatch(r"[0-9a-fA-F-]{8,36}", hdr or "") else str(uuid.uuid4())
        request.state.request_id = rid
    return rid


def user_agent_short(request: Optional[Request]) -> str:
    ua = (request.headers.get("user-agent", "") if request is not None else "") or ""
    browser = next((b for b in ("Edg", "YaBrowser", "Firefox", "Chrome", "Safari") if b in ua), "")
    os_ = next((o for o, k in (("Windows", "Windows"), ("macOS", "Mac OS"), ("iOS", "iPhone"), ("Android", "Android"), ("Linux", "Linux")) if k in ua), "")
    label = {"Edg": "Edge", "YaBrowser": "Яндекс Браузер"}.get(browser, browser)
    return " · ".join(x for x in (label, os_) if x)[:120] or "неизвестное устройство"


def sanitize_meta(meta: Optional[dict]) -> dict:
    out: dict[str, Any] = {}
    for k, v in (meta or {}).items():
        if not isinstance(k, str) or not _SAFE_KEY.match(k) or _BAD_KEYS.search(k):
            continue
        if isinstance(v, (bool, int, float)) or v is None:
            out[k] = v
        elif isinstance(v, str):
            s = v[:200]
            for rx in _BAD_VALUES:
                s = rx.sub("[скрыто]", s)
            out[k] = s
        elif isinstance(v, (list, tuple)):
            out[k] = [x if isinstance(x, (int, float, bool)) else str(x)[:80] for x in list(v)[:20]]
        elif isinstance(v, dict):
            out[k] = sanitize_meta(v)
    return out


def record(db: Session, event_type: str, action: str, *, result: str = "success", actor_id: Optional[str] = None,
           subject_id: Optional[str] = None, request: Optional[Request] = None, session_id: Optional[str] = None,
           entity_type: str = "", entity_id: str = "", deal_id: Optional[str] = None, fmt: str = "",
           row_count: Optional[int] = None, byte_size: Optional[int] = None, meta: Optional[dict] = None) -> SecurityEvent:
    ev = SecurityEvent(id=str(uuid.uuid4()), event_type=event_type, action=action, result=result, actor_user_id=actor_id, subject_user_id=subject_id,
                       request_id=request_id(request), session_id=session_id, ip_prefix=ip_prefix(request),
                       entity_type=entity_type, entity_id=str(entity_id or "")[:128], deal_id=deal_id, format=fmt,
                       row_count=row_count, byte_size=byte_size, meta=sanitize_meta(meta))
    from .security_alerts import evaluate   # правила предупреждений (04 §7.3) — в той же транзакции, до вставки (журнал неизменяем)
    ev.alert_id = evaluate(db, ev)
    db.add(ev)
    return ev
