"""Конфигурация из переменных окружения (см. .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    app_name: str = "GSHO Costing"
    env: str = field(default_factory=lambda: _env("APP_ENV", "dev"))
    secret_key: str = field(default_factory=lambda: _env("SECRET_KEY", "dev-secret-change-me"))
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "postgresql+psycopg://gsho:gsho@localhost:5432/gsho"))
    session_ttl_hours: int = field(default_factory=lambda: int(_env("SESSION_TTL_HOURS", "8")))
    cors_origins: list[str] = field(default_factory=lambda: _env("CORS_ORIGINS", "http://localhost:5173").split(","))

    # Вход (OpenID Connect): entra | yandex | keycloak | dev
    auth_provider: str = field(default_factory=lambda: _env("AUTH_PROVIDER", "dev"))
    oidc_issuer: str = field(default_factory=lambda: _env("OIDC_ISSUER", ""))
    oidc_client_id: str = field(default_factory=lambda: _env("OIDC_CLIENT_ID", ""))
    oidc_client_secret: str = field(default_factory=lambda: _env("OIDC_CLIENT_SECRET", ""))
    oidc_redirect_uri: str = field(default_factory=lambda: _env("OIDC_REDIRECT_URI", "http://localhost:8000/api/auth/callback"))
    oidc_group_role_map: str = field(default_factory=lambda: _env("OIDC_GROUP_ROLE_MAP", ""))  # "groupId:role,groupId:role"

    # Безопасность входа (docs/operations/04 §2–3, D-032…)
    session_absolute_hours: int = field(default_factory=lambda: int(_env("SESSION_ABSOLUTE_HOURS", "12")))   # вход не дольше 12 ч (D-041)
    mfa_policy: str = field(default_factory=lambda: _env("MFA_POLICY", "optional"))                          # optional | required
    mfa_encryption_key: str = field(default_factory=lambda: _env("MFA_ENCRYPTION_KEY", ""))                  # base64, 32 байта; в рабочем режиме обязателен
    legacy_sessions_until: str = field(default_factory=lambda: _env("LEGACY_SESSIONS_UNTIL", ""))            # ISO-время: до него принимаются старые cookie без sid

    # Хранилище: local | sharepoint | yandex_disk | s3
    storage_provider: str = field(default_factory=lambda: _env("STORAGE_PROVIDER", "local"))
    storage_local_root: str = field(default_factory=lambda: _env("STORAGE_LOCAL_ROOT", "./data/storage"))
    graph_tenant_id: str = field(default_factory=lambda: _env("GRAPH_TENANT_ID", ""))
    graph_site_id: str = field(default_factory=lambda: _env("GRAPH_SITE_ID", ""))
    graph_drive_id: str = field(default_factory=lambda: _env("GRAPH_DRIVE_ID", ""))
    s3_endpoint: str = field(default_factory=lambda: _env("S3_ENDPOINT", ""))
    s3_bucket: str = field(default_factory=lambda: _env("S3_BUCKET", "gsho"))
    s3_access_key: str = field(default_factory=lambda: _env("S3_ACCESS_KEY", ""))
    s3_secret_key: str = field(default_factory=lambda: _env("S3_SECRET_KEY", ""))

    # Почта: console | outlook | yandex_smtp
    mail_provider: str = field(default_factory=lambda: _env("MAIL_PROVIDER", "console"))
    mail_shared_mailbox: str = field(default_factory=lambda: _env("MAIL_SHARED_MAILBOX", "sales@example.com"))
    # SMTP (MAIL_PROVIDER=smtp или yandex_smtp): Яндекс 360 — smtp.yandex.ru:465, логин — адрес ящика, пароль приложения
    # Поиск наименования кода ТН ВЭД во внешнем сервисе: шаблон адреса с {code}, ответ JSON с полем name / Name / description
    # (например, API TKS «АПИ-ВЭД» или TWS.BY по договору); пусто — только справочник системы
    hs_lookup_url: str = field(default_factory=lambda: _env("HS_LOOKUP_URL", ""))
    smtp_host: str = field(default_factory=lambda: _env("SMTP_HOST", "smtp.yandex.ru"))
    smtp_port: int = field(default_factory=lambda: int(_env("SMTP_PORT", "465")))
    smtp_user: str = field(default_factory=lambda: _env("SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: _env("SMTP_PASSWORD", ""))
    smtp_from: str = field(default_factory=lambda: _env("SMTP_FROM", ""))
    # Мессенджер: console | wecom
    messenger_provider: str = field(default_factory=lambda: _env("MESSENGER_PROVIDER", "console"))
    moysklad_token: str = field(default_factory=lambda: _env("MOYSKLAD_TOKEN", ""))          # токен JSON API МойСклад — только в backend/.env
    moysklad_api: str = field(default_factory=lambda: _env("MOYSKLAD_API", "https://api.moysklad.ru/api/remap/1.2"))
    wecom_corp_id: str = field(default_factory=lambda: _env("WECOM_CORP_ID", ""))
    wecom_agent_id: str = field(default_factory=lambda: _env("WECOM_AGENT_ID", ""))
    wecom_secret: str = field(default_factory=lambda: _env("WECOM_SECRET", ""))

    # Переводчик RU↔CN (задача 4): console | yandex
    translator_provider: str = field(default_factory=lambda: _env("TRANSLATOR_PROVIDER", "yandex"))
    company_registry_provider: str = field(default_factory=lambda: _env("COMPANY_REGISTRY_PROVIDER", "dadata"))
    dadata_api_key: str = field(default_factory=lambda: _env("DADATA_API_KEY", ""))
    yandex_translate_api_key: str = field(default_factory=lambda: _env("YANDEX_TRANSLATE_API_KEY", ""))
    yandex_folder_id: str = field(default_factory=lambda: _env("YANDEX_FOLDER_ID", ""))

    # Курсы ЦБ
    cbr_daily_url: str = field(default_factory=lambda: _env("CBR_DAILY_URL", "https://www.cbr.ru/scripts/XML_daily.asp"))
    cbr_dynamic_url: str = field(default_factory=lambda: _env("CBR_DYNAMIC_URL", "https://www.cbr.ru/scripts/XML_dynamic.asp"))

    # Проверка загрузок антивирусом (04 §8, D-049): off | clamav. В production off запрещён (проверка при старте).
    file_scan: str = field(default_factory=lambda: _env("FILE_SCAN", "off"))
    clamd_host: str = field(default_factory=lambda: _env("CLAMD_HOST", "clamav"))
    clamd_port: int = field(default_factory=lambda: int(_env("CLAMD_PORT", "3310")))
    clamd_timeout: int = field(default_factory=lambda: int(_env("CLAMD_TIMEOUT", "120")))    # секунд на файл
    quarantine_dir: str = field(default_factory=lambda: _env("QUARANTINE_DIR", "./data/quarantine"))   # вне STORAGE_LOCAL_ROOT
    # Класс данных (04 §10.1): production | synthetic | recovery; пусто — по APP_ENV
    data_classification: str = field(default_factory=lambda: _env("DATA_CLASSIFICATION", ""))

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"

    @property
    def classification(self) -> str:
        if self.data_classification:
            return self.data_classification
        return {"prod": "production", "production": "production", "recovery": "recovery"}.get(self.env, "synthetic")


@lru_cache
def get_settings() -> Settings:
    return Settings()
