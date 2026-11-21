"""D-134: ИИ для писем Канцелярии — Yandex AI Studio (данные остаются в РФ).

Что умеет (ИИ предлагает — человек принимает; ничего не регистрирует, не подписывает и не отправляет сам):
- analyze   — входящее: краткое содержание 3–5 строк, вид письма, срок ответа из текста, требования, суммы и даты;
- edit      — черновик исходящего / приказа / записки: деловой стиль, мягче, жёстче, короче, только грамматика; показ «было / стало»;
- check     — проверка исходящего перед согласованием: цифры, даты, номера против входящего и сделки, недосказанности;
- translate — перевод письма RU → EN / ZH (китайский — Qwen3 235B); включается после проверки качества на реальных письмах.
Ограничения: конфиденциальные документы и приказы по личному составу в ИИ не уходят (152-ФЗ); в модель — только текст письма и
нужные поля, без доступа к базе; в журнал — кто, документ, действие, модель, число токенов и время, без текста.
Настройка (ключи — только в backend/.env, вводит владелец): LLM_YANDEX_API_KEY, YANDEX_FOLDER_ID, OFFICE_AI=on;
модели — OFFICE_AI_MODEL (yandexgpt/rc — YandexGPT Pro 5.1), OFFICE_AI_MODEL_TRANSLATE (qwen3-235b-a22b-fp8/latest).
"""
from __future__ import annotations

import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..adapters.llm import LLMProviderError, OpenAICompatible
from ..models import AuditLog, Deal, OfficeDoc

BASE_URL = "https://llm.api.cloud.yandex.net/v1"
DEFAULT_MODEL, DEFAULT_TRANSLATE = "yandexgpt/rc", "qwen3-235b-a22b-fp8/latest"
DAILY_LIMIT = 200
MAX_CHARS = 30000
EDIT_MODES = {"business": "деловой стиль", "softer": "мягче", "firmer": "жёстче", "shorter": "короче", "grammar": "только грамматика и пунктуация"}
LANGS = {"en": "английский", "zh": "китайский (упрощённые иероглифы)", "ru": "русский"}


def state(env: Optional[dict] = None) -> dict:
    e = env if env is not None else os.environ
    key, folder = bool(e.get("LLM_YANDEX_API_KEY")), bool(e.get("YANDEX_FOLDER_ID"))
    on = (e.get("OFFICE_AI", "off").lower() in ("on", "1", "true", "yes")) and key and folder
    return {"enabled": on, "configured": key and folder, "provider": "Yandex AI Studio",
            "model": e.get("OFFICE_AI_MODEL", DEFAULT_MODEL), "translate_model": e.get("OFFICE_AI_MODEL_TRANSLATE", DEFAULT_TRANSLATE)}


class _YandexOpenAI(OpenAICompatible):
    """OpenAI-совместимый API Yandex AI Studio: ключ — Bearer, каталог — заголовок OpenAI-Project, модель — gpt://<каталог>/<модель>."""

    def __init__(self, key: str, folder: str, model: str):
        super().__init__(key, BASE_URL, model if model.startswith("gpt://") else f"gpt://{folder}/{model}", "openai", timeout=120.0)
        self.folder = folder

    def complete(self, system: str, user: str, temperature: float = 0.1, max_tokens: int = 4000):
        import httpx
        from ..adapters.llm import LLMResult, _raise_for
        body = {"model": self.model, "temperature": temperature, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        r = httpx.post(f"{self.base_url}/chat/completions", json=body, timeout=self.timeout,
                       headers={"Authorization": f"Bearer {self.api_key}", "OpenAI-Project": self.folder, "Content-Type": "application/json"})
        _raise_for(r, "Yandex AI Studio", "сервисному аккаунту ключа нужна роль ai.languageModels.user в каталоге, у ключа — область yc.ai.languageModels.execute")
        data = r.json()
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "") or ""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()   # рассуждения Qwen3 — не показываем
        self._last_usage = data.get("usage") or {}
        return LLMResult(text=text, provider="yandex", model=self.model, usage=data.get("usage") or {})


def _llm(translate: bool = False):
    e = os.environ
    st = state()
    if not st["enabled"]:
        raise HTTPException(409, "ИИ для писем не подключён: владелец задаёт ключ Yandex AI Studio на сервере (deploy/production/ai-setup.sh)")
    return _YandexOpenAI(e["LLM_YANDEX_API_KEY"], e["YANDEX_FOLDER_ID"], st["translate_model"] if translate else st["model"])


