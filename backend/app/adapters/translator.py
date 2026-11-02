"""Переводчик RU ↔ ZH (задача 4): сменный адаптер. Провайдер по умолчанию — Яндекс Переводчик (API в РФ).

Порядок перевода в прикладном коде: сначала глоссарий (подтверждённые термины), затем переводчик;
машинный перевод помечается «проверить» и попадает в глоссарий только после подтверждения человеком.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

log = logging.getLogger("translator")


class TranslatorAdapter(ABC):
    name = "none"

    @abstractmethod
    def translate(self, texts: list[str], src: str, dst: str) -> list[Optional[str]]:
        """Список переводов (None — перевод недоступен)."""


class ConsoleTranslator(TranslatorAdapter):
    """Без внешнего сервиса: переводы не выполняются (строки остаются для ручного ввода)."""
    name = "console"

    def translate(self, texts: list[str], src: str, dst: str) -> list[Optional[str]]:
        log.info("TRANSLATE %s→%s: %d строк (переводчик не подключён)", src, dst, len(texts))
        return [None for _ in texts]


class YandexTranslator(TranslatorAdapter):
    """Yandex Cloud Translate API v2: POST https://translate.api.cloud.yandex.net/translate/v2/translate."""
    name = "yandex"
    URL = "https://translate.api.cloud.yandex.net/translate/v2/translate"

    def __init__(self, api_key: str, folder_id: str = ""):
        self.api_key, self.folder_id = api_key, folder_id

    def translate(self, texts: list[str], src: str, dst: str) -> list[Optional[str]]:
        if not texts:
            return []
        import httpx
        out: list[Optional[str]] = []
        for i in range(0, len(texts), 100):   # лимит запроса — до 10 000 символов, бьём пачками
            chunk = texts[i:i + 100]
            body = {"sourceLanguageCode": src, "targetLanguageCode": dst, "texts": chunk, "format": "PLAIN_TEXT"}
            if self.folder_id:
                body["folderId"] = self.folder_id
            try:
                r = httpx.post(self.URL, json=body, headers={"Authorization": f"Api-Key {self.api_key}"}, timeout=30)
                r.raise_for_status()
                out.extend(t.get("text") for t in r.json().get("translations", []))
                while len(out) < i + len(chunk):
                    out.append(None)
            except Exception as e:   # сервис недоступен — строки остаются без перевода
                log.warning("yandex translate failed: %s", e)
                out.extend(None for _ in chunk)
        return out


def make_translator(provider: str, api_key: str = "", folder_id: str = "") -> TranslatorAdapter:
    if provider == "yandex" and api_key:
        return YandexTranslator(api_key, folder_id)
    return ConsoleTranslator()
