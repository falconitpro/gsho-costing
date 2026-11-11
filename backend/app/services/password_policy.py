"""Единая проверка пароля (docs/operations/04 §4): создание, смена, сброс администратором.

12–128 символов, буква и цифра, не из списка распространённых, не производное от логина/имени, не текущий и не один из 5 прежних.
Ошибки понятны и не раскрывают чужие учётные записи.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PasswordHistory, User

MIN_LEN, MAX_LEN, HISTORY = 12, 128, 5

# Короткий локальный список самых распространённых паролей и их «усилений» (после 12+ символов и буква+цифра остаются такие)
COMMON = {
    "password1234", "password12345", "password123456", "qwerty123456", "qwertyuiop12", "qwerty1234567", "1234567890qw", "1q2w3e4r5t6y",
    "1qaz2wsx3edc", "zaq12wsxcde3", "qazwsx123456", "abc123456789", "abcd12345678", "a1b2c3d4e5f6", "iloveyou1234", "welcome12345",
    "admin1234567", "administrator1", "letmein12345", "monkey123456", "dragon123456", "football1234", "baseball1234", "sunshine1234",
    "princess1234", "superman1234", "trustno11234", "passw0rd1234", "p@ssw0rd1234", "changeme1234", "default12345", "test12345678",
    "testtest1234", "user12345678", "login1234567", "berghaus1234", "berghaus2026", "bergxaus2026", "parol1234567", "parol12345678",
    "qwerty2026!!", "password2026", "password2025", "zxcvbnm12345", "asdfghjkl123", "1234qwerasdf", "qwer1234asdf", "11111111111a",
    "a11111111111", "123456789abc", "123456789qwe", "123456789aaa", "1234567890ab", "q1w2e3r4t5y6", "q1w2e3r4t5y6u7",
}
_LEET = str.maketrans({"@": "a", "0": "o", "1": "i", "3": "e", "$": "s", "5": "s", "!": "i", "4": "a", "7": "t"})


def _tokens(user: Optional[User]) -> set[str]:
    if user is None:
        return set()
    parts = re.split(r"[^0-9a-zа-яё]+", f"{(user.email or '').split('@')[0]} {user.display_name or ''}".lower())
    return {p for p in parts if len(p) >= 3}


def problems(password: str, user: Optional[User] = None) -> list[str]:
    """Список нарушений; пусто — пароль подходит (без проверки истории — нужна БД)."""
    pw = password or ""
    out = []
    if len(pw) < MIN_LEN:
        out.append(f"не короче {MIN_LEN} символов")
    if len(pw) > MAX_LEN:
        out.append(f"не длиннее {MAX_LEN} символов")
    if not re.search(r"[A-Za-zА-Яа-яЁё]", pw) or not re.search(r"\d", pw):
        out.append("хотя бы одна буква и одна цифра")
    low = pw.lower()
    if low in COMMON or low.translate(_LEET) in COMMON:
        out.append("не из списка распространённых паролей")
    sym = str.maketrans({"@": "a", "$": "s", "!": "i"})
    cores = {re.sub(r"[^a-zа-яё]", "", low), re.sub(r"[^a-zа-яё]", "", low.translate(sym))}   # «Evgeniy2026!!» → «evgeniy»
    toks = _tokens(user)
    derived = toks | {t * 2 for t in toks} | {a + b for a in toks for b in toks}   # имя, «имяимя», «имяфамилия»
    if any(c and c in derived for c in cores):
        out.append("не совпадает с логином или именем")
    return out


def validate(db: Session, user: Optional[User], password: str) -> None:
    from ..core.auth import verify_password
    errs = problems(password, user)
    if not errs and user is not None:
        recent = [user.password_hash] if user.password_hash else []
        recent += db.scalars(select(PasswordHistory.password_hash).where(PasswordHistory.user_id == user.id)
                             .order_by(PasswordHistory.created_at.desc()).limit(HISTORY)).all()
        if any(verify_password(password, h) for h in recent if h):
            errs.append(f"не совпадает с текущим и {HISTORY} прежними паролями")
    if errs:
        raise HTTPException(422, {"code": "password_policy", "message": "Пароль не подходит: " + "; ".join(errs), "problems": errs})


def remember_old(db: Session, user: User) -> None:
    """Перед сменой — текущий хэш в историю (храним последние HISTORY)."""
    if not user.password_hash:
        return
    if not db.scalar(select(PasswordHistory.id).where(PasswordHistory.user_id == user.id, PasswordHistory.password_hash == user.password_hash)):
        db.add(PasswordHistory(user_id=user.id, password_hash=user.password_hash))
        db.flush()
    old = db.scalars(select(PasswordHistory).where(PasswordHistory.user_id == user.id).order_by(PasswordHistory.created_at.desc()).offset(HISTORY)).all()
    for h in old:
        db.delete(h)
