"""Юридический блок, этап 3: закупочный пакет — договор с поставщиком, приложение № 1 (спецификация оборудования),
приложение № 2 (спецификация ЗИП). Данные берутся из сделки, позиций, профилей и банков; ничего не вводится повторно.

Ключевые правила (ТЗ, разделы 3, 7, 11):
- на каждого поставщика в сделке — отдельный пакет; позиции разных поставщиков не смешиваются;
- в договор с иностранным поставщиком не входят опции, реализуемые в России (local_options);
- ЗИП в договор идёт только из подтверждённого договорного комплекта (LegalZipSet), а не из сервисного расчёта напрямую;
- в документе хранится снимок выбранных реквизитов — правка справочников старые пакеты не меняет;
- пакет с блокирующими ошибками не формируется.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..calc.types import EVENT_TITLES, PaymentEvent
from ..models import (BankProfile, Deal, DealVersion, Factory, LegalEntity, LegalPackage, LegalProfile, LegalZipSet, LocalOption, Position, PositionTechParam,
                      RfqImport, Template, User)
from . import legal_profiles as lp
from .legal_templates import LEGAL_KINDS

PURCHASE_DOCS = ("purchase_contract", "spec_equipment", "spec_zip")
DOC_TITLES = {"purchase_contract": "Договор поставки", "spec_equipment": "Приложение № 1 — Спецификация оборудования", "spec_zip": "Приложение № 2 — Спецификация ЗИП"}
STATUS_TITLES = {"needs_data": "требуется заполнить данные", "draft": "черновик", "lawyer_review": "на проверке юриста", "commercial_review": "на коммерческом согласовании",
                 "approved": "утверждён", "signed": "подписан", "superseded": "заменён новой версией", "archived": "архив"}
TRANSITIONS = {"draft": ["lawyer_review", "archived"], "needs_data": ["archived"], "lawyer_review": ["commercial_review", "draft", "archived"],
               "commercial_review": ["approved", "draft", "archived"], "approved": ["signed", "draft", "archived"], "signed": ["archived"], "superseded": [], "archived": []}
TRANSITION_ROLES = {"lawyer_review": {"manager", "lawyer", "cd", "gd"}, "commercial_review": {"lawyer", "cd", "gd"}, "approved": {"cd", "gd"}, "signed": {"manager", "cd", "gd"},
                    "draft": {"manager", "lawyer", "cd", "gd"}, "archived": {"cd", "gd", "lawyer"}}

# язык закупочных документов по стране поставщика (решение владельца 07.11.2026)
DEFAULT_LANGUAGE = {"CN": "ru-zh", "KG": "ru"}
CONTRACT_KIND_BY_COUNTRY = {"CN": "purchase_contract_cn", "KG": "purchase_contract_kg"}

UNIT_TITLES = {"calendar_days": "календарных дней", "working_days": "рабочих дней", "weeks": "недель"}
START_TITLES = {"factory_contract": "с даты подписания договора", "factory_prepayment": "с даты получения аванса"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_date(d: Optional[date]) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


def pay_text(lines: list[dict]) -> str:
    out = []
    for l in lines or []:
        ev = l.get("event")
        try:
            title = EVENT_TITLES[PaymentEvent(ev)]
        except Exception:  # noqa: BLE001
            title = str(ev or "")
        share = f"{l.get('share_pct')} %" if l.get("share_pct") is not None else f"{l.get('amount')}"
        off = l.get("offset_days") or 0
        kind = "раб. дн." if l.get("offset_kind") == "working" else "дн."
        out.append(f"{share} — {title}" + (f" + {off} {kind}" if off else "") + (" (аккредитив)" if l.get("via") == "lc" else ""))
    return "; ".join(out)


def production_term_text(term: dict | None, default_days: int | None) -> str:
    t = term or {}
    if t.get("unit") == "date" and t.get("fixed_date"):
        return f"до {t['fixed_date']}"
    v = t.get("value") or default_days
    if not v:
        return ""
    return f"{v:g} {UNIT_TITLES.get(t.get('unit') or 'calendar_days', 'дней')} {START_TITLES.get(t.get('start_event') or 'factory_contract', '')}".strip()


def warranty_text(p: Position) -> str:
    parts = []
    if p.warranty_months:
        parts.append(f"{p.warranty_months} мес.")
    if p.warranty_hours:
        parts.append(f"{p.warranty_hours:g} м·ч")
    return " или ".join(parts) + (" (что наступит раньше)" if len(parts) == 2 else "")


# ------------------------------------------------------------------ стороны

def party_from_profile(p: LegalProfile, bank: Optional[BankProfile]) -> dict:
    n = p.names or {}
    ru, en, zh = n.get("ru") or {}, n.get("en") or {}, n.get("zh") or {}
    c0 = next((c for c in (p.contacts or []) if isinstance(c, dict)), {})
    b = bank
    return {"profile_id": p.id, "name_ru": ru.get("full") or ru.get("short") or en.get("full") or "", "short_ru": ru.get("short") or "", "name_en": en.get("full") or "", "name_zh": zh.get("full") or "",
            "country": p.country, "reg_number": p.reg_number, "tax_number": p.tax_number, "kpp": p.kpp, "legal_address": p.legal_address, "actual_address": p.actual_address,
            "postal_address": p.postal_address, "director": p.director, "signatory": p.signatory_name, "signatory_position": p.signatory_position, "signatory_basis": p.signatory_basis,
            "phone": c0.get("phone", ""), "email": c0.get("email", ""),
            "bank": {"id": b.id, "title": b.title, "beneficiary": b.beneficiary or ru.get("full") or en.get("full") or "", "bank_name": b.bank_name, "bank_address": b.bank_address, "account": b.account,
                     "bik": b.bik, "swift": b.swift, "corr_account": b.corr_account, "corr_bank": b.corr_bank, "currency": b.currency, "verified": b.verified} if b else
                    {"id": None, "title": "", "beneficiary": "", "bank_name": "", "bank_address": "", "account": "", "bik": "", "swift": "", "corr_account": "", "corr_bank": "", "currency": "", "verified": False}}


def suggest_bank(banks: list[BankProfile], currency: str, country: str = "") -> Optional[BankProfile]:
    """Подсказка банковского профиля: действующий, подтверждённый, в валюте договора; затем просто в валюте; затем любой действующий."""
    active = [b for b in banks if b.status == "active"]
    for pred in (lambda b: b.currency == currency and b.verified, lambda b: b.currency == currency, lambda b: b.verified, lambda b: True):
        hit = next((b for b in active if pred(b)), None)
        if hit:
            return hit
    return None


# ------------------------------------------------------------------ договорный ЗИП

def zip_set_for(db: Session, deal: Deal, factory_id: str, create: bool = True) -> Optional[LegalZipSet]:
    zs = db.scalar(select(LegalZipSet).where(LegalZipSet.deal_id == deal.id, LegalZipSet.factory_id == factory_id))
    if zs is None and create:
        zs = LegalZipSet(deal_id=deal.id, factory_id=factory_id, lines=initial_zip_lines(db, deal, factory_id))
        db.add(zs)
        db.flush()
    return zs


def initial_zip_lines(db: Session, deal: Deal, factory_id: str) -> list[dict]:
    """Предзаполнение: ЗИП из позиций (сервисный блок, kind supply/to) и лист «ЗИП на ТО» из RFQ. Строки не выбраны, пока их не подтвердят."""
    import uuid
    lines: list[dict] = []
    for pos in deal.positions:
        if pos.factory_id != factory_id:
            continue
        for z in pos.zip_items or []:
            lines.append({"id": uuid.uuid4().hex, "position_id": pos.id, "position": pos.designation, "article": z.get("article", ""), "name": z.get("name", ""), "name_zh": "", "assembly": z.get("assembly", ""),
                          "unit": z.get("unit") or "шт", "qty": float(z.get("qty") or 1), "unit_price": z.get("price"), "currency": (z.get("currency") or pos.currency or "CNY").upper(),
                          "interval_hours": z.get("interval_hours"), "kind": z.get("kind", "supply"), "lead_days": z.get("lead_days"), "included": z.get("kind", "supply") == "supply",
                          "selected": z.get("kind", "supply") == "supply", "source": "service", "comment": z.get("comment", "")})
        if pos.rfq_import_id:
            imp = db.get(RfqImport, pos.rfq_import_id)
            for sp in (getattr(imp, "parsed", None) or {}).get("spare_parts", []) if imp else []:
                if not isinstance(sp, dict) or not (sp.get("article") or sp.get("name")):
                    continue
                lines.append({"id": uuid.uuid4().hex, "position_id": pos.id, "position": pos.designation, "article": str(sp.get("article") or ""), "name": str(sp.get("name") or sp.get("name_ru") or ""),
                              "name_zh": str(sp.get("name_zh") or ""), "assembly": str(sp.get("assembly") or ""), "unit": str(sp.get("unit") or "шт"), "qty": float(sp.get("qty") or 1),
                              "unit_price": sp.get("price"), "currency": (sp.get("currency") or pos.currency or "CNY").upper(), "interval_hours": sp.get("interval_hours"), "kind": "to",
                              "lead_days": sp.get("lead_days"), "included": False, "selected": False, "source": "rfq", "comment": ""})
    return lines


def zip_rows(zs: Optional[LegalZipSet]) -> list[dict]:
    rows = []
    for l in (zs.lines if zs else []):
        if not l.get("selected"):
            continue
        qty = float(l.get("qty") or 0)
        price = l.get("unit_price")
        amount = round(qty * float(price), 2) if price not in (None, "") else None
        rows.append({"n": len(rows) + 1, "article": l.get("article", ""), "name": l.get("name", ""), "name_zh": l.get("name_zh", ""), "assembly": l.get("assembly", ""), "unit": l.get("unit") or "шт",
                     "qty": qty, "unit_price": price, "amount": amount, "currency": l.get("currency", ""), "interval_hours": l.get("interval_hours"),
                     "kind": "ЗИП на ТО" if l.get("kind") == "to" else "в комплекте поставки", "lead_days": l.get("lead_days"), "included": "да" if l.get("included") else "нет", "position": l.get("position", "")})
    return rows


# ------------------------------------------------------------------ контекст закупки

@dataclass
class Choice:
    factory_id: str
    own_entity_id: Optional[str] = None
    own_profile_id: Optional[str] = None
    own_bank_id: Optional[str] = None
    cp_profile_id: Optional[str] = None
    cp_bank_id: Optional[str] = None
    language: str = ""
    templates: dict[str, Optional[str]] = field(default_factory=dict)    # doc_kind → template id ("" → встроенный генератор)
    contract_number: str = ""
    contract_date: Optional[date] = None
    place: str = ""
    framework_contract_id: Optional[str] = None      # оформить сделку спецификацией к рамочному договору (D-169)


def suppliers_in_deal(db: Session, deal: Deal) -> list[dict]:
    out: dict[str, dict] = {}
    for pos in deal.positions:
        if not pos.factory_id:
            continue
        f = db.get(Factory, pos.factory_id)
        o = out.setdefault(pos.factory_id, {"id": pos.factory_id, "name": f.name if f else "", "short_name": (f.short_name or f.name) if f else "", "country": (f.country if f else "CN") or "CN",
                                              "positions": 0, "units": 0, "currency": pos.currency, "amount": 0.0})
        o["positions"] += 1
        o["units"] += pos.qty or 0
        o["amount"] = round(o["amount"] + ((pos.unit_price or 0) + (pos.options_price or 0)) * (pos.qty or 1), 2)
        if f is not None and f.supplier_status == "group_company":
            o["group_company"] = True
    # конечные поставщики через компанию группы: договор от имени компании группы (D-160)
    from .mirror_offers import mirror_positions, mirror_suppliers
    via = mirror_suppliers(db, deal)
    for mp in mirror_positions(db, deal):
        if mp.factory_id in out and not out[mp.factory_id].get("via_group"):
            continue
        f = db.get(Factory, mp.factory_id)
        o = out.setdefault(mp.factory_id, {"id": mp.factory_id, "name": f.name if f else "", "short_name": (f.short_name or f.name) if f else "", "country": (f.country if f else "CN") or "CN",
                                             "positions": 0, "units": 0, "currency": mp.currency, "amount": 0.0, "via_group": via.get(mp.factory_id)})
        o["positions"] += 1
        o["units"] += mp.qty or 0
        o["amount"] = round(o["amount"] + (mp.unit_price or 0) * (mp.qty or 1), 2)
    return list(out.values())


def active_template(db: Session, kind: str, language: str) -> Optional[Template]:
    t = db.scalar(select(Template).where(Template.kind == kind, Template.language == language, Template.is_active.is_(True), Template.is_example.is_(False)).order_by(Template.version.desc()))
    if t is None:
        t = db.scalar(select(Template).where(Template.kind == kind, Template.is_active.is_(True), Template.is_example.is_(False)).order_by(Template.version.desc()))
    return t


def build_context(db: Session, deal: Deal, ch: Choice) -> dict:
    """Собирает все данные пакета + карту источников («откуда взято»). Не пишет в базу (кроме автосоздания профилей)."""
    src: dict[str, str] = {}
    factory = db.get(Factory, ch.factory_id)
    if factory is None:
        raise ValueError("Поставщик не найден")
    positions = [p for p in deal.positions if p.factory_id == ch.factory_id]
    mirror = None
    if not positions:   # конечный поставщик через компанию группы: договор компании группы с ним по его ответу (D-160)
        from .mirror_offers import mirror_positions, mirror_suppliers
        positions = mirror_positions(db, deal, ch.factory_id)
        mirror = mirror_suppliers(db, deal).get(ch.factory_id) if positions else None
    currency = (positions[0].currency if positions else factory.default_currency or "CNY").upper()
    country = (factory.country or "CN").upper()
    language = ch.language or DEFAULT_LANGUAGE.get(country, "ru-en")

    # компания группы — покупатель (по умолчанию RU; выбор другой — только ФД/ЗГД/КД, проверяется в API)
    entities = db.scalars(select(LegalEntity).where(LegalEntity.is_active.is_(True))).all()
    own_entity = db.get(LegalEntity, ch.own_entity_id) if ch.own_entity_id else (
        db.get(LegalEntity, mirror["entity_id"]) if mirror and mirror.get("entity_id") else next((e for e in entities if e.sells_to_clients), entities[0] if entities else None))
    own_profiles = lp.ensure_profiles(db, "own", own_entity.id) if own_entity else []
    own_profile = next((p for p in own_profiles if p.id == ch.own_profile_id), None) or next((p for p in own_profiles if p.is_default and p.is_active), own_profiles[0] if own_profiles else None)
    own_bank = None
    if own_profile:
        own_bank = next((b for b in own_profile.banks if b.id == ch.own_bank_id), None) or suggest_bank(list(own_profile.banks), currency)
    cp_profiles = lp.ensure_profiles(db, "factory", factory.id)
    cp_profile = next((p for p in cp_profiles if p.id == ch.cp_profile_id), None) or next((p for p in cp_profiles if p.is_default and p.is_active), cp_profiles[0] if cp_profiles else None)
    cp_bank = None
    if cp_profile:
        cp_bank = next((b for b in cp_profile.banks if b.id == ch.cp_bank_id), None) or suggest_bank(list(cp_profile.banks), currency)
    buyer = party_from_profile(own_profile, own_bank) if own_profile else {}
    seller = party_from_profile(cp_profile, cp_bank) if cp_profile else {}
    src["buyer"] = f"карточка компании группы «{own_entity.name}»" if own_entity else "—"
    src["seller"] = f"юридический профиль поставщика «{factory.short_name or factory.name}»"
    src["buyer.bank"] = f"банковский профиль «{own_bank.title}»" if own_bank else "не выбран"
    src["seller.bank"] = f"банковский профиль поставщика «{cp_bank.title}»" if cp_bank else "не выбран"

    # позиции выбранного поставщика; опции РФ исключаются
    local_by_pos: dict[str, list[LocalOption]] = {}
    for lo in db.scalars(select(LocalOption).where(LocalOption.deal_id == deal.id)).all():
        local_by_pos.setdefault(lo.position_id, []).append(lo)
    items, params_rows, units = [], [], 0
    eq_amount = 0.0
    for pos in positions:
        qty = pos.qty or 1
        unit_price = float(pos.unit_price or 0) + float(pos.options_price or 0) / qty if pos.options_price else float(pos.unit_price or 0)
        amount = round(unit_price * qty, 2)
        eq_amount += amount
        units += qty
        opts = [o.get("name") or o.get("code") for o in (pos.options or []) if isinstance(o, dict) and (o.get("name") or o.get("code"))]
        local_names = {lo.name for lo in local_by_pos.get(pos.id, [])}
        opts = [o for o in opts if o not in local_names]
        tps = db.scalars(select(PositionTechParam).where(PositionTechParam.position_id == pos.id, PositionTechParam.local_ru.is_(False)).order_by(PositionTechParam.no)).all()
        for tp in tps:
            if tp.value_ru or tp.value_zh or tp.customer_requirement_ru:
                params_rows.append({"n": len(params_rows) + 1, "position": pos.designation, "name": tp.name_ru, "name_zh": tp.name_zh, "unit": tp.unit, "requirement": tp.customer_requirement_ru,
                                    "value": tp.value_ru, "value_zh": tp.value_zh, "comment": tp.comment_ru})
        items.append({"n": len(items) + 1, "position_id": pos.id, "name": pos.machine_type or "Оборудование", "trademark": pos.trademark, "model": pos.model, "execution": "", "machine_type": pos.machine_type,
                      "designation": pos.designation, "qty": qty, "unit": "шт", "unit_price": round(unit_price, 2), "amount": amount, "currency": (pos.currency or currency).upper(),
                      "options": "; ".join(opts), "hs_code": (pos.logistics or {}).get("hs_code", ""), "production_term": production_term_text(pos.production_term, pos.production_days),
                      "warranty": warranty_text(pos), "name_zh": "", "params_text": "; ".join(f"{t.name_ru}: {t.value_ru}{(' ' + t.unit) if t.unit else ''}" for t in tps if t.value_ru)[:1000],
                      "local_options_excluded": sorted(local_names)})
    src["items"] = ("позиции сделки через компанию группы: цена, валюта, базис, срок и оплата — из ответа поставщика компании группы; опции РФ исключены" if mirror
                    else "позиции сделки этого поставщика (цена поставщика, без наценок); опции РФ исключены")
    src["params"] = "таблица технических характеристик позиций (без опций РФ)"

    zs = zip_set_for(db, deal, factory.id, create=False)
    zrows = zip_rows(zs)
    zip_amount = round(sum(r["amount"] or 0 for r in zrows), 2)
    src["zip"] = "договорный комплект ЗИП (подтверждённый)" if zs and zs.confirmed_at else "договорный комплект ЗИП не подтверждён"
    included = (zs.pricing_mode if zs else "included") == "included"
    total = round(eq_amount + (0 if included else zip_amount), 2)

    ver = db.get(DealVersion, deal.approved_version_id) if deal.approved_version_id else None
    first = positions[0] if positions else None
    own_number, own_date = (ch.contract_number, ch.contract_date) if mirror else (ch.contract_number or deal.factory_contract_number, ch.contract_date or deal.factory_contract_signed_at)
    contract = {"number": own_number or "", "date": _fmt_date(own_date), "place": ch.place or "",
                "subject": "поставка горно-шахтного оборудования и запасных частей", "currency": currency, "basis": first.basis if first else (factory.default_basis or ""),
                "delivery_point": first.delivery_point if first else "", "production_term": production_term_text(first.production_term, first.production_days) if first else "",
                "delivery_term": "", "warranty": warranty_text(first) if first else "", "payment_terms": pay_text(first.factory_payments or first.factory_payments_offer) if first else "",
                "language": language, "deal_number": deal.number, "deal_version": str(ver.version_no) if ver else "", "client_contract_number": deal.contract_number or "",
                "client_contract_date": _fmt_date(deal.contract_signed_at)}
    src["contract.number"] = "реквизиты контракта сделки (номер договора с заводом)" if deal.factory_contract_number else "не внесён — заполнится при подписании"
    framework = _framework_ref(db, deal, ch.framework_contract_id, factory.id, "expense", own_entity.id if own_entity else None) if ch.framework_contract_id else None
    if framework:
        contract.update({"number": framework["number"], "date": framework["date"], "spec_number": framework["spec_number"]})
        src["contract.number"] = f"рамочный договор {framework['number']}; спецификация {framework['spec_number']}"
    src["contract.payment_terms"] = "условия оплаты заводу из позиции (КД / ответ завода)"
    totals = {"equipment_amount": round(eq_amount, 2), "zip_amount": zip_amount, "options_amount": 0.0, "amount": total, "amount_words": amount_words(total, currency), "currency": currency,
              "vat_text": "НДС не облагается (экспорт)" if country != "RU" else "в том числе НДС 20 %", "items_count": len(items), "units_count": units, "zip_included": included}
    return {"factory": {"id": factory.id, "name": factory.name, "short_name": factory.short_name or factory.name, "country": country}, "own_entity": {"id": own_entity.id, "name": own_entity.name, "code": own_entity.code} if own_entity else None,
            "buyer": buyer, "seller": seller, "contract": contract, "items": items, "params": params_rows, "zip": zrows, "zip_set": {"id": zs.id if zs else None, "confirmed": bool(zs and zs.confirmed_at), "pricing_mode": zs.pricing_mode if zs else "included", "lines_total": len(zs.lines) if zs else 0},
            "options": [], "totals": totals, "deal_version": {"id": ver.id if ver else None, "no": ver.version_no if ver else None}, "language": language, "sources": src,
            "package": {"created_on": _fmt_date(date.today()), "author": "", "version": "", "status": STATUS_TITLES["draft"]}, "mirror": mirror, "framework": framework}


def amount_words(v: float, currency: str) -> str:
    """Сумма прописью (RU) — упрощённо: целая часть числом с разрядами + валюта; полная пропись — при доработке шаблонов."""
    cur = {"CNY": "китайских юаней", "USD": "долларов США", "EUR": "евро", "RUB": "рублей", "KGS": "киргизских сомов"}.get(currency, currency)
    whole = int(v)
    frac = int(round((v - whole) * 100))
    return f"{whole:,}".replace(",", " ") + f" {cur} {frac:02d}/100"


# ------------------------------------------------------------------ проверки перед формированием (раздел 11 ТЗ)

def check_purchase(ctx: dict, templates: dict[str, Optional[Template]]) -> dict:
    errors, warnings = [], []
    b, s = ctx.get("buyer") or {}, ctx.get("seller") or {}
    if not b:
        errors.append("Не выбрана компания группы — покупатель (карточка БЕРГ-ХАУС)")
    else:
        for k, t in (("name_ru", "наименование покупателя"), ("tax_number", "налоговый номер покупателя (ИНН)"), ("legal_address", "юридический адрес покупателя"), ("signatory", "подписант покупателя"), ("signatory_basis", "основание полномочий подписанта покупателя")):
            if not b.get(k):
                errors.append(f"Не заполнено: {t}")
        if not b.get("bank", {}).get("account"):
            errors.append("Не выбран банковский профиль покупателя (счёт)")
        elif not b["bank"].get("verified"):
            warnings.append("Банковские реквизиты покупателя не подтверждены ФД / юристом")
    if not s:
        errors.append("У поставщика нет юридического профиля")
    else:
        if not (s.get("name_en") or s.get("name_ru") or s.get("name_zh")):
            errors.append("Не заполнено наименование поставщика")
        for k, t in (("reg_number", "регистрационный номер поставщика"), ("legal_address", "адрес поставщика"), ("signatory", "подписант поставщика")):
            if not s.get(k):
                (errors if k != "signatory" else warnings).append(f"Не заполнено: {t}")
        if not s.get("bank", {}).get("account"):
            errors.append("Не выбран банковский профиль поставщика (счёт для оплаты)")
        elif not s["bank"].get("verified"):
            warnings.append("Банковские реквизиты поставщика не подтверждены — риск подмены реквизитов")
        if s.get("bank", {}).get("currency") and s["bank"]["currency"] != ctx["contract"]["currency"]:
            warnings.append(f"Валюта счёта поставщика ({s['bank']['currency']}) не совпадает с валютой договора ({ctx['contract']['currency']})")
    items = ctx.get("items") or []
    if not items:
        errors.append("У выбранного поставщика нет позиций в сделке")
    for it in items:
        if not it.get("qty") or not it.get("unit_price"):
            errors.append(f"Позиция «{it.get('designation')}»: не заполнены количество или цена")
        if it.get("currency") != ctx["contract"]["currency"]:
            errors.append(f"Позиция «{it.get('designation')}»: валюта {it.get('currency')} отличается от валюты договора {ctx['contract']['currency']}")
        if not it.get("production_term"):
            warnings.append(f"Позиция «{it.get('designation')}»: не указан срок производства")
        if not it.get("warranty"):
            warnings.append(f"Позиция «{it.get('designation')}»: не указана гарантия")
        if not it.get("hs_code"):
            warnings.append(f"Позиция «{it.get('designation')}»: не указан код ТН ВЭД (логист)")
    if not ctx["deal_version"]["id"]:
        errors.append("У сделки нет утверждённой версии расчёта — пакет привязывается к версии сделки")
    if not ctx["zip_set"]["confirmed"]:
        errors.append("Договорный комплект ЗИП не подтверждён (экран «Договорный комплект ЗИП»)")
    if not ctx["contract"]["payment_terms"]:
        errors.append("Не заданы условия оплаты поставщику (позиция → условия оплаты заводу)")
    if not ctx["contract"]["basis"]:
        warnings.append("Не указан базис поставки")
    if not ctx["contract"]["number"]:
        warnings.append("Номер договора не внесён — в документе останется пустым до подписания")
    t_sum = round(sum(i["amount"] for i in items), 2)
    if abs(t_sum - ctx["totals"]["equipment_amount"]) > 0.01:
        errors.append("Итог спецификации оборудования не совпадает с суммой договора")
    for kind in PURCHASE_DOCS:
        t = templates.get(kind)
        if t is None:
            warnings.append(f"{DOC_TITLES[kind]}: шаблон не загружен — используется встроенный генератор")
        else:
            unknown = (t.variables or {}).get("unknown") or []
            if unknown:
                errors.append(f"{DOC_TITLES[kind]}: в шаблоне неизвестные переменные {', '.join(unknown[:5])}")
    return {"errors": errors, "warnings": warnings, "ok": not errors}


# ------------------------------------------------------------------ формирование документов

def _flatten_for_template(ctx: dict, author: str, version_no: int) -> dict:
    data = {k: ctx[k] for k in ("buyer", "seller", "contract", "totals", "items", "params", "zip", "options")}
    data["package"] = {"created_on": _fmt_date(date.today()), "author": author, "version": str(version_no), "status": STATUS_TITLES["draft"]}
    return data


def render_documents(db: Session, ctx: dict, templates: dict[str, Optional[Template]], out_dir: str, author: str, version_no: int) -> list[dict]:
    """Три DOCX: по загруженным шаблонам (docx_fill) или встроенным генератором. Возвращает [{kind, title, path, unresolved}]."""
    from .docx_fill import fill_docx
    from .legal_builtin import build_purchase_contract, build_spec_equipment, build_spec_zip
    data = _flatten_for_template(ctx, author, version_no)
    out = []
    num = ctx["contract"]["number"] or "б-н"
    names = {"purchase_contract": f"Договор {num} — {ctx['factory']['short_name']}.docx", "spec_equipment": f"Приложение 1 — Спецификация оборудования — {num}.docx", "spec_zip": f"Приложение 2 — Спецификация ЗИП — {num}.docx"}
    builders = {"purchase_contract": build_purchase_contract, "spec_equipment": build_spec_equipment, "spec_zip": build_spec_zip}
    if ctx.get("framework"):    # спецификация к рамочному договору: без нового договора, приложения — к спецификации (D-169)
        sp = ctx["framework"]["spec_number"]
        names.update({"spec_equipment": f"Спецификация {sp} — оборудование — к договору {num}.docx", "spec_zip": f"Спецификация {sp} — ЗИП — к договору {num}.docx"})
    for kind in PURCHASE_DOCS:
        if kind == "purchase_contract" and ctx.get("framework"):
            continue
        path = os.path.join(out_dir, names[kind].replace("/", "-"))
        t = templates.get(kind)
        if t is not None and os.path.isfile(t.file_path):
            res = fill_docx(t.file_path, path, data)
            out.append({"kind": kind, "title": DOC_TITLES[kind], "path": path, "unresolved": res.unresolved, "leftovers": res.leftovers, "template": {"id": t.id, "version": t.version, "title": t.title}})
        else:
            builders[kind](ctx, path)
            out.append({"kind": kind, "title": DOC_TITLES[kind], "path": path, "unresolved": [], "leftovers": [], "template": None})
    return out


def package_payload(pkg: LegalPackage, names: dict[str, str]) -> dict:
    return {"id": pkg.id, "deal_id": pkg.deal_id, "kind": pkg.kind, "counterparty_kind": pkg.counterparty_kind, "counterparty_id": pkg.counterparty_id, "version_no": pkg.version_no,
            "deal_version_no": pkg.deal_version_no, "own_entity_id": pkg.own_entity_id, "language": pkg.language, "templates": pkg.templates, "status": pkg.status, "status_title": STATUS_TITLES.get(pkg.status, pkg.status),
            "stage_code": pkg.stage_code, "checks": pkg.checks, "files": pkg.files, "approvals": [{**a, "by_name": names.get(a.get("by") or "", "")} for a in reversed(pkg.approvals or [])],
            "notes": pkg.notes, "created_by_name": names.get(pkg.created_by or "", ""), "created_at": pkg.created_at, "superseded_by": pkg.superseded_by,
            "counterparty_name": (pkg.snapshot or {}).get("factory", {}).get("short_name") or (pkg.snapshot or {}).get("client", {}).get("name", ""),
            "totals": (pkg.snapshot or {}).get("totals", {}), "contract": (pkg.snapshot or {}).get("contract", {}), "next_statuses": TRANSITIONS.get(pkg.status, []),
            "mirror": (pkg.snapshot or {}).get("mirror")}


def user_names(db: Session) -> dict[str, str]:
    return {u.id: u.display_name for u in db.scalars(select(User)).all()}



def _framework_ref(db: Session, deal: Deal, fw_id: str, cp_id: str, direction: str, own_entity_id: Optional[str]) -> dict:
    """Рамочный договор для спецификации: тот же контрагент, направление и компания группы; номер спецификации — существующей или следующей."""
    from ..models import Contract
    from .legal_contracts import next_child_number, spec_for_deal
    fw = db.get(Contract, fw_id)
    if fw is None or not fw.is_framework or fw.doc_type != "contract":
        raise HTTPException(422, "Рамочный договор не найден")
    if fw.cp_id != cp_id or fw.direction != direction or (own_entity_id and fw.own_entity_id and fw.own_entity_id != own_entity_id):
        raise HTTPException(422, "Рамочный договор заключён с другим контрагентом или другой компанией группы")
    if fw.status in ("terminated", "closed"):
        raise HTTPException(422, "Рамочный договор расторгнут или закрыт")
    spec = spec_for_deal(db, fw, deal.id)
    return {"id": fw.id, "number": fw.cp_number or fw.number, "registry_number": fw.number, "date": _fmt_date(fw.signed_on),
            "status": fw.status, "spec_number": spec.number if spec else next_child_number(db, fw, "specification"), "spec_id": spec.id if spec else None}