def file_text(doc: OfficeDoc, files_dir: str, limit: int = MAX_CHARS) -> str:
    """Текст письма из файлов: текстовый слой PDF, письмо .eml, .txt, .docx. Скан без текстового слоя не читается (нужен OCR)."""
    out: list[str] = []
    try:
        names = os.listdir(files_dir)
    except FileNotFoundError:
        return ""
    for f in sorted(doc.files or [], key=lambda x: {"original": 0, "scan": 1, "draft": 2}.get(x.get("kind"), 3)):
        path = next((os.path.join(files_dir, x) for x in names if x.startswith(f["id"])), None)
        if not path:
            continue
        low = f["name"].lower()
        try:
            if low.endswith(".pdf"):
                import fitz
                with fitz.open(path) as pdf:
                    out.append("\n".join(p.get_text() for p in pdf)[:limit])
            elif low.endswith(".eml"):
                from .deal_feed import parse_eml
                with open(path, "rb") as fh:
                    m = parse_eml(fh.read())
                out.append(f"Тема: {m.get('subject', '')}\nОт: {m.get('from', '')}\n\n{m.get('body', '')}")
            elif low.endswith(".txt"):
                with open(path, encoding="utf-8", errors="replace") as fh:
                    out.append(fh.read(limit))
            elif low.endswith(".docx"):
                import docx
                out.append("\n".join(p.text for p in docx.Document(path).paragraphs))
        except Exception:   # noqa: BLE001 — повреждённый файл не мешает остальным
            continue
        if sum(len(x) for x in out) >= limit:
            break
    return "\n\n".join(x.strip() for x in out if x.strip())[:limit]


def _guard(db: Session, doc: OfficeDoc, user_id: str) -> None:
    if doc.confidential or (doc.kind == "order" and doc.category == "hr"):
        raise HTTPException(409, "Конфиденциальные документы и приказы по личному составу в ИИ не отправляются")
    since = datetime.now(timezone.utc) - timedelta(days=1)
    n = db.scalar(select(func.count(AuditLog.id)).where(AuditLog.user_id == user_id, AuditLog.entity == "office_ai", AuditLog.at >= since)) or 0
    if n >= DAILY_LIMIT:
        raise HTTPException(429, f"Лимит обращений к ИИ на сутки ({DAILY_LIMIT}) исчерпан")


def _numbers(s: str) -> set[str]:
    """Числа и даты для сверки «было / стало»: дата 05.12.2026 — одним значением, «1 250 000,00» — как 1250000.00."""
    pat = r"\d{1,2}\.\d{1,2}\.\d{2,4}|\d[\d\s\u00a0]*(?:[.,]\d+)?"
    return {re.sub(r"[\s\u00a0]", "", x).replace(",", ".").rstrip(".") for x in re.findall(pat, s or "") if len(re.sub(r"\D", "", x)) >= 2}


SYS = ("Ты — помощник делопроизводителя российской компании (горная техника, ВЭД, сервис). Пиши по-русски, деловым языком, без выдумок: "
       "если чего-то нет в тексте — так и скажи. Не добавляй фактов, цифр, дат и обещаний, которых нет в исходном тексте.")


def analyze(text: str, meta: dict) -> tuple[dict, Any]:
    llm = _llm()
    prompt = (f"Входящее письмо. Отправитель: {meta.get('counterparty') or '—'}; тема: {meta.get('subject') or '—'}; дата регистрации: {meta.get('reg_date') or '—'}.\n"
              f"Текст письма:\n<<<\n{text}\n>>>\n\n"
              "Верни JSON: {\"summary\": \"3–5 коротких строк: суть, что просят, к какому сроку\", "
              "\"letter_type\": \"request|claim|complaint|gov|notice|other\" (запрос, претензия, рекламация, запрос госоргана, уведомление, прочее), "
              "\"due_on\": \"ГГГГ-ММ-ДД или пусто, только если срок прямо указан в письме\", \"due_quote\": \"дословная фраза письма со сроком\", "
              "\"requirements\": [\"что конкретно требуют\"], \"amounts\": [\"суммы с валютой как в тексте\"], \"dates\": [\"значимые даты как в тексте\"], "
              "\"refs\": [\"номера договоров, счетов, писем из текста\"], \"suggested_role\": \"manager|logistician|service_head|lawyer|fd|cd|gd\", "
              "\"needs_reply\": true|false}")
    data = llm.complete_json(SYS, prompt, max_tokens=1500)
    if not isinstance(data, dict):
        raise LLMProviderError("модель вернула не JSON")
    # даты и суммы, которых нет в тексте письма, отбрасываем (модель не должна их придумывать)
    src = _numbers(text)
    data["amounts"] = [a for a in data.get("amounts") or [] if _numbers(a) <= src]
    due = str(data.get("due_on") or "")
    try:
        d = date.fromisoformat(due) if due else None
    except ValueError:
        d = None
    quote = re.sub(r"\s+", " ", str(data.get("due_quote") or "")).strip()
    if d and not (quote and re.search(r"\d", quote) and quote.lower() in re.sub(r"\s+", " ", text).lower()):
        d = None   # срок не подтверждён цитатой из письма — не предлагаем
    data["due_on"] = d.isoformat() if d else ""
    return data, llm


