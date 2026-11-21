"""D-132: вход нового сотрудника и восстановление пароля по одноразовой ссылке «Задать пароль».

Пароль по почте не отправляется: в письме — ссылка на 24 часа, одноразовая; в базе хранится только SHA-256 токена.
Ссылка меняет только пароль: код из приложения (если включён) по-прежнему спрашивается при входе.
"""
from __future__ import annotations

import hashlib
import html
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..models import PasswordLink, User

log = logging.getLogger(__name__)
TTL = timedelta(hours=24)
PURPOSES = {"invite": "приглашение", "reset": "сброс администратором", "forgot": "«Забыли пароль?»"}
FORGOT_PER_HOUR = 3
_forgot_hits: dict[str, list[float]] = {}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def base_url(s: Settings) -> str:
    return (s.public_url or (s.cors_origins[0] if s.cors_origins else "")).rstrip("/")


def mail_ready(s: Settings) -> bool:
    """Письмо реально уйдёт: почта подключена (не консоль) и для SMTP заданы логин и пароль приложения."""
    if s.mail_provider == "console":
        return False
    if s.mail_provider in ("smtp", "yandex_smtp"):
        return bool(s.smtp_user and s.smtp_password)
    return True


def throttled(key: str) -> bool:
    """Не больше 3 запросов «Забыли пароль?» в час на адрес и на IP."""
    now = time.time()
    hits = [t for t in _forgot_hits.get(key, []) if now - t < 3600]
    _forgot_hits[key] = hits
    if len(hits) >= FORGOT_PER_HOUR:
        return True
    hits.append(now)
    return False


def issue(db: Session, user: User, purpose: str, to: str, actor_id: Optional[str]) -> tuple[PasswordLink, str]:
    """Новая ссылка; прежние неиспользованные ссылки сотрудника перестают действовать."""
    now = datetime.now(timezone.utc)
    db.execute(update(PasswordLink).where(PasswordLink.user_id == user.id, PasswordLink.used_at.is_(None), PasswordLink.revoked_at.is_(None))
               .values(revoked_at=now))
    token = secrets.token_urlsafe(32)
    row = PasswordLink(user_id=user.id, token_hash=_hash(token), purpose=purpose, sent_to=to, created_by=actor_id, expires_at=now + TTL)
    db.add(row)
    db.flush()
    return row, token


def find(db: Session, token: str) -> Optional[PasswordLink]:
    if not token or len(token) > 128:
        return None
    row = db.scalar(select(PasswordLink).where(PasswordLink.token_hash == _hash(token)))
    if row is None or row.used_at or row.revoked_at:
        return None
    exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    return row if exp > datetime.now(timezone.utc) else None


def link_url(s: Settings, token: str) -> str:
    return f"{base_url(s)}/set-password?t={token}"


def send(s: Settings, user: User, to: str, url: str, purpose: str) -> tuple[bool, str]:
    """Отправить письмо со ссылкой. (True, "") — ушло; (False, причина) — не ушло, ссылку покажут администратору."""
    if not mail_ready(s):
        return False, "почта системы не подключена"
    from ..adapters import mail
    from ..adapters.mail import OutgoingMail
    head = "Для вас создана учётная запись" if purpose == "invite" else "Запрошена смена пароля"
    name = html.escape(user.display_name or "")
    body = (f"<p>Здравствуйте, {name}!</p><p>{head} в системе «Расчёт себестоимости ГШО». Логин: <b>{html.escape(user.email)}</b>.</p>"
            f"<p><a href=\"{html.escape(url)}\">Задать пароль</a> — ссылка действует 24 часа и только один раз.</p>"
            f"<p>Если вы не запрашивали смену пароля, просто не открывайте ссылку и сообщите администратору — текущий пароль останется прежним.</p>")
    try:
        mail(s).send(OutgoingMail(to=[to], subject=("Доступ в систему: задайте пароль" if purpose == "invite" else "Смена пароля"), body_html=body))
        return True, ""
    except Exception as e:   # noqa: BLE001 — почтовый сервер недоступен: ссылка покажется администратору
        log.warning("password link mail failed: %s", type(e).__name__)
        return False, "почтовый сервер не принял письмо"


def notify_changed(s: Settings, user: User) -> None:
    """Письмо «Ваш пароль изменён» — сотрудник узнает, если пароль сменил не он."""
    if not mail_ready(s) or not user.email:
        return
    from ..adapters import mail
    from ..adapters.mail import OutgoingMail
    try:
        mail(s).send(OutgoingMail(to=[user.email], subject="Пароль изменён",
                                  body_html=f"<p>Пароль вашей учётной записи {html.escape(user.email)} изменён {datetime.now():%d.%m.%Y %H:%M}. "
                                            f"Если это были не вы — срочно сообщите администратору.</p>"))
    except Exception:   # noqa: BLE001
        pass
