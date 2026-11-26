"""Перенос контактов из карточек клиентов (clients.contacts) в справочник client_contacts (D-184, K-1).
По умолчанию — пробный прогон: ничего не меняет и выводит только количества (персональные данные в вывод не попадают).

Совпадение у одного клиента — по рабочей почте, иначе по телефону (последние 10 цифр), иначе по ФИО: такой контакт
не создаётся повторно, поэтому повторный запуск ничего не дублирует. Исходный clients.contacts не меняется.
Если включена настройка «Требовать отметку о согласии», перенесённые контакты получают источник
«перенесено из карточки клиента» и дату переноса — юрист решает, достаточно ли этого основания.

  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend python -m scripts.contacts_import         # пробный
  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend python -m scripts.contacts_import --yes   # перенос
"""
from __future__ import annotations

import re
import sys

from sqlalchemy import select


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")[-10:]


def _items(raw) -> list[dict]:
    out = []
    for x in raw or []:
        if not isinstance(x, dict):
            continue
        name = " ".join(str(x.get("name") or x.get("full_name") or x.get("fio") or "").split())
        if not name:
            continue
        out.append({"full_name": name[:255], "position": str(x.get("position") or "")[:255],
                    "work_phone": str(x.get("phone") or x.get("tel") or x.get("mobile") or "").strip()[:64],
                    "work_email": str(x.get("email") or "").strip().lower()[:255],
                    "messenger": str(x.get("messenger") or x.get("telegram") or "")[:255], "comment": str(x.get("comment") or "")})
    return out


def plan(db) -> dict:
    from app.models import Client, ClientContact
    have: dict[str, list] = {}
    for c in db.scalars(select(ClientContact)).all():
        have.setdefault(c.client_id, []).append(c)
    res = {"clients": 0, "create": [], "same": 0}
    for cl in db.scalars(select(Client)).all():
        items = _items(cl.contacts)
        if not items:
            continue
        res["clients"] += 1
        known = have.get(cl.id, [])
        seen: list[dict] = []
        for it in items:
            def match(o) -> bool:
                oe, op, on = (o.work_email, o.work_phone, o.full_name) if not isinstance(o, dict) else (o["work_email"], o["work_phone"], o["full_name"])
                if it["work_email"] and oe:
                    return it["work_email"] == oe
                if _digits(it["work_phone"]) and _digits(op):
                    return _digits(it["work_phone"]) == _digits(op)
                return it["full_name"].lower() == on.lower()
            if any(match(o) for o in known) or any(match(o) for o in seen):
                res["same"] += 1
                continue
            seen.append(it)
            res["create"].append((cl.id, it))
    return res


def run(yes: bool = False) -> int:
    from datetime import date

    from app.db import SessionLocal
    from app.models import ClientContact
    from app.services import settings_service
    from app.services.deal_service import audit
    with SessionLocal() as db:
        p = plan(db)
        print(f"клиентов с контактами в карточке: {p['clients']}; новых контактов: {len(p['create'])}; уже есть или повторяются: {p['same']}")
        if not yes:
            print("Пробный прогон: ничего не изменено. Чтобы перенести, добавьте --yes")
            return 0
        consent = bool(settings_service.get(db, "contacts_consent_required"))
        for client_id, it in p["create"]:
            c = ClientContact(client_id=client_id, **it, **({"consent_on": date.today(), "consent_source": "перенесено из карточки клиента"} if consent else {}))
            db.add(c)
            db.flush()
            audit(db, None, None, "client_contact", c.id, "create", None, {"client_id": client_id}, comment="contacts_import")
        db.commit()
        print(f"Готово: перенесено контактов {len(p['create'])}.")
    return 0


if __name__ == "__main__":
    sys.exit(run(yes="--yes" in sys.argv[1:]))
