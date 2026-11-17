"""Шапка заявки (D-071): общий для всех новых сделок перечень полей над шаблоном технических характеристик.

Структуру правят КД и ЗГД (тот же редактор, что и шаблоны техники); значения вносит менеджер в каждой позиции.
По умолчанию — разделы опросника RFQ, не зависящие от типа машины: сведения о запросе, дополнительные опции,
коммерческие условия, сроки и гарантия (без полей, вынесенных в «Что нужно клиенту» и «Ответ завода»).
Хранится в настройке `request_header_rows`; строки с кодом RFQ сохраняются в позиции по коду, свои строки —
строками таблицы характеристик с блоком шапки.
"""
from __future__ import annotations

import re
from typing import Optional

HEADER_SECTIONS = (0, 12, 13, 14)
# поля карточки позиции — в шапке и таблице не повторяются (см. PositionForm OWN_CODES)
OWN_CODES = {"0.7", "1.1", "1.2", "13.1", "13.2", "13.3", "13.4", "13.5", "14.1", "14.4", "14.5"}


def _schema(db) -> dict:
    from sqlalchemy import select
    from ..models import Template
    from .rfq_import import template_fields
    from .rfq_template import template_path
    custom = db.scalar(select(Template).where(Template.kind == "rfq", Template.is_active.is_(True)).order_by(Template.version.desc()))
    return template_fields(template_path(custom.file_path if custom else None))


def default_rows(db) -> list[dict]:
    from .tech_params_service import ru_only
    sch = _schema(db)
    titles = {s["no"]: ru_only(s["title"]).split(" / ")[0] for s in sch["sections"]}
    out = []
    for f in sorted(sch["fields"], key=lambda f: [int(x) for x in re.findall(r"\d+", f["code"])]):
        no = f.get("section_no")
        if no not in HEADER_SECTIONS or f["code"] in OWN_CODES:
            continue
        out.append({"code": f["code"], "name_ru": ru_only(f.get("name") or "") or f["code"], "name_zh": f.get("name_zh") or "",
                    "unit": ru_only(f.get("unit") or "").replace("—", ""), "required": False, "section": f"{no}. {titles.get(no, '')}".strip()})
    return out


def rows(db) -> list[dict]:
    from . import settings_service
    saved = settings_service.get(db, "request_header_rows")
    base = saved if isinstance(saved, list) and saved else default_rows(db)
    return [{**r, "no": i} for i, r in enumerate(base, 1)]


def normalize(raw: list, user_id: Optional[str] = None) -> list[dict]:
    out = []
    for r in raw or []:
        name = re.sub(r"\s+", " ", str((r or {}).get("name_ru") or "")).strip()
        if not name:
            continue
        out.append({"code": str(r.get("code") or "").strip()[:16], "name_ru": name[:512], "name_zh": str(r.get("name_zh") or "").strip()[:512],
                    "unit": str(r.get("unit") or "").strip()[:64], "required": bool(r.get("required")),
                    "section": str(r.get("section") or "").strip()[:255] or "Шапка заявки"})
    return out
