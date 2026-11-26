"""Перенос площадок в справочник client_sites (D-182, K-2). По умолчанию — пробный прогон: ничего не меняет.

Источники по каждому клиенту:
  * clients.sites — площадки из карточки клиента (список {name, address?} или строк);
  * client_machines.site — текстовое «место работы» у машин клиента.
Одинаковые названия (без учёта регистра, пробелов и кавычек) — одна площадка. Уже заведённая площадка с тем же названием
используется повторно, поэтому повторный запуск ничего не дублирует. Похожие, но не одинаковые названия у одного клиента
выводятся списком «проверить вручную» и сами не объединяются.
Исходные clients.sites и client_machines.site не меняются.

Запуск на рабочем сервере (только по согласованию владельца):
  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend python -m scripts.sites_import          # пробный
  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend python -m scripts.sites_import --yes    # перенос
"""
from __future__ import annotations

import difflib
import re
import sys
from collections import defaultdict

from sqlalchemy import select

NOISE = re.compile(r"[«»\"'`.,;:()\-–—_/\\]+")


def norm(name: str) -> str:
    return re.sub(r"\s+", " ", NOISE.sub(" ", (name or "").lower().replace("ё", "е"))).strip()


def _json_sites(raw) -> list[tuple[str, str]]:
    out = []
    for x in raw or []:
        if isinstance(x, str):
            out.append((x.strip(), ""))
        elif isinstance(x, dict):
            out.append((str(x.get("name") or x.get("title") or "").strip(), str(x.get("address") or "").strip()))
    return [(n, a) for n, a in out if n]


def plan(db) -> dict:
    from app.models import Client, ClientMachine, ClientSite
    existing: dict[tuple[str, str], ClientSite] = {}
    for s in db.scalars(select(ClientSite)).all():
        existing.setdefault((s.client_id, norm(s.name)), s)
    machines: dict[str, list] = defaultdict(list)
    for m in db.scalars(select(ClientMachine).where(ClientMachine.site_id.is_(None), ClientMachine.site != "")).all():
        machines[m.client_id].append(m)
    clients = {c.id: c for c in db.scalars(select(Client)).all()}
    result = {"create": [], "reuse": 0, "link": [], "review": [], "clients": 0}
    for cid, c in clients.items():
        found: dict[str, dict] = {}
        for name, address in _json_sites(c.sites):
            found.setdefault(norm(name), {"name": name, "address": address, "from": set()})["from"].add("карточка клиента")
        for m in machines.get(cid, []):
            found.setdefault(norm(m.site), {"name": m.site.strip(), "address": "", "from": set()})["from"].add("машины")
        if not found:
            continue
        result["clients"] += 1
        for key, info in found.items():
            if (cid, key) in existing:
                result["reuse"] += 1
            else:
                result["create"].append({"client_id": cid, "client": c.name, "key": key, **info})
        for m in machines.get(cid, []):
            result["link"].append((m, cid, norm(m.site)))
        keys = sorted(set(found) | {k for (ci, k) in existing if ci == cid})
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                if difflib.SequenceMatcher(None, a, b).ratio() >= 0.85:
                    result["review"].append((c.name, a, b))
    return result


def run(yes: bool = False) -> int:
    from app.db import SessionLocal
    from app.models import ClientSite
    from app.services.deal_service import audit
    with SessionLocal() as db:
        p = plan(db)
        print(f"клиентов с площадками: {p['clients']}; новых площадок: {len(p['create'])}; уже есть: {p['reuse']}; машин к привязке: {len(p['link'])}")
        for x in p["create"][:200]:
            print(f"  + {x['client']}: «{x['name']}»" + (f" — {x['address']}" if x["address"] else "") + f" ({', '.join(sorted(x['from']))})")
        if p["review"]:
            print(f"проверить вручную — похожие названия у одного клиента ({len(p['review'])}); не объединяются:")
            for cl, a, b in p["review"][:100]:
                print(f"  ? {cl}: «{a}» ~ «{b}»")
        if not yes:
            print("Пробный прогон: ничего не изменено. Чтобы перенести, добавьте --yes")
            return 0
        made: dict[tuple[str, str], str] = {}
        for x in p["create"]:
            s = ClientSite(client_id=x["client_id"], name=x["name"][:255], address=x["address"], site_type="other",
                           comment="перенесено из карточки клиента / парка (D-182)")
            db.add(s)
            db.flush()
            made[(x["client_id"], x["key"])] = s.id
            audit(db, None, None, "client_site", s.id, "create", None, {"client_id": s.client_id, "name": s.name}, comment="sites_import")
        ids = {(s.client_id, norm(s.name)): s.id for s in db.scalars(select(ClientSite)).all()}
        linked = 0
        for m, cid, key in p["link"]:
            sid = made.get((cid, key)) or ids.get((cid, key))
            if sid:
                m.site_id = sid
                linked += 1
                audit(db, None, None, "client_machine", m.id, "link_site", None, {"site_id": sid}, comment="sites_import")
        db.commit()
        print(f"Готово: создано площадок {len(made)}, привязано машин {linked}.")
    return 0


if __name__ == "__main__":
    sys.exit(run(yes="--yes" in sys.argv[1:]))
