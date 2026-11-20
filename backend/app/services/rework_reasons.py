"""Причины возврата на доработку справочником (D-100, аудит 19.11.2026).

Возврат по-прежнему требует причину; теперь её выбирают из списка («Настройки → Ставки и нормативы → Возвраты на доработку»),
комментарий — по желанию, для «Другое» обязателен. Код причины пишется в журнал действий (action="rework_reason") —
по нему строится отчёт «Возвраты на доработку». Возвраты до 20.11.2026 в отчёте — с исходным текстом, как «без кода».
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, time, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditLog, Deal, User
from . import settings_service

OTHER = "other"
BLOCKS = {"manager": "менеджер", "logistics": "логист", "service": "сервис", "pricing": "КД", "gd": "ЗГД"}


def normalize(rows: Any) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        title = str(r.get("title") or "").strip()
        code = str(r.get("code") or "").strip().lower()
        if not title and not code:
            continue
        if not title:
            raise ValueError(f"Причина с кодом «{code}»: укажите название")
        if not code:
            code = re.sub(r"[^a-z0-9_]+", "_", title.lower()).strip("_") or f"r{len(out) + 1}"
        if not re.fullmatch(r"[a-z0-9_]{1,40}", code):
            raise ValueError(f"Код «{code}»: латинские буквы, цифры и «_», до 40 знаков")
        if code in seen:
            raise ValueError(f"Код «{code}» повторяется")
        block = str(r.get("block") or "")
        if block and block not in BLOCKS:
            raise ValueError(f"Причина «{title}»: неизвестный блок «{block}»")
        seen.add(code)
        out.append({"code": code, "title": title[:200], "block": block})
    if OTHER not in seen:
        out.append({"code": OTHER, "title": "Другое (пояснить в комментарии)", "block": ""})
    return out


def catalog(db: Session) -> list[dict]:
    return normalize(settings_service.get(db, "rework_reasons"))


def resolve(db: Session, code: str, comment: str) -> tuple[dict, str]:
    """Причина по коду и текст для задачи и уведомления. ValueError — неизвестный код или нет комментария к «Другое»."""
    item = next((x for x in catalog(db) if x["code"] == code), None)
    if item is None:
        raise ValueError("Выберите причину возврата из списка")
    comment = (comment or "").strip()
    if code == OTHER and not comment:
        raise ValueError("Для причины «Другое» напишите комментарий")
    text = item["title"] if code != OTHER else comment
    if comment and code != OTHER:
        text = f"{item['title']}: {comment}"
    return item, text


_LEGACY = re.compile(r"^Возврат на доработку \((?P<block>[^)]*)\): (?P<reason>.*)$", re.S)


def report(db: Session, date_from: Optional[date], date_to: Optional[date], visible_deal_ids: Optional[set[str]] = None) -> dict:
    """Возвраты за период: по причинам, по блокам, по сделкам (сколько кругов), последние записи."""
    q = select(AuditLog).where(AuditLog.action.in_(("rework_reason", "status")))
    if date_from:
        q = q.where(AuditLog.at >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
    if date_to:
        q = q.where(AuditLog.at < datetime.combine(date_to, time.max, tzinfo=timezone.utc))
    rows = db.scalars(q.order_by(AuditLog.at)).all()
    cat = {x["code"]: x for x in catalog(db)}
    items: list[dict] = []
    for r in rows:
        if visible_deal_ids is not None and r.deal_id not in visible_deal_ids:
            continue
        if r.action == "rework_reason":
            a = r.after or {}
            code = a.get("code") or ""
            items.append({"at": r.at, "deal_id": r.deal_id, "user_id": r.user_id, "code": code, "title": (cat.get(code) or {}).get("title") or a.get("title") or code,
                          "block": a.get("block") or "", "comment": a.get("comment") or ""})
        elif (r.after or {}).get("status") == "rework" and not (r.after or {}).get("reason_code"):
            m = _LEGACY.match(r.comment or "")
            if not m:
                continue
            # возврат до справочника (или старым запросом без кода): исходный текст
            reason = m.group("reason").strip()
            visa = reason.startswith("Виза отклонена")   # отказ ФД или ЗГД в визе — отдельная строка отчёта, не «без кода»
            items.append({"at": r.at, "deal_id": r.deal_id, "user_id": r.user_id, "code": "_visa" if visa else "",
                          "title": "Отказ в визе" if visa else "без кода (до 20.11.2026 или старым способом)", "block_title": m.group("block"),
                          "block": "", "comment": reason})
    deals = {d.id: d for d in db.scalars(select(Deal).where(Deal.id.in_({x["deal_id"] for x in items if x["deal_id"]}))).all()} if items else {}
    names = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    by_reason = Counter(x["code"] or "_legacy" for x in items)   # _legacy — свободный текст без кода, _visa — отказ в визе
    by_block = Counter(x["block"] or "_none" for x in items)
    by_deal = Counter(x["deal_id"] for x in items)
    titles = {x["code"] or "_legacy": x["title"] for x in items}
    return {
        "total": len(items),
        "by_reason": [{"code": k, "title": titles[k], "count": n} for k, n in by_reason.most_common()],
        "by_block": [{"block": k, "title": BLOCKS.get(k, "не указан"), "count": n} for k, n in by_block.most_common()],
        "by_deal": [{"deal_id": k, "number": deals[k].number if k in deals else "", "count": n} for k, n in by_deal.most_common() if n >= 2],
        "rows": [{"at": x["at"].isoformat(), "deal_id": x["deal_id"], "number": deals[x["deal_id"]].number if x["deal_id"] in deals else "", "title": x["title"],
                  "block": BLOCKS.get(x["block"]) or x.get("block_title") or "", "comment": x["comment"], "by": names.get(x["user_id"], "")} for x in reversed(items)][:200],
    }
