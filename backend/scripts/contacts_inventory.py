"""Обследование контактов клиентов перед единым справочником (D-187). Только чтение; в выводе — только количества,
без персональных данных. Запуск на рабочем сервере:

  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend python -m scripts.contacts_inventory

Источники: прежний список карточки клиента (clients.contacts), контакты юридических профилей клиентов
(legal_profiles.contacts), доверенности профилей, новый справочник (client_contacts) и отправленные КП (лента сделки).
Сопоставление — как в правилах миграции: почта → телефон (последние 10 цифр) → только ФИО (= «неоднозначно, вручную»)."""
from __future__ import annotations

import re
import sys
from collections import Counter

from sqlalchemy import select

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _d(s) -> str:
    return re.sub(r"\D", "", str(s or ""))[-10:]


def _n(s) -> str:
    return " ".join(str(s or "").lower().replace("ё", "е").split())


def _items(raw) -> list[dict]:
    out = []
    for x in raw or []:
        if isinstance(x, dict):
            out.append({"name": _n(x.get("name") or x.get("full_name") or x.get("fio")), "email": str(x.get("email") or "").strip().lower(),
                        "phone": _d(x.get("phone") or x.get("tel") or x.get("mobile"))})
    return out


def collect(db) -> dict:
    from app.models import Client, ClientContact, FeedEntry, LegalProfile
    reg: dict[str, list] = {}
    for c in db.scalars(select(ClientContact)).all():
        reg.setdefault(c.client_id, []).append({"name": _n(c.full_name), "email": c.work_email, "phone": _d(c.work_phone)})
    r: Counter = Counter()

    def classify(cid: str, it: dict, src: str) -> None:
        r[f"{src}: всего"] += 1
        if not it["name"]:
            r[f"{src}: без ФИО"] += 1
        if it["email"] and not EMAIL.match(it["email"]):
            r[f"{src}: почта с ошибкой"] += 1
        if not it["email"] and not it["phone"]:
            r[f"{src}: ни почты, ни телефона"] += 1
        known = reg.get(cid, [])
        if it["email"] and any(k["email"] == it["email"] for k in known):
            r[f"{src}: совпадает со справочником по почте"] += 1
        elif it["phone"] and any(k["phone"] and k["phone"] == it["phone"] for k in known):
            r[f"{src}: совпадает со справочником по телефону"] += 1
        elif it["name"] and any(k["name"] == it["name"] for k in known):
            r[f"{src}: совпадает только по ФИО — вручную"] += 1
        else:
            r[f"{src}: нет в справочнике — новый"] += 1

    for c in db.scalars(select(Client)).all():
        items = _items(c.contacts)
        if items:
            r["клиентов с прежним списком контактов"] += 1
        emails = Counter(i["email"] for i in items if i["email"])
        r["прежний список: повтор почты внутри клиента"] += sum(n - 1 for n in emails.values() if n > 1)
        for it in items:
            classify(c.id, it, "прежний список")
    for p in db.scalars(select(LegalProfile).where(LegalProfile.owner_kind == "client")).all():
        items = _items(p.contacts)
        r["юр. профилей клиентов"] += 1
        if items:
            r["юр. профилей клиентов с контактами"] += 1
        for it in items:
            classify(p.owner_id, it, "юр. профиль")
        poa = [x for x in (p.powers_of_attorney or []) if isinstance(x, dict)]
        r["доверенностей в юр. профилях клиентов"] += len(poa)
        r["подписант указан в профиле"] += 1 if (p.signatory_name or "").strip() else 0
    r["справочник: контактов"] = sum(len(v) for v in reg.values())
    r["отправок КП в ленте сделок"] = db.query(FeedEntry).filter(FeedEntry.kind == "mail_out", FeedEntry.subject.ilike("%КП%")).count()
    return dict(r)


def run() -> int:
    from app.db import SessionLocal
    with SessionLocal() as db:
        res = collect(db)
    print("== обследование контактов клиентов (только чтение, без персональных данных)")
    for k in sorted(res):
        print(f"{k}: {res[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