def edit(text: str, mode: str, kind_title: str) -> tuple[str, list[str], Any]:
    if mode not in EDIT_MODES:
        raise HTTPException(422, "Режим правки: " + ", ".join(EDIT_MODES.values()))
    llm = _llm()
    r = llm.complete(SYS + " Сохраняй все цифры, даты, номера, имена и смысл без изменений; не добавляй новых обязательств.",
                     f"Документ: {kind_title}. Задача: {EDIT_MODES[mode]}.\nВерни только исправленный текст без пояснений.\n<<<\n{text}\n>>>",
                     temperature=0.2, max_tokens=4000)
    out = r.text.strip().strip("<>").strip()
    lost = sorted(_numbers(text) - _numbers(out))
    added = sorted(_numbers(out) - _numbers(text))
    warn = ([f"пропали числа: {', '.join(lost)}"] if lost else []) + ([f"появились числа, которых не было: {', '.join(added)}"] if added else [])
    return out, warn, llm


def check(db: Session, doc: OfficeDoc, parent_text: str) -> tuple[dict, Any]:
    llm = _llm()
    facts = []
    for did in doc.deal_ids or []:
        d = db.get(Deal, did)
        if d is not None:
            facts.append(f"Сделка {d.number}" + (f", объект {d.site}" if getattr(d, "site", "") else ""))
    prompt = (f"Проверь исходящее письмо перед согласованием.\nАдресат: {doc.counterparty or '—'}; тема: {doc.subject}.\n"
              + (f"Сведения системы: {'; '.join(facts)}.\n" if facts else "")
              + (f"Входящее, на которое отвечаем:\n<<<\n{parent_text[:12000]}\n>>>\n" if parent_text else "")
              + f"Проект исходящего:\n<<<\n{doc.body[:15000]}\n>>>\n\n"
              "Верни JSON: {\"issues\": [{\"level\": \"high|medium|low\", \"text\": \"что не так и где\"}], "
              "\"unanswered\": [\"вопросы входящего, на которые ответа нет\"], \"ok\": true|false}. "
              "Ищи: расхождения цифр, дат, номеров с входящим и сведениями системы; обещания без оснований; ошибки; неясности. Не придумывай проблем.")
    data = llm.complete_json(SYS, prompt, max_tokens=2000)
    if not isinstance(data, dict):
        raise LLMProviderError("модель вернула не JSON")
    # локальная проверка: числа проекта, которых нет ни во входящем, ни в сведениях системы — показать человеку
    known = _numbers(parent_text) | _numbers(" ".join(facts)) | _numbers(doc.subject)
    new = sorted(n for n in _numbers(doc.body) - known if len(n) >= 3)
    data["numbers_to_verify"] = new[:30] if parent_text else []
    return data, llm


def translate(text: str, lang: str) -> tuple[str, list[str], Any]:
    if lang not in LANGS:
        raise HTTPException(422, "Язык: английский или китайский")
    llm = _llm(translate=True)
    r = llm.complete("You are a professional business translator for a Russian mining equipment company. Translate exactly, keep all numbers, "
                     "dates, model codes, units and names unchanged; keep the letter layout. Output only the translation. /no_think",
                     f"Translate into {({'en': 'English', 'zh': 'Simplified Chinese', 'ru': 'Russian'})[lang]}:\n<<<\n{text}\n>>>", temperature=0.1, max_tokens=6000)
    out = r.text.strip()
    lost = sorted(_numbers(text) - _numbers(out))
    return out, ([f"проверьте числа: {', '.join(lost)}"] if lost else []), llm


def log_call(db: Session, user_id: str, doc: OfficeDoc, action: str, llm: Any, started: float, ok: bool, err: str = "") -> None:
    """Журнал обращений к ИИ: без текста письма и ответа."""
    from .deal_service import audit
    usage = {}
    try:
        usage = dict(getattr(llm, "_last_usage", {}) or {})
    except Exception:   # noqa: BLE001
        pass
    audit(db, user_id, (doc.deal_ids or [None])[0], "office_ai", doc.id, action,
          after={"model": getattr(llm, "model", ""), "ok": ok, "ms": int((time.monotonic() - started) * 1000), "usage": usage, "error": err[:200]})
