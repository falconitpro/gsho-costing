"""Претензии по договорам (юридический блок, этап 4): лестница сроков, черновик претензии, печатная форма, решения.

Лестница по обязательствам контрагента (оплата нам, поставка / работы для нас):
  за 3 дня до срока — напоминание куратору (по оплате — и ФД);
  1-й день просрочки — уведомление куратору, юристам и ФД;
  5-й день просрочки — черновик претензии (долг / исполнение + неустойка), юристам, КД и ЗГД; подписывает КД или ЗГД.
Отправленная претензия переводит неустойки в «предъявлена», удовлетворённая — в «признана»; входящая претензия — наши неустойки в «предъявлена».
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Contract, ContractClaim, LegalEntity, LegalProfile, User
from . import legal_contracts as cs
from .kp_generator import ACCENT, BLACK, _run, _table
from .legal_poa import _setup, date_words

STATUSES_OUT = {"draft": "черновик", "signing": "на подписи у КД / ЗГД", "signed": "подписана", "sent": "отправлена — ждём ответа", "answered": "получен ответ",
                "court": "передана в суд", "closed": "закрыта", "cancelled": "отменена"}
STATUSES_IN = {"received": "получена — готовим ответ", "answered": "ответ направлен", "court": "спор в суде", "closed": "закрыта", "cancelled": "отозвана"}
RESULTS = {"satisfied": "удовлетворена полностью", "partial": "удовлетворена частично", "rejected": "отклонена", "no_answer": "ответа нет в срок"}
SENT_VIA = {"post": "почтой (заказное с уведомлением)", "courier": "курьером", "email": "по электронной почте", "edo": "через ЭДО"}
LINE_KINDS = {"debt": "долг", "performance": "исполнение обязательства", "penalty": "неустойка"}
SIGNER_TITLES = {"cd": "Коммерческий директор", "gd": "Заместитель генерального директора"}
OPEN = ("draft", "signing", "signed", "sent", "received", "answered")
REMIND_DAYS, CLAIM_DAY = 3, 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hist(cl: ContractClaim, user_id: Optional[str], action: str, comment: str = "") -> None:
    cl.history = list(cl.history or []) + [{"at": _now().isoformat(), "by": user_id, "action": action, "comment": comment}]


def statuses(cl: ContractClaim) -> dict:
    return STATUSES_IN if cl.direction == "incoming" else STATUSES_OUT


def totals(cl: ContractClaim) -> dict:
    debt = sum(float(x.get("amount") or 0) for x in cl.lines or [] if x.get("kind") == "debt")
    pen = sum(float(x.get("amount") or 0) for x in cl.lines or [] if x.get("kind") == "penalty")
    return {"debt": round(debt, 2), "penalty": round(pen, 2), "total": round(debt + pen, 2)}


def next_number(db: Session, c: Contract, on: date) -> tuple[str, int]:
    ent = db.get(LegalEntity, c.own_entity_id) if c.own_entity_id else None
    code = (ent.code if ent else "RU").upper()
    seq = (db.scalar(select(func.max(ContractClaim.seq)).where(ContractClaim.direction == "outgoing", ContractClaim.number.like(f"ПР-{code}-{on.year}-%"))) or 0) + 1
    return f"ПР-{code}-{on.year}-{seq:03d}", seq


def lines_for(db: Session, c: Contract, keys: Optional[list[str]] = None, side: str = "cp", today: Optional[date] = None) -> list[dict]:
    """Строки претензии по просроченным обязательствам должника side: долг (оплата) или исполнение + неустойки по ним."""
    ob = cs.obligations(db, c, today)
    out = []
    pool = [o for o in ob["payments"] + ob["performance"] if o["debtor"] == side and o["days_late"] > 0]
    if keys:
        pool = [o for o in pool if o["key"] in keys]
    for o in pool:
        is_pay = o["key"].startswith("pay:")
        if is_pay and o["status"] != "paid":
            out.append({"kind": "debt", "key": o["key"], "title": o["title"], "amount": o["amount"], "days": o["days_late"], "due_on": o["due_on"]})
        elif not is_pay and o["status"] != "done":
            out.append({"kind": "performance", "key": o["key"], "title": o["title"], "amount": None, "days": o["days_late"], "due_on": o["due_on"]})
        for p in ob["penalties"]:
            if p["obligation_key"] == o["key"] and p["payer"] == side and p["status"] != "waived" and p["amount"]:
                out.append({"kind": "penalty", "key": o["key"], "penalty_key": p["key"], "title": f"Неустойка: {p['reason_title']} ({o['title']})", "amount": p["amount"],
                            "days": p["days"], "due_on": o["due_on"], "rate_text": p["rate_text"]})
    return out


def create(db: Session, c: Contract, user_id: Optional[str], direction: str = "outgoing", keys: Optional[list[str]] = None, source: str = "manual",
           lines: Optional[list[dict]] = None, number: str = "", issued_on: Optional[date] = None) -> ContractClaim:
    today = date.today()
    cl = ContractClaim(contract_id=c.id, deal_id=c.deal_id, direction=direction, currency=c.currency or "RUB", source=source, created_by=user_id,
                       answer_days=int((c.law or {}).get("claim_days") or 30), status="draft" if direction == "outgoing" else "received")
    if direction == "outgoing":
        cl.lines = lines if lines is not None else lines_for(db, c, keys, "cp")
        cl.number, cl.seq = next_number(db, c, today)
    else:
        cl.lines = lines if lines is not None else lines_for(db, c, keys, "us")
        cl.number = number or "вх. б/н"
        cl.issued_on = issued_on or today
        cl.sent_on = issued_on or today
        cl.answer_due = (issued_on or today) + timedelta(days=cl.answer_days)
        mark_penalties(c, cl, "claimed", user_id)
    hist(cl, user_id, "created", ("черновик сформирован автоматически на 5-й день просрочки" if source == "auto" else "создана") + f" · {len(cl.lines)} строк")
    db.add(cl)
    db.flush()
    cs.hist(c, user_id, "claim", f"{'исходящая' if direction == 'outgoing' else 'входящая'} претензия {cl.number}")
    return cl


def mark_penalties(c: Contract, cl: ContractClaim, status: str, user_id: Optional[str], share: float = 1.0) -> None:
    """Неустойки из претензии → статус в договоре (предъявлена / признана) с суммой и сроком ответа."""
    for x in cl.lines or []:
        if x.get("kind") == "penalty" and x.get("penalty_key"):
            cs.set_fact(c, "pen", x["penalty_key"], {"status": status, "amount": round(float(x.get("amount") or 0) * share, 2), "on": (cl.sent_on or date.today()).isoformat(),
                                                    "due_on": cl.answer_due.isoformat() if cl.answer_due else None, "comment": f"претензия {cl.number}", "by": user_id})


def payload(db: Session, cl: ContractClaim, names: Optional[dict] = None, full: bool = True) -> dict:
    names = names if names is not None else cs._names(db)
    c = db.get(Contract, cl.contract_id)
    st = statuses(cl)
    t = totals(cl)
    today = date.today()
    out = {"id": cl.id, "contract_id": cl.contract_id, "contract_number": c.number if c else "", "cp_name": cs.cp_display(db, c) if c else "", "deal_id": cl.deal_id,
           "direction": cl.direction, "direction_title": "исходящая (наше требование)" if cl.direction == "outgoing" else "входящая (требование к нам)",
           "number": cl.number, "status": cl.status, "status_title": st.get(cl.status, cl.status), "currency": cl.currency, **t,
           "subject": subject(cl), "sent_on": cl.sent_on.isoformat() if cl.sent_on else None, "answer_due": cl.answer_due.isoformat() if cl.answer_due else None,
           "answer_overdue": bool(cl.answer_due and cl.answer_due < today and cl.status in ("sent", "received")), "result": cl.result, "result_title": RESULTS.get(cl.result, ""),
           "result_amount": cl.result_amount, "source": cl.source, "signer_name": names.get(cl.signer_id or "", ""), "created_at": cl.created_at.isoformat() if cl.created_at else None}
    if full:
        out.update({"lines": [{**x, "kind_title": LINE_KINDS.get(x.get("kind"), x.get("kind"))} for x in cl.lines or []], "demand": cl.demand, "answer_days": cl.answer_days,
                    "issued_on": cl.issued_on.isoformat() if cl.issued_on else None, "signer_role": cl.signer_role, "signed_at": cl.signed_at.isoformat() if cl.signed_at else None,
                    "sent_via": cl.sent_via, "track": cl.track, "answer_on": cl.answer_on.isoformat() if cl.answer_on else None, "answer_text": cl.answer_text,
                    "files": [{**f, "uploaded_by_name": names.get(f.get("uploaded_by") or "", "")} for f in cl.files or []],
                    "history": [{**h, "by_name": names.get(h.get("by") or "", "")} for h in reversed(cl.history or [])], "statuses": st,
                    "catalog": {"results": RESULTS, "sent_via": SENT_VIA, "line_kinds": LINE_KINDS}})
    return out


def subject(cl: ContractClaim) -> str:
    kinds = {x.get("kind") for x in cl.lines or []}
    keys = {str(x.get("key") or "") for x in cl.lines or []}
    if "debt" in kinds or any(k.startswith("pay:") for k in keys):
        return "о нарушении срока оплаты"
    if "performance" in kinds or any(k.startswith("perf:") for k in keys):
        return "о нарушении срока исполнения обязательств"
    return "о взыскании неустойки"


# ------------------------------------------------------------------ печатная форма
def _profile(db: Session, kind: str, owner_id: Optional[str]) -> Optional[LegalProfile]:
    if not owner_id:
        return None
    rows = db.scalars(select(LegalProfile).where(LegalProfile.owner_kind == kind, LegalProfile.owner_id == owner_id, LegalProfile.is_active.is_(True))).all()
    return next((p for p in rows if p.is_default), rows[0] if rows else None)


def _pname(p: Optional[LegalProfile], fallback: str) -> str:
    if p is None:
        return fallback
    n = (p.names or {}).get("ru") or {}
    return n.get("full") or n.get("short") or fallback


def _money(v) -> str:
    return "—" if v in (None, "") else f"{float(v):,.2f}".replace(",", " ").replace(".", ",")


def template_data(db: Session, cl: ContractClaim) -> dict:
    c = db.get(Contract, cl.contract_id)
    ent = db.get(LegalEntity, c.own_entity_id) if c and c.own_entity_id else None
    own = _profile(db, "own", c.own_entity_id if c else None)
    cpp = _profile(db, c.cp_kind, c.cp_id) if c and c.cp_kind in ("client", "factory") else None
    t = totals(cl)
    cur = "руб." if cl.currency == "RUB" else cl.currency
    signer = db.get(User, cl.signer_id) if cl.signer_id else None
    cnum = (c.cp_number or c.number) if c else ""
    cdate = c.signed_on.strftime("%d.%m.%Y") if c and c.signed_on else ""
    demands = []
    if t["debt"]:
        demands.append(f"оплатить задолженность в размере {_money(t['debt'])} {cur}")
    for x in cl.lines or []:
        if x.get("kind") == "performance":
            demands.append(f"исполнить обязательство: {x['title'].lower()}")
    if t["penalty"]:
        demands.append(f"уплатить неустойку в размере {_money(t['penalty'])} {cur}")
    if cl.demand.strip():
        demands.append(cl.demand.strip())
    facts = []
    for x in cl.lines or []:
        if x.get("kind") in ("debt", "performance"):
            facts.append(f"{x['title']} — срок {date.fromisoformat(x['due_on']).strftime('%d.%m.%Y') if x.get('due_on') else '—'}, обязательство не исполнено, просрочка {x.get('days', 0)} дн.")
    lines = [{"n": i + 1, "title": x["title"], "due_on": date.fromisoformat(x["due_on"]).strftime("%d.%m.%Y") if x.get("due_on") else "", "days": x.get("days") or "",
              "amount": _money(x.get("amount")) if x.get("amount") is not None else "", "kind": LINE_KINDS.get(x.get("kind"), ""), "rate": x.get("rate_text", "")} for i, x in enumerate(cl.lines or [])]
    on = cl.issued_on or date.today()
    return {"claim": {"number": cl.number, "date": on.strftime("%d.%m.%Y"), "date_words": date_words(on), "subject": subject(cl), "contract_number": cnum, "contract_date": cdate,
                      "contract_text": f"договор № {cnum}" + (f" от {cdate}" if cdate else ""), "debt": _money(t["debt"]), "penalty": _money(t["penalty"]), "total": _money(t["total"]),
                      "currency": cur, "answer_days": str(cl.answer_days), "demand_text": "; ".join(f"{i + 1}) {d}" for i, d in enumerate(demands)),
                      "facts_text": " ".join(facts), "signer": signer.display_name if signer else "", "signer_position": SIGNER_TITLES.get(cl.signer_role, "")},
            "sender": {"name": _pname(own, ent.name if ent else ""), "inn": own.tax_number if own else "", "kpp": own.kpp if own else "", "ogrn": own.reg_number if own else "",
                       "address": (own.legal_address if own else "")},
            "addressee": {"name": _pname(cpp, cs.cp_display(db, c) if c else ""), "inn": (cpp.tax_number if cpp else (c.cp_inn if c else "")), "address": cpp.legal_address if cpp else "",
                          "director": (f"Руководителю {cpp.director}" if cpp and cpp.director else "Руководителю")},
            "lines": lines, "_demands": demands}


def build_docx(db: Session, cl: ContractClaim, path: str, draft: bool) -> str:
    d = template_data(db, cl)
    k, snd, adr = d["claim"], d["sender"], d["addressee"]
    doc = Document()
    _setup(doc)
    hdr = doc.add_table(rows=1, cols=2)
    l, r = hdr.rows[0].cells
    l.text, r.text = "", ""
    _run(l.paragraphs[0], f"{snd['name']}\nИНН {snd['inn'] or '—'}{' / КПП ' + snd['kpp'] if snd['kpp'] else ''}\n{snd['address']}\n\nИсх. № {k['number']} от {k['date']}", color=BLACK, size=9)
    _run(r.paragraphs[0], f"{adr['director']}\n{adr['name']}\nИНН {adr['inn'] or '—'}\n{adr['address']}", bold=True, color=BLACK, size=10)
    doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(p, "ПРЕТЕНЗИЯ", bold=True, color=ACCENT, size=18)
    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(p2, f"{k['subject']} по {k['contract_text']}", bold=True, color=BLACK, size=11)
    if draft:
        pd = doc.add_paragraph()
        pd.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _run(pd, "ПРОЕКТ — претензия не подписана", color=ACCENT, size=8)
    doc.add_paragraph(f"Между {snd['name']} и {adr['name']} заключён {k['contract_text']} (далее — Договор).")
    if k["facts_text"]:
        doc.add_paragraph(f"В нарушение условий Договора: {k['facts_text']}")
    doc.add_paragraph("Расчёт требований на дату претензии:")
    _table(doc, ["№", "Обязательство / основание", "Срок", "Дней просрочки", "Ставка", f"Сумма, {k['currency']}"],
           [[str(x["n"]), x["title"], x["due_on"], str(x["days"]), x["rate"], x["amount"] or "—"] for x in d["lines"]] + [["", "Итого", "", "", "", k["total"]]])
    doc.add_paragraph()
    doc.add_paragraph("Условиями Договора предусмотрена ответственность за нарушение сроков исполнения обязательств в виде неустойки. "
                      "Обязательства должны исполняться надлежащим образом и в установленный срок (ст. 309, 310 ГК РФ); за просрочку подлежит уплате неустойка (ст. 330 ГК РФ).")
    para = doc.add_paragraph()
    _run(para, f"На основании изложенного требуем в течение {k['answer_days']} календарных дней с даты получения настоящей претензии: ", color=BLACK, size=11)
    for i, dm in enumerate(d["_demands"]):
        doc.add_paragraph(f"{i + 1}) {dm}.")
    doc.add_paragraph("В случае неудовлетворения требований в указанный срок мы будем вынуждены обратиться в арбитражный суд с иском о взыскании задолженности, неустойки, "
                      "процентов и судебных расходов. Ответ на претензию просим направить в письменном виде.")
    doc.add_paragraph("Приложения: копия Договора; расчёт неустойки.")
    doc.add_paragraph()
    sig = doc.add_paragraph()
    _run(sig, f"{k['signer_position'] or 'Коммерческий директор / Заместитель генерального директора'}\n{snd['name']}\n\n_______________ / {k['signer'] or '________'} /", color=BLACK, size=11)
    for s in doc.sections:
        f = s.footer.paragraphs[0]
        f.text = ""
        _run(f, f"Претензия № {k['number']} · {k['contract_text']} · CRM БЕРГ-ХАУС", color=ACCENT, size=8)
    doc.save(path)
    return path


# ------------------------------------------------------------------ лестница сроков (ежедневно)
def ladder(db: Session, today: Optional[date] = None) -> int:
    today = today or date.today()
    n = 0
    users = cs.role_users(db, "lawyer", "fd", "cd", "gd")
    lawyers = [u.id for u in users if "lawyer" in (u.roles or [])]
    fds = [u.id for u in users if "fd" in (u.roles or [])]
    bosses = [u.id for u in users if {"cd", "gd"} & set(u.roles or [])]
    open_claims = db.scalars(select(ContractClaim).where(ContractClaim.direction == "outgoing", ContractClaim.status.in_(OPEN))).all()
    claimed_keys = {(cl.contract_id, x.get("key")) for cl in open_claims for x in cl.lines or []}
    for c in db.scalars(select(Contract).where(Contract.status == "active", Contract.direction.in_(("income", "expense")))).all():
        ob = cs.obligations(db, c, today)
        sent = set(c.notified or [])
        add = []
        for o in ob["payments"] + ob["performance"]:
            if o["debtor"] != "cp" or o["status"] in ("paid", "done") or not o["due_on"] or not o["due_legal"]:
                continue
            due = date.fromisoformat(o["due_on"])
            is_pay = o["key"].startswith("pay:")
            who = [c.curator_id or ""] + (fds if is_pay else [])
            left = (due - today).days
            what = f"{o['title']} по договору {c.number} ({cs.cp_display(db, c)})"
            if 0 < left <= REMIND_DAYS and f"rem3:{o['key']}:{o['due_on']}" not in sent:
                cs.notify_users(db, c, who, f"Через {left} дн. ({due:%d.%m.%Y}) срок: {what}" + (" — проконтролируйте оплату" if is_pay else " — проконтролируйте исполнение"), "claim")
                add.append(f"rem3:{o['key']}:{o['due_on']}")
            late = o["days_late"]
            if late >= 1 and f"over1:{o['key']}:{o['due_on']}" not in sent:
                cs.notify_users(db, c, who + lawyers, f"Просрочка {late} дн.: {what}. На {CLAIM_DAY}-й день будет подготовлен черновик претензии", "claim")
                add.append(f"over1:{o['key']}:{o['due_on']}")
            if late >= CLAIM_DAY and (c.id, o["key"]) not in claimed_keys and f"claim:{o['key']}:{o['due_on']}" not in sent:
                cl = create(db, c, None, "outgoing", [o["key"]], source="auto")
                claimed_keys.add((c.id, o["key"]))
                t = totals(cl)
                cs.notify_users(db, c, lawyers + bosses + [c.curator_id or ""],
                                f"Черновик претензии {cl.number} по договору {c.number} ({cs.cp_display(db, c)}): {_money(t['total'])} {cl.currency}. Проверьте и передайте на подпись КД или ЗГД", "claim")
                add.append(f"claim:{o['key']}:{o['due_on']}")
        if add:
            c.notified = list(c.notified or []) + add
            n += len(add)
    # срок ответа на отправленную претензию истёк
    for cl in db.scalars(select(ContractClaim).where(ContractClaim.status.in_(("sent", "received")), ContractClaim.answer_due < today)).all():
        key = f"answer_due:{cl.answer_due}"
        if key in (cl.notified or []):
            continue
        c = db.get(Contract, cl.contract_id)
        txt = (f"Срок ответа на претензию {cl.number} ({cs.cp_display(db, c)}) истёк {cl.answer_due:%d.%m.%Y} — решите: суд, повторная претензия или закрыть" if cl.direction == "outgoing"
               else f"Истёк срок ответа на входящую претензию {cl.number} ({cs.cp_display(db, c)}) — {cl.answer_due:%d.%m.%Y}")
        cs.notify_users(db, c, lawyers + bosses + [c.curator_id or ""], txt, "claim")
        cl.notified = list(cl.notified or []) + [key]
        n += 1
    return n
