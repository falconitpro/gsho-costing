"""Рабочее место логиста (D-073).

- Какие технические характеристики видит логист — выбирает ЗГД (служебная настройка `logistics_tech_fields`, по коду RFQ
  или по названию строки); по умолчанию — страна производства, габариты и массы.
- Адрес отгрузки — из карточки завода, адрес доставки — из карточки клиента (утверждённые адреса доставки) или пункта
  поставки позиции; логист может поправить. Новый адрес доставки уходит в карточку клиента «на утверждении ЗГД».
- Запрос логистическим компаниям: Excel и PDF по типовой форме, отправка по почте (когда почта подключена), ответы
  (файл + сумма, срок, условия) — в сделке.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Client, Deal, Factory, User

DEFAULT_FIELDS = [{"code": c, "name": n} for c, n in (
    ("1.3", "Страна производства"), ("1.5", "Длина (транспортное положение)"), ("1.6", "Ширина"), ("1.7", "Высота"),
    ("1.14", "Снаряжённая масса"), ("1.15", "Полная масса"))]
SERVICES = (("door_to_door", "Доставка завод → пункт поставки единой ставкой (без таможенного оформления)"),
            ("intl_freight", "Международная перевозка до границы РФ"), ("rf_delivery", "Доставка по РФ до пункта поставки"),
            ("insurance", "Страхование груза"), ("customs", "Таможенное оформление в РФ (брокер, СВХ)"),
            ("export", "Экспортное оформление в стране отправления"))
SERVICE_TITLES = dict(SERVICES)
# обычно таможню в РФ и экспортное оформление делаем без логистической компании (D-075)
DEFAULT_SERVICES = ["door_to_door", "intl_freight", "rf_delivery", "insurance"]


def services(deal: Deal) -> list[str]:
    sel = (deal.logistics_info or {}).get("services")
    return [k for k, _ in SERVICES if k in sel] if isinstance(sel, list) else list(DEFAULT_SERVICES)


def set_services(deal: Deal, codes: list[str]) -> list[str]:
    bad = [c for c in codes if c not in SERVICE_TITLES]
    if bad:
        raise ValueError(f"Неизвестные услуги: {', '.join(bad)}")
    if not codes:
        raise ValueError("Выберите хотя бы одну услугу")
    info = dict(deal.logistics_info or {})
    info["services"] = [k for k, _ in SERVICES if k in codes]
    deal.logistics_info = info
    return info["services"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _key(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower().replace("ё", "е"))


# ------------------------------------------------------------------ поля технических характеристик для логиста

def fields(db: Session) -> list[dict]:
    from . import settings_service
    v = settings_service.get(db, "logistics_tech_fields")
    return v if isinstance(v, list) and v else DEFAULT_FIELDS


def set_fields(db: Session, raw: list, user_id: str) -> list[dict]:
    from . import settings_service
    out, seen = [], set()
    for r in raw or []:
        code, name = str((r or {}).get("code") or "").strip()[:16], re.sub(r"\s+", " ", str((r or {}).get("name") or "")).strip()[:255]
        k = code or _key(name)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append({"code": code, "name": name})
    settings_service.set_value(db, "logistics_tech_fields", out, user_id, "поля технических характеристик для логиста")
    return out


def position_fields(db: Session, deal: Deal) -> list[dict]:
    """По каждой позиции — только выбранные ЗГД характеристики (значение завода, иначе требование клиента)."""
    chosen = fields(db)
    out = []
    for p in deal.positions:
        rows = list(p.tech_param_rows or [])
        by_code = {r.code: r for r in rows if r.code}
        by_name = {_key(r.name_ru): r for r in rows if r.name_ru}
        items = []
        for f in chosen:
            r = by_code.get(f["code"]) if f.get("code") else None
            r = r or by_name.get(_key(f.get("name") or ""))
            items.append({"code": f.get("code") or "", "name": (r.name_ru if r else "") or f.get("name") or f.get("code"), "unit": (r.unit if r else "") or "",
                          "value": (r.value_ru if r else "") or "", "requirement": (r.customer_requirement_ru if r else "") or ""})
        out.append({"position_id": p.id, "machine_type": p.machine_type, "designation": " ".join(x for x in (p.trademark, p.model) if x),
                    "qty": p.qty, "factory_id": p.factory_id, "basis": p.basis, "delivery_point": p.delivery_point, "rows": items})
    return out


def field_options(db: Session, deal: Optional[Deal] = None) -> list[dict]:
    """Из чего выбирать: строки общего шаблона и (если есть) таблиц позиций сделки — по блокам."""
    from . import tech_params_service as tps
    seen, out = set(), []
    for t in tps.ensure_general_template(db):
        k = t.code or _key(t.name_ru)
        if k not in seen:
            seen.add(k)
            out.append({"code": t.code or "", "name": tps.ru_only(t.name_ru) or t.name_ru, "section": tps.block_name(t.code, t.section)})
    for p in (deal.positions if deal else []):
        for r in p.tech_param_rows or []:
            k = r.code or _key(r.name_ru)
            if r.name_ru and k not in seen:
                seen.add(k)
                out.append({"code": r.code or "", "name": r.name_ru, "section": r.section or "Дополнительные параметры"})
    return out


# ------------------------------------------------------------------ адреса

def client_addresses(c: Optional[Client]) -> list[dict]:
    if c is None:
        return []
    return [a for a in (c.delivery_addresses or []) if a.get("address")]


def addresses(db: Session, deal: Deal) -> dict:
    info = deal.logistics_info or {}
    pick = info.get("pickup") or {}
    seen, pickup = set(), []
    for p in deal.positions:
        if not p.factory_id or p.factory_id in seen:
            continue
        seen.add(p.factory_id)
        f = db.get(Factory, p.factory_id)
        card = " ".join(x for x in ((f.address if f else ""), (f"({f.address_zh})" if f and f.address_zh else "")) if x).strip()
        pickup.append({"factory_id": p.factory_id, "factory": f.name if f else "", "card": card, "address": pick.get(p.factory_id) or card,
                       "edited": bool(pick.get(p.factory_id)) and pick.get(p.factory_id) != card})
    approved = [a for a in client_addresses(deal.client) if a.get("status") == "approved"]
    fallback = next((p.delivery_point for p in deal.positions if (p.delivery_point or "").strip()), "") or deal.site or ""
    delivery = info.get("delivery_address") or (approved[0]["address"] if approved else fallback)
    return {"pickup": pickup, "delivery_address": delivery, "client_addresses": client_addresses(deal.client),
            "delivery_source": "сделка" if info.get("delivery_address") else "карточка клиента" if approved else "пункт поставки / объект" if fallback else ""}


def set_addresses(db: Session, deal: Deal, user: User, pickup: dict[str, str], delivery: Optional[str], propose: bool) -> dict:
    from .deal_service import audit, notify
    from .legal_contracts import role_users
    info = dict(deal.logistics_info or {})
    before = {"pickup": info.get("pickup"), "delivery_address": info.get("delivery_address")}
    info["pickup"] = {k: re.sub(r"\s+", " ", v).strip()[:500] for k, v in (pickup or {}).items() if (v or "").strip()}
    if delivery is not None:
        info["delivery_address"] = re.sub(r"\s+", " ", delivery).strip()[:500]
    deal.logistics_info = info
    proposed = False
    addr = info.get("delivery_address") or ""
    c = deal.client
    if propose and addr and c is not None and _key(addr) not in {_key(a["address"]) for a in client_addresses(c)}:
        c.delivery_addresses = [*client_addresses(c), {"id": uuid.uuid4().hex[:12], "address": addr, "status": "pending", "proposed_by": user.id,
                                                        "proposed_at": _now(), "deal_number": deal.number}]
        proposed = True
        for u in role_users(db, "gd"):
            notify(db, deal, u.id, "client_address", None, f"Новый адрес доставки клиента «{c.name}» на утверждение (сделка {deal.number}): {addr}")
    audit(db, user.id, deal.id, "deal", deal.id, "logistics_addresses", before=before, after={"pickup": info["pickup"], "delivery_address": addr, "proposed": proposed})
    return {"proposed": proposed}


def decide_address(db: Session, c: Client, addr_id: str, user: User, approve: bool) -> None:
    from .deal_service import audit
    rows = list(c.delivery_addresses or [])
    a = next((x for x in rows if x.get("id") == addr_id), None)
    if a is None:
        raise KeyError(addr_id)
    if approve:
        a.update(status="approved", approved_by=user.id, approved_at=_now())
    else:
        rows = [x for x in rows if x.get("id") != addr_id]
    c.delivery_addresses = [dict(x) for x in rows]
    audit(db, user.id, None, "client", c.id, "delivery_address_" + ("approve" if approve else "reject"), after=a)


# ------------------------------------------------------------------ логистические компании (адресная книга запросов)

def carriers(db: Session) -> list[dict]:
    from . import settings_service
    v = settings_service.get(db, "logistics_carriers")
    return v if isinstance(v, list) else []


def set_carriers(db: Session, raw: list, user_id: str) -> list[dict]:
    from . import settings_service
    out = []
    for r in raw or []:
        name = str((r or {}).get("name") or "").strip()[:255]
        email = str((r or {}).get("email") or "").strip()[:255]
        if not name and not email:
            continue
        if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            raise ValueError(f"Неверный адрес почты: {email}")
        out.append({"name": name or email, "email": email, "phone": str(r.get("phone") or "").strip()[:64], "comment": str(r.get("comment") or "").strip()[:500]})
    settings_service.set_value(db, "logistics_carriers", out, user_id, "логистические компании")
    return out


# ------------------------------------------------------------------ запрос на расчёт доставки

def request_data(db: Session, deal: Deal, answer_days: int = 3) -> dict:
    from .request_fields import FIELDS  # noqa: F401  (единообразие названий)
    adr = addresses(db, deal)
    pos = position_fields(db, deal)
    readiness = None
    for p in deal.positions:
        from .production_term import calendar_days
        if p.production_term:
            d = date.today() + timedelta(days=calendar_days(p.production_term))
            readiness = d if readiness is None or d > readiness else readiness
    info = deal.logistics_info or {}
    no = f"{deal.number}-Л{len(info.get('requests') or []) + 1}"
    return {"no": no, "date": date.today().isoformat(), "answer_by": (date.today() + timedelta(days=answer_days)).isoformat(),
            "deal": deal.number, "client_region": adr["delivery_address"], "pickup": adr["pickup"], "delivery": adr["delivery_address"],
            "readiness": readiness.isoformat() if readiness else "", "positions": pos, "services": [SERVICE_TITLES[k] for k in services(deal)], "service_codes": services(deal),
            "basis": sorted({p.basis for p in deal.positions if p.basis}) or ["FCA"]}


ANSWER_COLUMNS = ["Услуга", "Стоимость", "Валюта", "Срок, дней", "Условия / что не входит"]


def to_xlsx(data: dict, path: str) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    wb = Workbook()
    ws = wb.active
    ws.title = "Запрос"
    red = PatternFill("solid", fgColor="E3262B")
    thin = Side(style="thin", color="BBBBBB")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws.append(["ООО «БЕРГ-ХАУС» — запрос на расчёт стоимости доставки"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"Запрос № {data['no']} от {data['date']}", "", "", f"Ответ просим до {data['answer_by']}"])
    ws.append([])
    rows = [("Пункт отгрузки", "; ".join(f"{p['factory']}: {p['address'] or 'адрес уточняется'}" for p in data["pickup"]) or "уточняется"),
            ("Пункт доставки", data["delivery"] or "уточняется"), ("Базис поставки завода", ", ".join(data["basis"])),
            ("Ориентировочная готовность груза", data["readiness"] or "уточняется")]
    for k, v in rows:
        ws.append([k, v])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
    ws.append([])
    ws.append(["Груз"])
    ws.cell(ws.max_row, 1).font = Font(bold=True, size=12)
    for p in data["positions"]:
        ws.append([f"{p['machine_type'] or 'Техника'} {p['designation']}".strip(), f"{p['qty']} шт."])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
        for r in p["rows"]:
            ws.append(["   " + r["name"], " ".join(x for x in (r["value"] or r["requirement"] or "—", r["unit"]) if x)])
    ws.append([])
    ws.append(["Просим рассчитать (заполните таблицу ниже)"])
    ws.cell(ws.max_row, 1).font = Font(bold=True, size=12)
    ws.append(ANSWER_COLUMNS)
    for c in ws[ws.max_row]:
        c.font, c.fill, c.border = Font(bold=True, color="FFFFFF"), red, box
    for s in data["services"]:
        ws.append([s, "", "", "", ""])
        for c in ws[ws.max_row]:
            c.border = box
    ws.append([])
    ws.append(["Срок действия предложения:", ""])
    ws.append(["Контактное лицо логистической компании:", ""])
    for col, w in zip("ABCDE", (52, 40, 10, 12, 40)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows():
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)


def to_pdf(data: dict, path: str) -> None:
    import html
    import pymupdf
    e = html.escape
    cargo = "".join(
        f"<tr><td colspan='2' class='pos'>{e((p['machine_type'] or 'Техника') + ' ' + p['designation'])} — {p['qty']} шт.</td></tr>"
        + "".join(f"<tr><td>{e(r['name'])}</td><td>{e(' '.join(x for x in (r['value'] or r['requirement'] or '—', r['unit']) if x))}</td></tr>" for r in p["rows"])
        for p in data["positions"])
    svc = "".join(f"<tr><td>{e(s)}</td><td></td><td></td><td></td><td></td></tr>" for s in data["services"])
    pick = "<br/>".join(f"{e(p['factory'])}: {e(p['address'] or 'адрес уточняется')}" for p in data["pickup"]) or "уточняется"
    doc = f"""<style>body{{font-family:sans-serif;font-size:10pt}} h1{{font-size:15pt;margin:0}} table{{border-collapse:collapse;width:100%;margin:4pt 0}}
