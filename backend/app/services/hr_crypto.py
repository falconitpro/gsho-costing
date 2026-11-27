"""Шифрование персональных и зарплатных данных «Персонала» (D-205): AES-GCM на уровне приложения.

Ключ:
- HR_ENCRYPTION_KEY в backend/.env (base64, 32 байта — «openssl rand -base64 32»), идентификатор «hr1»;
- если его нет — ключ выводится из MFA_ENCRYPTION_KEY (HMAC-SHA256 с меткой «gsho-hr-pd»), идентификатор «hrm1»:
  отдельной настройки сервера не требуется, но MFA-ключ и ключ ПДн тогда меняются только вместе;
- на стенде разработки без ключей — временный ключ от SECRET_KEY («dev»).

Ключ не хранится в базе. Каждая запись хранит идентификатор ключа (kid); запись другим ключом не расшифровывается — 503.
В базе и в хранилище файлов лежит только шифротекст; восстановление из резервной копии требует того же ключа (.env в защищённой заметке).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any, Optional

from fastapi import HTTPException

from ..core.config import Settings, get_settings
from . import mfa

MAGIC = b"GSHOHR1"   # заголовок зашифрованного файла


def _hr_key_raw(s: Settings) -> str:
    return mfa._normalize(os.environ.get("HR_ENCRYPTION_KEY", ""))


def _key(s: Settings) -> tuple[bytes, str]:
    raw = _hr_key_raw(s)
    if raw:
        try:
            k = base64.b64decode(raw, validate=True)
        except Exception:  # noqa: BLE001
            k = b""
        if len(k) != 32:
            raise HTTPException(503, "Ключ HR_ENCRYPTION_KEY задан неверно (нужен результат «openssl rand -base64 32») — обратитесь к администратору")
        return k, "hr1"
    st = mfa.key_status(s)
    if st == "ok":
        base = base64.b64decode(mfa._normalize(s.mfa_encryption_key))
        return hmac.new(base, b"gsho-hr-pd", hashlib.sha256).digest(), "hrm1"
    if st == "dev":
        return hashlib.sha256(b"gsho-hr-dev|" + s.secret_key.encode()).digest(), "dev"
    raise HTTPException(503, "Шифрование личных данных не настроено на сервере (нет ключа) — обратитесь к администратору")


def available(s: Optional[Settings] = None) -> bool:
    try:
        _key(s or get_settings())
        return True
    except HTTPException:
        return False


def current_kid(s: Optional[Settings] = None) -> str:
    return _key(s or get_settings())[1]


def encrypt_bytes(data: bytes, aad: str = "", s: Optional[Settings] = None) -> tuple[bytes, str]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key, kid = _key(s or get_settings())
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, data, f"{kid}|{aad}".encode()), kid


def decrypt_bytes(blob: bytes, kid: str, aad: str = "", s: Optional[Settings] = None) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key, cur = _key(s or get_settings())
    if kid != cur:
        raise HTTPException(503, "Данные зашифрованы другим ключом — нужен прежний ключ шифрования (обратитесь к администратору)")
    return AESGCM(key).decrypt(blob[:12], blob[12:], f"{kid}|{aad}".encode())


def seal(obj: Any, aad: str) -> tuple[str, str]:
    """dict → base64 шифротекст. aad привязывает шифротекст к записи (нельзя переставить чужой)."""
    blob, kid = encrypt_bytes(json.dumps(obj, ensure_ascii=False, default=str).encode(), aad)
    return base64.b64encode(blob).decode(), kid


def unseal(text: str, kid: str, aad: str) -> Any:
    if not text:
        return {}
    return json.loads(decrypt_bytes(base64.b64decode(text), kid, aad).decode())


def seal_file(data: bytes, aad: str) -> tuple[bytes, str]:
    blob, kid = encrypt_bytes(data, aad)
    return MAGIC + blob, kid


def unseal_file(data: bytes, kid: str, aad: str) -> bytes:
    if not data.startswith(MAGIC):
        raise HTTPException(500, "Файл повреждён: нет заголовка шифрования")
    return decrypt_bytes(data[len(MAGIC):], kid, aad)
