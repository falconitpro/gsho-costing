"""Разовый перенос контактов клиентов в единый справочник (D-189; решение владельца «Единый справочник контактов» v1.0, раздел 5).

Источники: прежний список карточки клиента (clients.contacts) и контакты юридических профилей клиентов (legal_profiles.contacts).
Профили собственных компаний и поставщиков не переносятся.

Сопоставление у того же клиента: точная почта (основная или дополнительная) → нормализованный телефон (последние 10 цифр).
Совпадение только по ФИО — «неоднозначно»: не создаётся и не объединяется, идёт в ручной разбор (перенос на рабочем
сервере выполняется только при нуле неоднозначных). Записи из юр. профиля получают у клиента роль «Договоры и документы».
Каждый источник записывается в contact_migration_map (ключ = источник + владелец + номер + отпечаток записи), повторный
запуск ничего не дублирует. Исходные списки не меняются. В вывод попадают только количества — без персональных данных.

  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend python -m scripts.contacts_migrate          # пробный
  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend python -m scripts.contacts_migrate --yes    # перенос
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter

from sqlalchemy import select

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _d(s) -> str:
    return re.sub(r"\D", "", str(s or ""))[-10:]


def _rows(raw) -> list[tuple[int, dict]]:
    out = []
    for i, x in enumerate(raw or []):
        if not isinstance(x, dict):
            continue
        name = " ".join(str(x.get("name") or x.get("full_name") or x.get("fio") or "").split())
        email = str(x.get("email") or "").strip().lower()
        out.append((i, {"full_name": name[:255], "position": str(x.get("position") or "")[:255], "work_phone": str(x.get("phone") or "").strip()[:64], "mobile_phone": str(x.get("mobile") or "").strip()[:64],
                        "work_email": email if EMAIL.match(email) else "", "bad_email": bool(email and not EMAIL.match(email)), "raw": x}))
    return out


def _key(source: str, owner: str, i: int, raw: dict) -> str:
    h = hashlib.sha1(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    return f"{source}:{owner}:{i}:{h}"


def plan(db, only: set | None = None) -> dict:
    from app.api.client_contacts import client_contacts, emails_of
    from app.models import Client, ContactMigrationMap, LegalProfile
    done = set(db.scalars(select(ContactMigrationMap.source_key)).all())
    sources: list[tuple[str, str, str, int, dict]] = []      # (source, client_id, owner_id, i, row)
    for c in db.scalars(select(Client)).all():
        if only and c.id not in only:
            continue
        for i, r in _rows(c.contacts):
            sources.append(("client_card", c.id, c.id, i, r))
    for p in db.scalars(select(LegalProfile).where(LegalProfile.owner_kind == "client")).all():
        if only and p.owner_id not in only:
            continue
        for i, r in _rows(p.contacts):
            sources.append(("legal_profile", p.owner_id, p.id, i, r))
    known: dict[str, list[dict]] = {}
    res = {"items": [], "count": Counter()}
    for src, cid, owner, i, r in sources:
        key = _key(src, owner, i, r["raw"])
        res["count"][f"{src}: всего"] += 1
        if key in done:
            res["count"][f"{src}: уже перенесено ранее"] += 1
            continue
        if cid not in known:
            known[cid] = [{"id": c.id, "link": l, "emails": set(emails_of(c)), "phone": _d(c.work_phone), "mobile": _d(c.mobile_phone), "name": c.full_name.lower(), "new": False}
                          for c, l in client_contacts(db, cid, True)]
        if not r["full_name"]:
            res["count"][f"{src}: без ФИО — вручную"] += 1
            res["items"].append({"key": key, "src": src, "client_id": cid, "outcome": "ambiguous", "row": r})
            continue
        if r["bad_email"]:
            res["count"][f"{src}: почта с ошибкой (перенос без почты)"] += 1
        em, nm = r["work_email"], r["full_name"].lower()
        phones = {x for x in (_d(r["work_phone"]), _d(r["mobile_phone"])) if x}
        hit = next((k for k in known[cid] if em and em in k["emails"]), None) or next((k for k in known[cid] if phones & {k["phone"], k.get("mobile", "")} - {""}), None)
        if hit is not None:
            res["count"][f"{src}: совпадает (почта или телефон)"] += 1
            res["items"].append({"key": key, "src": src, "client_id": cid, "outcome": "matched", "row": r, "hit": hit})
            continue
        if any(k["name"] == nm for k in known[cid]):
            res["count"][f"{src}: совпадает только по ФИО — вручную"] += 1
            res["items"].append({"key": key, "src": src, "client_id": cid, "outcome": "ambiguous", "row": r})
            continue
        entry = {"id": None, "link": None, "emails": {em} if em else set(), "phone": _d(r["work_phone"]), "mobile": _d(r["mobile_phone"]), "name": nm, "new": True}
        known[cid].append(entry)
        res["count"][f"{src}: новый контакт"] += 1
        res["items"].append({"key": key, "src": src, "client_id": cid, "outcome": "created", "row": r, "hit": entry})
    res["count"]["неоднозначных (вручную)"] = sum(1 for x in res["items"] if x["outcome"] == "ambiguous")
    return res


def run(yes: bool = False, force: bool = False, only: set | None = None) -> int:
    from app.db import SessionLocal
    from app.models import ClientContact, ClientContactLink, ContactMigrationMap
    from app.services.deal_service import audit
    with SessionLocal() as db:
        p = plan(db, only)
        print("== перенос контактов клиентов в единый справочник (без персональных данных)")
        for k in sorted(p["count"]):
            print(f"{k}: {p['count'][k]}")
        amb = p["count"]["неоднозначных (вручную)"]
        if not yes:
            print("Пробный прогон: ничего не изменено. Чтобы перенести, добавьте --yes")
            return 0
        if amb and not force:
            print(f"Остановлено: неоднозначных совпадений {amb} — разберите вручную (раздел 5.1 решения). Ничего не изменено.")
            return 1
        for it in p["items"]:
            r, src, cid = it["row"], it["src"], it["client_id"]
            role = ["contracts"] if src == "legal_profile" else []
            contact_id = link_id = None
            if it["outcome"] == "created" and it["hit"]["id"] is None:
                c = ClientContact(client_id=cid, full_name=r["full_name"], position=r["position"], work_phone=r["work_phone"], mobile_phone=r["mobile_phone"],
                                  work_email=r["work_email"], roles=role)
                db.add(c)
                db.flush()
                link = ClientContactLink(contact_id=c.id, client_id=cid, roles=role)
                db.add(link)
                db.flush()
                it["hit"]["id"], it["hit"]["link"] = c.id, link
                contact_id, link_id = c.id, link.id
                audit(db, None, None, "client_contact", c.id, "create", None, {"client_id": cid, "roles": role}, comment="contacts_migrate")
            elif it["outcome"] in ("created", "matched"):
                contact_id = it["hit"]["id"]
                link = it["hit"]["link"]
                if link is None:
                    link = db.scalars(select(ClientContactLink).where(ClientContactLink.contact_id == contact_id, ClientContactLink.client_id == cid)).first()
                if link is not None:
                    link_id = link.id
                    if role and role[0] not in (link.roles or []):
                        link.roles = [*(link.roles or []), *role]
            db.add(ContactMigrationMap(source=src, source_key=it["key"], contact_id=contact_id, link_id=link_id, outcome=it["outcome"]))
        db.commit()
        print(f"Готово: создано {sum(1 for x in p['items'] if x['outcome'] == 'created')}, сопоставлено {sum(1 for x in p['items'] if x['outcome'] == 'matched')}, "
              f"вручную {amb}. Исходные списки не изменены.")
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    only = {a[a.index("--client") + 1]} if "--client" in a else None   # перенос по одному клиенту
    sys.exit(run(yes="--yes" in a, force="--force" in a, only=only))
