"""Ошибки ИИ-провайдера понятны без чтения логов: код, сообщение провайдера, что проверить (403 YandexGPT — права ключа)."""
import httpx
import pytest

from app.adapters.llm import LLMProviderError, YandexGPT, _raise_for, make_llm, yandex_key_source


def _resp(code, body):
    return httpx.Response(code, json=body, request=httpx.Request("POST", "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"))


def test_yandex_403_with_translate_key_explains_what_to_do():
    llm = make_llm("yandexgpt", env={"YANDEX_TRANSLATE_API_KEY": "k", "YANDEX_FOLDER_ID": "b1gfolder"})
    assert isinstance(llm, YandexGPT) and llm.key_source == "YANDEX_TRANSLATE_API_KEY"
    with pytest.raises(LLMProviderError) as e:
        _raise_for(_resp(403, {"error": {"message": "Permission denied"}}), "YandexGPT", llm.hint_403())
    s = str(e.value)
    assert "HTTP 403" in s and "Permission denied" in s and "LLM_YANDEX_API_KEY" in s and "ai.languageModels.user" in s and "b1gfolder" in s
    assert yandex_key_source({"YANDEX_TRANSLATE_API_KEY": "k"}) == "YANDEX_TRANSLATE_API_KEY"
    assert yandex_key_source({"LLM_YANDEX_API_KEY": "k", "YANDEX_TRANSLATE_API_KEY": "t"}) == "LLM_YANDEX_API_KEY"


def test_own_key_and_other_codes():
    llm = make_llm("yandexgpt", env={"LLM_YANDEX_API_KEY": "k", "YANDEX_FOLDER_ID": "f"})
    assert "переводчика" not in llm.hint_403()
    with pytest.raises(LLMProviderError, match="HTTP 429.*квота"):
        _raise_for(_resp(429, {"message": "Too many requests"}), "YandexGPT")
    _raise_for(_resp(200, {}), "YandexGPT")   # успех — без исключения
