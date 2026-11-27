"""Единый поиск Каталога (J-a, D-197).

Номера деталей и машин люди пишут по-разному: «BH-0001», «bh 0001», «BH.0001». Поэтому номер сравнивается
в нормализованном виде — только буквы и цифры, верхний регистр (NORM_SQL; та же формула — в индексах migrations/063).
Название ищется как есть (ILIKE). У каждой находки — причина совпадения, чтобы было видно, почему она в списке.
Серийные номера машин — только тем, у кого есть доступ к парку (группа client_fleet).
"""
from __future__ import annotations

import re

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from ..models import (ClientMachine, EquipmentExecution, EquipmentModel, Factory, OtherMachineModel, Part, PartCrossRef)

_STRIP = re.compile(r"[^0-9A-ZА-ЯЁ]")
NORM_PATTERN = "[^0-9A-ZА-ЯЁ]"


def norm(s: str) -> str:
    return _STRIP.sub("", (s or "").upper())


def norm_sql(col):
    """Та же нормализация в SQL: upper → убрать всё, кроме букв и цифр."""
    return func.regexp_replace(func.upper(col), NORM_PATTERN, "", "g")


def _rank(col, n: str):
    """0 — номер совпал полностью, 1 — начинается с запроса, 2 — содержит."""
    e = norm_sql(col)
    return case((e == n, 0), (e.like(f"{n}%"), 1), else_=2)


def search(db: Session, user, q: str, limit: int = 15) -> dict:
    q = (q or "").strip()
    out = {"models": [], "executions": [], "parts": [], "other": [], "machines": [], "q": q, "norm": norm(q)}
    if len(q) < 2:
        return out
    n, like = norm(q), f"%{q}%"
    nlike = f"%{n}%" if len(n) >= 2 else None
    facs = {f.id: (f.short_name or f.name) for f in db.scalars(select(Factory)).all()}

    # запчасти: номер (артикул, OEM, кросс) нормализованно, название — как есть
    part_hits: dict[str, dict] = {}
    if nlike:
        for col, why in ((Part.article, "артикул"), (Part.oem_number, "OEM")):
            for p, r in db.execute(select(Part, _rank(col, n)).where(Part.status == "active", norm_sql(col).like(nlike))
                                   .order_by(_rank(col, n), Part.article).limit(limit)).all():
                h = part_hits.get(p.id)
                if h is None or r < h["rank"]:
                    part_hits[p.id] = {"part": p, "rank": r, "match": why}
        for p, c, r in db.execute(select(Part, PartCrossRef, _rank(PartCrossRef.number, n)).join(PartCrossRef, PartCrossRef.part_id == Part.id)
                                  .where(Part.status == "active", norm_sql(PartCrossRef.number).like(nlike))
                                  .order_by(_rank(PartCrossRef.number, n)).limit(limit)).all():
            h = part_hits.get(p.id)
            if h is None or r < h["rank"]:
                part_hits[p.id] = {"part": p, "rank": r, "match": f"кросс-номер: {c.number}"}
    for p in db.scalars(select(Part).where(Part.status == "active", Part.name_ru.ilike(like)).order_by(Part.article).limit(limit)).all():
        part_hits.setdefault(p.id, {"part": p, "rank": 3, "match": "название"})
    for h in part_hits.values():
        p = h["part"]
        if h["match"].startswith("артикул"):
            h["match"] = f"артикул: {p.article}"
        elif h["match"].startswith("OEM"):
            h["match"] = f"OEM: {p.oem_number}"
    parts = sorted(part_hits.values(), key=lambda h: (h["rank"], h["part"].article))[:limit]
    out["parts"] = [{"id": h["part"].id, "article": h["part"].article, "name_ru": h["part"].name_ru, "manufacturer": h["part"].manufacturer,
                     "match": h["match"], "exact": h["rank"] == 0} for h in parts]

    # модели и исполнения ГШО
    mconds = [EquipmentModel.model.ilike(like), EquipmentModel.trademark.ilike(like), EquipmentModel.brand_name.ilike(like)]
    if nlike:
        mconds.append(norm_sql(EquipmentModel.model).like(nlike))
    models = db.scalars(select(EquipmentModel).where(EquipmentModel.status != "archived", or_(*mconds)).limit(limit)).all()
    out["models"] = [{"id": m.id, "title": " ".join(x for x in (m.trademark or facs.get(m.factory_id, ""), m.model) if x),
                      "machine_type": m.machine_type, "status": m.status, "match": "модель"} for m in models]
    econds = [EquipmentExecution.name.ilike(like), EquipmentExecution.brand_name.ilike(like)]
    if nlike:
        econds.append(norm_sql(EquipmentExecution.name).like(nlike))
    exs = db.execute(select(EquipmentExecution, EquipmentModel).join(EquipmentModel, EquipmentModel.id == EquipmentExecution.model_id)
                     .where(EquipmentExecution.status != "archived", EquipmentModel.status != "archived", or_(*econds)).limit(limit)).all()
    out["executions"] = [{"id": e.id, "model_id": m.id, "title": e.name,
                          "model_title": " ".join(x for x in (m.trademark or facs.get(m.factory_id, ""), m.model) if x), "match": "исполнение"}
                         for e, m in exs]
    seen = {x["id"] for x in out["models"]}
    for e in out["executions"]:                      # прежний формат: модель находится и по исполнению
        if e["model_id"] not in seen:
            m = db.get(EquipmentModel, e["model_id"])
            out["models"].append({"id": m.id, "title": e["model_title"], "machine_type": m.machine_type, "status": m.status,
                                  "match": f"исполнение: {e['title']}"})
            seen.add(m.id)

    oconds = [OtherMachineModel.manufacturer.ilike(like), OtherMachineModel.model.ilike(like)]
    if nlike:
        oconds.append(norm_sql(OtherMachineModel.model).like(nlike))
    other = db.scalars(select(OtherMachineModel).where(or_(*oconds)).limit(10)).all()
    out["other"] = [{"id": o.id, "title": f"{o.manufacturer} {o.model}", "machine_type": o.machine_type, "match": "модель"} for o in other]

    # машины клиентов по серийному номеру — только с доступом к парку
    if nlike and user.can("client_fleet"):
        from ..models import Client
        st = (select(ClientMachine, Client.name, _rank(ClientMachine.serial, n)).join(Client, Client.id == ClientMachine.client_id)
              .where(ClientMachine.serial != "", norm_sql(ClientMachine.serial).like(nlike)))
        if user.roles and all(r == "engineer" for r in user.roles):
            # инженер видит парк только по своим нарядам (D-181)
            from ..models import WorkOrder
            st = st.where(ClientMachine.id.in_(select(WorkOrder.machine_id).where(WorkOrder.engineer_id == user.id)))
        rows = db.execute(st.order_by(_rank(ClientMachine.serial, n), ClientMachine.serial).limit(limit)).all()
        out["machines"] = [{"id": m.id, "title": " ".join(x for x in (m.manufacturer, m.model) if x) or "машина", "serial": m.serial,
                            "client_name": cn, "client_id": m.client_id, "match": f"зав. №: {m.serial}", "exact": r == 0} for m, cn, r in rows]
    return out