td,th{{border:0.5pt solid #999;padding:3pt;vertical-align:top}} th{{background:#e3262b;color:#fff;text-align:left}} .pos{{background:#fdecec;font-weight:bold}}
.k{{width:34%;font-weight:bold}} .muted{{color:#666}}</style>
<h1>ООО «БЕРГ-ХАУС»</h1><p><b>Запрос на расчёт стоимости доставки № {e(data['no'])}</b> от {e(data['date'])} · ответ просим до <b>{e(data['answer_by'])}</b></p>
<table><tr><td class='k'>Пункт отгрузки</td><td>{pick}</td></tr><tr><td class='k'>Пункт доставки</td><td>{e(data['delivery'] or 'уточняется')}</td></tr>
<tr><td class='k'>Базис поставки завода</td><td>{e(', '.join(data['basis']))}</td></tr><tr><td class='k'>Готовность груза (ориентир)</td><td>{e(data['readiness'] or 'уточняется')}</td></tr></table>
<p><b>Груз</b></p><table>{cargo}</table>
<p><b>Просим рассчитать</b></p><table><tr>{''.join(f'<th>{e(c)}</th>' for c in ANSWER_COLUMNS)}</tr>{svc}</table>
<p class='muted'>Срок действия предложения: ____________ · Контактное лицо: ____________</p>"""
    story = pymupdf.Story(html=doc)
    w = pymupdf.DocumentWriter(path)
    mb = pymupdf.paper_rect("a4")
    where = mb + (36, 36, -36, -36)
    more = 1
    while more:
        dev = w.begin_page(mb)
        more, _ = story.place(where)
        story.draw(dev)
        w.end_page()
    w.close()


def record_request(deal: Deal, data: dict, user_id: str, sent_to: list[str], files: dict[str, str], req_id: str = "") -> dict:
    info = dict(deal.logistics_info or {})
    req = {"id": req_id or uuid.uuid4().hex[:12], "no": data["no"], "at": _now(), "by": user_id, "answer_by": data["answer_by"], "sent_to": sent_to, "files": files,
           "services": data.get("service_codes") or []}
    info["requests"] = [*(info.get("requests") or []), req]
    deal.logistics_info = info
    return req


# ------------------------------------------------------------------ ответы логистических компаний

def add_quote(deal: Deal, user_id: str, q: dict, attachment_id: Optional[str], file_name: str = "") -> dict:
    info = dict(deal.logistics_info or {})
    row = {"id": uuid.uuid4().hex[:12], "at": _now(), "by": user_id, "attachment_id": attachment_id, "file_name": file_name, **q}
    info["quotes"] = [*(info.get("quotes") or []), row]
    deal.logistics_info = info
    return row


def remove_quote(deal: Deal, qid: str) -> bool:
    info = dict(deal.logistics_info or {})
    rows = info.get("quotes") or []
    left = [q for q in rows if q.get("id") != qid]
    info["quotes"] = left
    deal.logistics_info = info
    return len(left) != len(rows)


def compare(db: Session, deal: Deal, quotes: list[dict], carriers_list: list[dict]) -> dict:
    """Сравнение предложений логистических компаний по выбранным услугам: итог в рублях по курсу ЦБ, срок, полнота, лучшее."""
    from .deal_calc import load_fx
    rates = load_fx(db, date.today())[0].rates
    sel = services(deal)
    need = [k for k in sel if k != "door_to_door"]
    by_mail = {(c.get("email") or "").lower(): c.get("name") for c in carriers_list}
    rows: dict[str, dict] = {}
    for q in quotes:
        name = (q.get("carrier") or "").strip()
        r = rows.setdefault(_key(name), {"carrier": name, "lines": {}, "quote_ids": []})
        ccy = (q.get("currency") or "RUB").upper()
        rate = 1.0 if ccy == "RUB" else float(rates.get(ccy) or 0)
        rub = float(q.get("amount") or 0) * rate
        cur = r["lines"].get(q.get("scope"))
        if cur is None or q.get("at", "") > cur.get("at", ""):     # по услуге берём последний ответ компании
            r["lines"][q.get("scope")] = {"id": q["id"], "amount": q.get("amount"), "currency": q.get("currency"), "rub": round(rub, 2),
                                         "transit_days": q.get("transit_days"), "valid_until": q.get("valid_until") or "", "at": q.get("at", ""), "comment": q.get("comment") or ""}
        r["quote_ids"].append(q["id"])
    out = []
    for r in rows.values():
        L = r["lines"]
        split = [k for k in need if k not in ("insurance",)]
        if "door_to_door" in L and "door_to_door" in sel:
            total = L["door_to_door"]["rub"] + sum(L[k]["rub"] for k in need if k in L and k not in ("intl_freight", "rf_delivery"))
            days = L["door_to_door"].get("transit_days")
            missing = [SERVICE_TITLES[k] for k in need if k not in L and k not in ("intl_freight", "rf_delivery", "insurance")]
            mode = "единой ставкой"
        else:
            total = sum(L[k]["rub"] for k in need if k in L)
            days = sum(int(L[k].get("transit_days") or 0) for k in ("intl_freight", "rf_delivery") if k in L) or None
            missing = [SERVICE_TITLES[k] for k in split if k not in L]
            mode = "по участкам"
        out.append({**r, "total_rub": round(total, 2), "transit_days": days, "missing": missing, "complete": not missing and total > 0, "mode": mode})
    full = [r for r in out if r["complete"]]
    best = min(full, key=lambda r: r["total_rub"])["carrier"] if full else None
    fastest = min((r for r in full if r["transit_days"]), key=lambda r: r["transit_days"], default=None)
    info = deal.logistics_info or {}
    out.sort(key=lambda r: (not r["complete"], r["total_rub"]))
    # кто из адресатов запросов ещё не ответил
    asked = {}
    for req in info.get("requests") or []:
        for m in req.get("sent_to") or []:
            asked[m.lower()] = by_mail.get(m.lower()) or m
    answered = {_key(r["carrier"]) for r in out}
    waiting = sorted({n for n in asked.values() if _key(n) not in answered})
    return {"services": sel, "rows": out, "best": best, "fastest": fastest["carrier"] if fastest else None,
            "chosen": info.get("chosen_carrier") or "", "chosen_at": info.get("chosen_at") or "", "waiting": waiting,
            "chosen_status": info.get("chosen_status") or ("approved" if info.get("chosen_carrier") else ""),
            "chosen_comment": info.get("chosen_comment") or "", "gd_comment": info.get("gd_comment") or "", "gd_decision_at": info.get("gd_decision_at") or ""}


def choose(deal: Deal, carrier: str, user_id: str, comment: str, need_gd: bool) -> str:
    """Выбор предложения; со согласованием ЗГД — статус «на согласовании» до решения ЗГД (D-075)."""
    info = dict(deal.logistics_info or {})
    status = "" if not carrier.strip() else "pending_gd" if need_gd else "approved"
    info.update(chosen_carrier=carrier.strip(), chosen_at=_now(), chosen_by=user_id, chosen_comment=comment.strip(), chosen_status=status,
                gd_decision_by=None, gd_decision_at=None, gd_comment="")
    deal.logistics_info = info
    return status


def decide_choice(deal: Deal, user_id: str, approve: bool, comment: str) -> None:
    info = dict(deal.logistics_info or {})
    info.update(chosen_status="approved" if approve else "declined", gd_decision_by=user_id, gd_decision_at=_now(), gd_comment=comment.strip())
    deal.logistics_info = info


def workspace(db: Session, deal: Deal, user: Any) -> dict:
    from ..core.config import get_settings
    info = deal.logistics_info or {}
    names = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    return {"fields": position_fields(db, deal), "addresses": addresses(db, deal), "carriers": carriers(db),
            "requests": [{**r, "by_name": names.get(r.get("by"), "")} for r in info.get("requests") or []],
            "quotes": [{**q, "by_name": names.get(q.get("by"), "")} for q in info.get("quotes") or []],
            "mail_enabled": get_settings().mail_provider != "console",
            "services": services(deal), "service_options": [{"code": k, "title": t} for k, t in SERVICES],
            "compare": compare(db, deal, info.get("quotes") or [], carriers(db))}
