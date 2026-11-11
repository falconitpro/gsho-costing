"""TOTP и коды восстановления (docs/operations/04 §3, 02 §5).

- Секрет хранится только зашифрованным (AES-GCM). Ключ — MFA_ENCRYPTION_KEY (base64, 32 байта). В рабочем режиме без ключа
  настройка и проверка TOTP недоступны (503): обхода нет. В dev ключ выводится из SECRET_KEY (key_id «dev»), см. D-033.
- TOTP: RFC 6238, SHA-1, 30 с, 6 цифр, окно ±1 шаг (±30 с); повтор того же шага отклоняется.
- 10 кодов восстановления, каждый одноразовый; хранятся только хэши (PBKDF2).
- Второй шаг входа — MfaChallenge на 5 минут, до 5 попыток; в cookie лежит только случайное проверочное значение, в БД — его SHA-256.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import Settings, get_settings
from ..models import MfaChallenge, MfaCredential, MfaRecoveryCode, User

STEP, DIGITS, WINDOW = 30, 6, 1
CHALLENGE_TTL = timedelta(minutes=5)
CHALLENGE_ATTEMPTS = 5
PENDING_TTL = timedelta(minutes=15)
LOCK_AFTER, LOCK_FOR = 5, timedelta(minutes=15)
RECOVERY_COUNT = 10
ISSUER = "CRM БЕРГ-ХАУС"
CHALLENGE_COOKIE = "gsho_mfa"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ шифрование секрета

def _key(s: Settings) -> tuple[bytes, str]:
    if s.mfa_encryption_key:
        try:
            k = base64.b64decode(s.mfa_encryption_key)
        except Exception:  # noqa: BLE001
            k = b""
        if len(k) != 32:
            raise HTTPException(503, "Ключ шифрования TOTP задан неверно (MFA_ENCRYPTION_KEY: base64, 32 байта) — обратитесь к администратору")
        return k, "env1"
    if s.is_dev:
        return hashlib.sha256(b"gsho-mfa-dev|" + s.secret_key.encode()).digest(), "dev"
    raise HTTPException(503, "Вход с кодом из приложения не настроен на сервере (нет MFA_ENCRYPTION_KEY) — обратитесь к администратору")


def available(s: Optional[Settings] = None) -> bool:
    s = s or get_settings()
    try:
        _key(s)
        return True
    except HTTPException:
        return False


def encrypt(secret: str, s: Optional[Settings] = None) -> tuple[str, str]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key, kid = _key(s or get_settings())
    nonce = os.urandom(12)
    return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, secret.encode(), kid.encode())).decode(), kid


def decrypt(blob: str, kid: str, s: Optional[Settings] = None) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key, cur = _key(s or get_settings())
    if kid != cur:
        raise HTTPException(503, "Секрет TOTP зашифрован другим ключом — обратитесь к администратору (сброс TOTP)")
    raw = base64.b64decode(blob)
    return AESGCM(key).decrypt(raw[:12], raw[12:], kid.encode()).decode()


# ------------------------------------------------------------------ TOTP

def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    h = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** DIGITS).zfill(DIGITS)


def code_now(secret: str, at: Optional[float] = None) -> str:
    return _code_at(secret, int((at if at is not None else time.time()) // STEP))


def match_step(secret: str, code: str, at: Optional[float] = None) -> Optional[int]:
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == DIGITS):
        return None
    cur = int((at if at is not None else time.time()) // STEP)
    for st in range(cur - WINDOW, cur + WINDOW + 1):
        if hmac.compare_digest(_code_at(secret, st), code):
            return st
    return None


def otpauth_uri(secret: str, account: str) -> str:
    return f"otpauth://totp/{quote(ISSUER)}:{quote(account)}?secret={secret}&issuer={quote(ISSUER)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


# ------------------------------------------------------------------ учётные данные пользователя

def credential(db: Session, user_id: str) -> Optional[MfaCredential]:
    return db.scalar(select(MfaCredential).where(MfaCredential.user_id == user_id))


def enabled(db: Session, user_id: str) -> bool:
    c = credential(db, user_id)
    return bool(c and c.enabled_at and c.secret_ciphertext)


def start_setup(db: Session, user: User) -> dict:
    c = credential(db, user.id) or MfaCredential(user_id=user.id)
    secret = new_secret()
    c.pending_secret_ciphertext, c.pending_key_id = encrypt(secret)
    c.pending_expires_at = _now() + PENDING_TTL
    db.add(c)
    return {"secret": secret, "otpauth_uri": otpauth_uri(secret, user.email), "expires_in": int(PENDING_TTL.total_seconds())}


def _locked(c: MfaCredential) -> None:
    if c.locked_until and c.locked_until > _now():
        left = int((c.locked_until - _now()).total_seconds() // 60) + 1
        raise HTTPException(429, f"Слишком много неверных кодов. Повторите через {left} мин.")


def _fail(c: MfaCredential) -> None:
    c.failed_attempts = (c.failed_attempts or 0) + 1
    if c.failed_attempts >= LOCK_AFTER:
        c.locked_until, c.failed_attempts = _now() + LOCK_FOR, 0


def confirm_setup(db: Session, user: User, code: str) -> list[str]:
    c = credential(db, user.id)
    if c is None or not c.pending_secret_ciphertext or not c.pending_expires_at or c.pending_expires_at < _now():
        raise HTTPException(409, "Настройка не начата или истекла — начните заново")
    _locked(c)
    secret = decrypt(c.pending_secret_ciphertext, c.pending_key_id)
    st = match_step(secret, code)
    if st is None:
        _fail(c)
        db.commit()
        raise HTTPException(422, "Код не подошёл. Проверьте время на телефоне и введите текущий код из приложения")
    # действующий секрет заменяется только сейчас — после успешной проверки нового
    c.secret_ciphertext, c.key_id = c.pending_secret_ciphertext, c.pending_key_id
    c.pending_secret_ciphertext, c.pending_key_id, c.pending_expires_at = "", "", None
    c.enabled_at, c.last_used_at, c.last_used_step, c.failed_attempts, c.locked_until = _now(), _now(), st, 0, None
    return regenerate_recovery(db, user.id)


def verify_totp(db: Session, user_id: str, code: str) -> bool:
    """Проверка кода действующего TOTP. Неверный код увеличивает счётчик (коммит — на вызывающей стороне)."""
    c = credential(db, user_id)
    if c is None or not c.enabled_at:
        return False
    _locked(c)
    st = match_step(decrypt(c.secret_ciphertext, c.key_id), code)
    if st is None or st <= (c.last_used_step or 0):
        _fail(c)
        return False
    c.last_used_step, c.last_used_at, c.failed_attempts = st, _now(), 0
    return True


def disable(db: Session, user_id: str) -> None:
    c = credential(db, user_id)
    if c is not None:
        db.delete(c)
    for r in db.scalars(select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id)).all():
        db.delete(r)
    for ch in db.scalars(select(MfaChallenge).where(MfaChallenge.user_id == user_id, MfaChallenge.consumed_at.is_(None))).all():
        ch.consumed_at = _now()


# ------------------------------------------------------------------ коды восстановления

def _hash_code(code: str) -> str:
    salt = secrets.token_hex(8)
    return f"pbkdf2_sha256$20000${salt}${hashlib.pbkdf2_hmac('sha256', code.encode(), salt.encode(), 20000).hex()}"


def _check_code(code: str, stored: str) -> bool:
    try:
        _, it, salt, dig = stored.split("$")
        return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", code.encode(), salt.encode(), int(it)).hex(), dig)
    except ValueError:
        return False


def _norm(code: str) -> str:
    return "".join(ch for ch in (code or "").lower() if ch.isalnum())


def regenerate_recovery(db: Session, user_id: str) -> list[str]:
    for r in db.scalars(select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id)).all():
        db.delete(r)
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    codes = ["".join(secrets.choice(alphabet) for _ in range(4)) + "-" + "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(RECOVERY_COUNT)]
    for code in codes:
        db.add(MfaRecoveryCode(user_id=user_id, code_hash=_hash_code(_norm(code))))
    return codes


def use_recovery(db: Session, user_id: str, code: str) -> Optional[int]:
    """Одноразовое использование кода; возвращает, сколько осталось, или None, если код не подошёл."""
    n = _norm(code)
    if len(n) != 8:
        return None
    rows = db.scalars(select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id, MfaRecoveryCode.used_at.is_(None))).all()
    hit = next((r for r in rows if _check_code(n, r.code_hash)), None)
    if hit is None:
        return None
    hit.used_at = _now()
    return len(rows) - 1


def recovery_left(db: Session, user_id: str) -> int:
    return len(db.scalars(select(MfaRecoveryCode.id).where(MfaRecoveryCode.user_id == user_id, MfaRecoveryCode.used_at.is_(None))).all())


# ------------------------------------------------------------------ второй шаг входа

def create_challenge(db: Session, user: User, remember: bool, method: str, ip: str) -> str:
    token = secrets.token_urlsafe(32)
    db.add(MfaChallenge(user_id=user.id, verifier_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=_now() + CHALLENGE_TTL,
                        remember=remember, auth_method=method, ip_prefix=ip))
    return token


def load_challenge(db: Session, token: str) -> MfaChallenge:
    ch = db.scalar(select(MfaChallenge).where(MfaChallenge.verifier_hash == hashlib.sha256((token or "").encode()).hexdigest())) if token else None
    if ch is None or ch.consumed_at is not None or ch.expires_at < _now():
        raise HTTPException(401, {"code": "mfa_challenge_expired", "message": "Время на ввод кода истекло — войдите заново"})
    if ch.attempts >= CHALLENGE_ATTEMPTS:
        ch.consumed_at = _now()
        db.commit()
        raise HTTPException(429, {"code": "mfa_challenge_expired", "message": "Слишком много неверных кодов — войдите заново"})
    return ch
