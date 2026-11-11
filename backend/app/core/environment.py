"""Классификация сред и данных (docs/operations/04 §10, D-050).

APP_ENV=prod (или production) → DATA_CLASSIFICATION=production; dev/test → synthetic; recovery → recovery (восстановленная копия
production только для drill). Backend при старте отказывается работать в опасных сочетаниях. Имени базы не доверяем:
в самой базе хранится отметка app_markers.data_classification, с которой она работала.

- synthetic-среда на базе с отметкой production/recovery — отказ (боевые данные в тестовом стенде);
- recovery: почта/мессенджер — только console, хранилище только локальное (никакой записи в production S3),
  фоновые задания выключены; отметка копии меняется на recovery (это копия, исходная база не затрагивается);
- production: класс production, вход не dev, проверка файлов включена (FILE_SCAN=clamav), свои SECRET_KEY,
  MFA_ENCRYPTION_KEY и пароль базы (значения из репозитория не принимаются).
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from .config import Settings

log = logging.getLogger("gsho.security")
MARKER = "data_classification"
ENV_CLASS = {"prod": "production", "production": "production", "recovery": "recovery"}   # в проекте рабочий режим — APP_ENV=prod
PROD = ("prod", "production")
AUTH_PROVIDERS = ("password", "entra", "yandex", "keycloak")
WEAK_SECRETS = ("", "change-me", "dev-secret-change-me")


class UnsafeEnvironment(RuntimeError):
    pass


def config_problems(s: Settings) -> list[str]:
    """Сочетания настроек, при которых запуск запрещён (без обращения к базе)."""
    p: list[str] = []
    want = ENV_CLASS.get(s.env, "synthetic")
    if s.classification not in ("production", "synthetic", "recovery"):
        p.append(f"DATA_CLASSIFICATION={s.classification}: допустимо production | synthetic | recovery")
    elif s.classification != want:
        p.append(f"APP_ENV={s.env} требует DATA_CLASSIFICATION={want}, задано {s.classification}")
    if s.env in PROD:
        if s.auth_provider == "dev":
            p.append("production с AUTH_PROVIDER=dev запрещён: задайте AUTH_PROVIDER=password (логин и пароль) или entra | yandex | keycloak")
        elif s.auth_provider not in AUTH_PROVIDERS:
            p.append(f"AUTH_PROVIDER={s.auth_provider}: допустимо password | entra | yandex | keycloak")
        if s.secret_key in WEAK_SECRETS or len(s.secret_key) < 32:
            p.append("SECRET_KEY не задан или короче 32 символов: задайте результат «openssl rand -hex 32»")
        from ..services import mfa
        if mfa.key_status(s) != "ok":
            p.append("MFA_ENCRYPTION_KEY не задан или неверен: задайте результат «openssl rand -base64 32» и сохраните его в защищённой заметке")
        if ":gsho@" in s.database_url:
            p.append("пароль базы данных по умолчанию: задайте POSTGRES_PASSWORD в файле .env")
        if s.file_scan != "clamav":
            p.append("production без проверки файлов запрещён: задайте FILE_SCAN=clamav")
    if s.env == "recovery":
        if s.mail_provider != "console":
            p.append("recovery: внешняя почта должна быть выключена (MAIL_PROVIDER=console)")
        if s.messenger_provider != "console":
            p.append("recovery: мессенджер должен быть выключен (MESSENGER_PROVIDER=console)")
        if s.storage_provider != "local":
            p.append("recovery: только локальное хранилище — нельзя писать в production S3 (STORAGE_PROVIDER=local)")
    return p


def check(db: Session, s: Settings) -> str:
    """Проверяет настройки и отметку базы; записывает отметку при первом запуске. Возвращает класс данных базы."""
    from ..models import AppMarker
    problems = config_problems(s)
    if problems:
        raise UnsafeEnvironment("; ".join(problems))
    if not inspect(db.get_bind()).has_table("app_markers"):
        if s.env in PROD + ("recovery",):
            raise UnsafeEnvironment("нет таблицы app_markers — примените backend/migrations/016b_file_scanning.sql")
        log.warning("app_markers отсутствует — проверка класса данных пропущена")
        return s.classification
    m = db.get(AppMarker, MARKER)
    cur = s.classification
    if m is None:
        db.add(AppMarker(key=MARKER, value=cur, app_env=s.env))
        db.commit()
        return cur
    if cur == "synthetic" and m.value in ("production", "recovery"):
        raise UnsafeEnvironment(f"база помечена как «{m.value}» — тестовая среда (APP_ENV={s.env}) с боевыми данными не запускается")
    if m.value != cur:
        log.warning("класс данных базы: %s → %s (APP_ENV=%s)", m.value, cur, s.env)
        m.value, m.app_env = cur, s.env
        db.commit()
    return cur
