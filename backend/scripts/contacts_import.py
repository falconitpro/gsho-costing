"""Перенос контактов из карточек клиентов (clients.contacts) в справочник client_contacts (D-184, K-1).
По умолчанию — пробный прогон: ничего не меняет и выводит только количества (персональные данные в вывод не попадают).

Совпадение у одного клиента — по рабочей почте или телефону (последние 10 цифр); совпадение только по ФИО — в ручной разбор
(не переносится); уже перенесённые не создаются повторно, поэтому повторный запуск ничего не дублирует. Исходный clients.contacts не меняется.
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
    """Сопоставление по решению «Единый справочник контактов» v1.0 (п. 5): почта → телефон у того же клиента;
    совпадение только по ФИО — «неоднозначно», в ручной разбор (не создаётся и не объединяется)."""
    from app.api.client_contacts import client_contacts, emails_of
    from app.models import Client
    res = {"clients": 0, "create": [], "same": 0, "ambiguous": 0}
    for cl in db.scalars(select(Client)).all():
        items = _items(cl.contacts)
        if not items:
            continue
        res["clients"] += 1
        known = [{"emails": set(emails_of(c)), "phone": _digits(c.work_phone), "name": c.full_name.lower()} for c, _ in client_contacts(db, cl.id, True)]
        for it in items:
            em, ph, nm = it["work_email"], _digits(it["work_phone"]), it["full_name"].lower()
            pool = known
            if em and any(em in k["emails"] for k in pool) or ph and any(k["phone"] and k["phone"] == ph for k in pool):
                res["same"] += 1
                continue
            if not em and not ph and any(k["name"] == nm for k in pool):
                res["ambiguous"] += 1      # только ФИО — вручную
                continue
            known.append({"emails": {em} if em else set(), "phone": ph, "name": nm})
            res["create"].append((cl.id, it))
    return res


def run(yes: bool = False) -> int:
    from datetime import date

    from app.db import SessionLocal
    from app.models import ClientContact, ClientContactLink
    from app.services import settings_service
    from app.services.deal_service import audit
    with SessionLocal() as db:
        p = plan(db)
        print(f"клиентов с контактами в карточке: {p['clients']}; новых контактов: {len(p['create'])}; уже есть или повторяются: {p['same']}; "
              f"совпадают только по ФИО (вручную, не переносятся): {p['ambiguous']}")
        if not yes:
            print("Пробный прогон: ничего не изменено. Чтобы перенести, добавьте --yes")
            return 0
        consent = bool(settings_service.get(db, "contacts_consent_required"))
        for client_id, it in p["create"]:
            c = ClientContact(client_id=client_id, **it, **({"consent_on": date.today(), "consent_source": "перенесено из карточки клиента"} if consent else {}))
            db.add(c)
            db.flush()
            db.add(ClientContactLink(contact_id=c.id, client_id=client_id))
            audit(db, None, None, "client_contact", c.id, "create", None, {"client_id": client_id}, comment="contacts_import")
        db.commit()
        print(f"Готово: перенесено контактов {len(p['create'])}.")
    return 0


if __name__ == "__main__":
    sys.exit(run(yes="--yes" in sys.argv[1:]))
