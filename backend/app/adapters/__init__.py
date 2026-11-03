"""Сменные адаптеры интеграций (раздел 13 ТЗ).

Прикладной код работает только через интерфейсы ниже; конкретный поставщик выбирается настройками:
  STORAGE_PROVIDER   = local | sharepoint | yandex_disk | s3
  MAIL_PROVIDER      = console | outlook | yandex_smtp
  MESSENGER_PROVIDER = console | wecom
  TRANSLATOR_PROVIDER= console | yandex   (задача 4; ключ — YANDEX_TRANSLATE_API_KEY, каталог — YANDEX_FOLDER_ID)
  COMPANY_REGISTRY_PROVIDER = none | dadata   (реквизиты клиента по ИНН; ключ — DADATA_API_KEY)
  AUTH_PROVIDER      = dev | entra | yandex | keycloak   (см. core/auth.py)
"""
from __future__ import annotations

from ..core.config import Settings, get_settings
from .fx_source import CbrFxSource
from .mail import ConsoleMail, MailAdapter, OutlookGraphMail, YandexSmtpMail
from .messenger import ConsoleMessenger, MessengerAdapter, WeComMessenger
from .storage import LocalStorage, S3Storage, SharePointStorage, StorageAdapter, YandexDiskStorage
from .company_registry import CompanyRegistry, make_registry
from .translator import TranslatorAdapter, make_translator


def storage(s: Settings | None = None) -> StorageAdapter:
    s = s or get_settings()
    return {
        "local": lambda: LocalStorage(s.storage_local_root),
        "sharepoint": lambda: SharePointStorage(s),
        "yandex_disk": lambda: YandexDiskStorage(s),
        "s3": lambda: S3Storage(s),
    }[s.storage_provider]()


def mail(s: Settings | None = None) -> MailAdapter:
    s = s or get_settings()
    return {"console": ConsoleMail, "outlook": lambda: OutlookGraphMail(s), "yandex_smtp": lambda: YandexSmtpMail(s)}[s.mail_provider]()


def messenger(s: Settings | None = None) -> MessengerAdapter:
    s = s or get_settings()
    return {"console": ConsoleMessenger, "wecom": lambda: WeComMessenger(s)}[s.messenger_provider]()


def fx_source(s: Settings | None = None) -> CbrFxSource:
    s = s or get_settings()
    return CbrFxSource(s.cbr_daily_url, s.cbr_dynamic_url)


def translator(s: Settings | None = None) -> TranslatorAdapter:
    s = s or get_settings()
    return make_translator(s.translator_provider, s.yandex_translate_api_key, s.yandex_folder_id)


def company_registry(s: Settings | None = None) -> CompanyRegistry:
    s = s or get_settings()
    return make_registry(s.company_registry_provider, s.dadata_api_key)
