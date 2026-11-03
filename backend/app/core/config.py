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
    # Мессенджер: console | wecom
    messenger_provider: str = field(default_factory=lambda: _env("MESSENGER_PROVIDER", "console"))
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

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
