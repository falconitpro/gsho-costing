"""Адаптер ИИ-моделей для юридического анализа договоров (юридический блок, этап 4).

Провайдеры (ключи — только в backend/.env, в интерфейсе не показываются):
  yandexgpt — Yandex Foundation Models: LLM_YANDEX_API_KEY (или YANDEX_TRANSLATE_API_KEY), YANDEX_FOLDER_ID; модель по умолчанию yandexgpt/latest
  openai    — любой OpenAI-совместимый API: LLM_OPENAI_API_KEY, LLM_OPENAI_BASE_URL (по умолчанию https://api.openai.com/v1), LLM_OPENAI_MODEL
  kimi      — Moonshot Kimi (OpenAI-совместимый): LLM_KIMI_API_KEY, LLM_KIMI_BASE_URL (по умолчанию https://api.moonshot.ai/v1), LLM_KIMI_MODEL (kimi-k2-0711-preview)
Провайдер и модель по умолчанию — YandexGPT; ЗГД переключает в «Юридический блок → Правила». Анализ работает и без ИИ — правила внутри системы.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

PROVIDERS = {"none": "без ИИ (только правила)", "yandexgpt": "YandexGPT", "openai": "OpenAI-совместимый API", "kimi": "Kimi (Moonshot)"}
DEFAULT_MODELS = {"yandexgpt": "yandexgpt/latest", "openai": "gpt-4o-mini", "kimi": "kimi-k2-0711-preview"}


class LLMNotConfigured(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    usage: dict[str, Any]


class LLMAdapter:
    name = "none"
    model = ""

    def complete(self, system: str, user: str, temperature: float = 0.1, max_tokens: int = 4000) -> LLMResult:
        raise LLMNotConfigured("ИИ-провайдер не настроен")

    def complete_json(self, system: str, user: str, **kw) -> Any:
        """Ответ модели как JSON (модели иногда оборачивают в ```json … ``` — вырезаем)."""
        r = self.complete(system + "\nОтвечай строго одним JSON без пояснений.", user, **kw)
        txt = r.text.strip()
        m = re.search(r"```(?:json)?\s*(.*?)```", txt, re.S)
        if m:
            txt = m.group(1).strip()
        start = txt.find("{") if txt.find("{") >= 0 else txt.find("[")
        end = max(txt.rfind("}"), txt.rfind("]"))
        if start >= 0 and end > start:
            txt = txt[start:end + 1]
        return json.loads(txt)


class YandexGPT(LLMAdapter):
    name = "yandexgpt"
    URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    def __init__(self, api_key: str, folder_id: str, model: str = "", timeout: float = 90.0):
        if not api_key or not folder_id:
            raise LLMNotConfigured("YandexGPT: задайте LLM_YANDEX_API_KEY (или YANDEX_TRANSLATE_API_KEY) и YANDEX_FOLDER_ID в backend/.env")
        self.api_key, self.folder_id, self.model, self.timeout = api_key, folder_id, model or DEFAULT_MODELS["yandexgpt"], timeout

    def complete(self, system: str, user: str, temperature: float = 0.1, max_tokens: int = 4000) -> LLMResult:
        import httpx
        uri = self.model if self.model.startswith("gpt://") else f"gpt://{self.folder_id}/{self.model}"
        body = {"modelUri": uri, "completionOptions": {"stream": False, "temperature": temperature, "maxTokens": str(max_tokens)},
                "messages": [{"role": "system", "text": system}, {"role": "user", "text": user}]}
        r = httpx.post(self.URL, json=body, timeout=self.timeout, headers={"Authorization": f"Api-Key {self.api_key}", "x-folder-id": self.folder_id})
        r.raise_for_status()
        res = r.json().get("result") or {}
        alt = (res.get("alternatives") or [{}])[0]
        return LLMResult(text=(alt.get("message") or {}).get("text", ""), provider=self.name, model=self.model, usage=res.get("usage") or {})


class OpenAICompatible(LLMAdapter):
    name = "openai"

    def __init__(self, api_key: str, base_url: str, model: str, name: str = "openai", timeout: float = 90.0):
        if not api_key:
            raise LLMNotConfigured(f"{PROVIDERS.get(name, name)}: задайте ключ API в backend/.env")
        self.api_key, self.base_url, self.model, self.name, self.timeout = api_key, (base_url or "https://api.openai.com/v1").rstrip("/"), model, name, timeout

    def complete(self, system: str, user: str, temperature: float = 0.1, max_tokens: int = 4000) -> LLMResult:
        import httpx
        body = {"model": self.model, "temperature": temperature, "max_tokens": max_tokens, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        r = httpx.post(f"{self.base_url}/chat/completions", json=body, timeout=self.timeout, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        r.raise_for_status()
        data = r.json()
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        return LLMResult(text=text or "", provider=self.name, model=self.model, usage=data.get("usage") or {})


def make_llm(provider: str, model: str = "", env: Optional[dict[str, str]] = None) -> LLMAdapter:
    e = env if env is not None else os.environ
    if provider == "yandexgpt":
        return YandexGPT(e.get("LLM_YANDEX_API_KEY") or e.get("YANDEX_TRANSLATE_API_KEY", ""), e.get("YANDEX_FOLDER_ID", ""), model)
    if provider == "openai":
        return OpenAICompatible(e.get("LLM_OPENAI_API_KEY", ""), e.get("LLM_OPENAI_BASE_URL", ""), model or e.get("LLM_OPENAI_MODEL") or DEFAULT_MODELS["openai"], "openai")
    if provider == "kimi":
        return OpenAICompatible(e.get("LLM_KIMI_API_KEY", ""), e.get("LLM_KIMI_BASE_URL", "https://api.moonshot.ai/v1"), model or e.get("LLM_KIMI_MODEL") or DEFAULT_MODELS["kimi"], "kimi")
    return LLMAdapter()


def configured_providers(env: Optional[dict[str, str]] = None) -> dict[str, bool]:
    """Какие провайдеры настроены ключами в .env (сами ключи не раскрываются)."""
    e = env if env is not None else os.environ
    return {"none": True, "yandexgpt": bool((e.get("LLM_YANDEX_API_KEY") or e.get("YANDEX_TRANSLATE_API_KEY")) and e.get("YANDEX_FOLDER_ID")),
            "openai": bool(e.get("LLM_OPENAI_API_KEY")), "kimi": bool(e.get("LLM_KIMI_API_KEY"))}
