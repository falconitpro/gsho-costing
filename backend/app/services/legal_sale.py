"""Юридический блок, этап 4: клиентский пакет (сценарий Б) — рабочая копия договора заказчика с реквизитами БЕРГ-ХАУС,
протокол разногласий, техническая спецификация.

Принцип белого списка (раздел 10 ТЗ): в клиентские документы попадают только явно перечисленные поля — состав оборудования,
модель и исполнение, количество, характеристики, опции/комплектация. Цены завода, себестоимость, маржа, условия оплаты заводу
и (в режиме бренда компании) название завода не попадают по построению, а готовые файлы дополнительно проверяются стоп-словами.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Mm, Pt
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Client, Deal, DealVersion, Factory, LegalEntity, LocalOption, Position, PositionTechParam, Template
from . import legal_profiles as lp
from .execution_service import won_variant
from .kp_generator import ACCENT, BLACK, STOP_WORDS, _heading, _run, _table
from .legal_packages import party_from_profile, production_term_text, suggest_bank, warranty_text, _fmt_date
from .legal_rules import GROUPS, RULES_VERSION, SEVERITY, Finding, protocol_rows

SALE_DOCS = ("client_contract_copy", "disagreements_protocol", "tech_spec_client")
SALE_TITLES = {"client_contract_copy": "Рабочая копия договора заказчика", "disagreements_protocol": "Протокол разногласий", "tech_spec_client": "Техническая спецификация"}
FACTORY_STOP_EXTRA = ("unit_price", "factory_price", "закупоч", "завод-изготовител", "цена поставщика")


@dataclass
class SaleChoice:
    source_attachment_id: str = ""          # исходный договор заказчика (Attachment, категория contract_form)
    own_entity_id: Optional[str] = None     # продавец — всегда компания РФ (sells_to_clients); поле для проверки, не для выбора
    own_profile_id: Optional[str] = None
    own_bank_id: Optional[str] = None
    client_profile_id: Optional[str] = None
    templates: dict[str, Optional[str]] = field(default_factory=dict)
    approved_replacements: list[str] = field(default_factory=list)
    use_ai: bool = True
    notes: str = ""


def build_sale_context(db: Session, deal: Deal, ch: SaleChoice) -> dict:
    src: dict[str, str] = {}
    # продавец — компания РФ
    ent = next((e for e in db.scalars(select(LegalEntity).where(LegalEntity.is_active.is_(True))).all() if e.sells_to_clients), None)
    if ent is None:
        raise ValueError("Не задана компания группы, продающая клиентам (legal_entities.sells_to_clients)")
    profs = lp.ensure_profiles(db, "own", ent.id)
    prof = next((p for p in profs if p.id == ch.own_profile_id), None) or next((p for p in profs if p.is_default and p.is_active), profs[0] if profs else None)
    bank = None
    if prof:
        bank = next((b for b in prof.banks if b.id == ch.own_bank_id), None) or suggest_bank(list(prof.banks), "RUB", "RU")
    seller = party_from_profile(prof, bank) if prof else {}
    src["seller"] = f"карточка компании «{ent.name}»"
    src["seller.bank"] = f"банковский профиль «{bank.title}»" if bank else "не выбран"
    # покупатель — клиент: снимок из реквизитов контракта сделки, иначе профиль
    client = db.get(Client, deal.client_id)
    cprofs = lp.ensure_profiles(db, "client", client.id)
    cprof = next((p for p in cprofs if p.id == ch.client_profile_id), None) or next((p for p in cprofs if p.id == deal.client_requisites_id), None) or next((p for p in cprofs if p.is_default and p.is_active), cprofs[0] if cprofs else None)
    buyer = party_from_profile(cprof, suggest_bank(list(cprof.banks), "RUB")) if cprof else {}
    src["buyer"] = "юридический профиль клиента" + (" (выбран в реквизитах контракта)" if cprof and cprof.id == deal.client_requisites_id else " (по умолчанию)")

    # выигранное предложение и утверждённая версия
    v = won_variant(deal)
    ver = db.get(DealVersion, deal.approved_version_id) if deal.approved_version_id else None
    pos_ids = set(v.position_ids) if v and v.position_ids else {p.id for p in deal.positions}
    positions = [p for p in deal.positions if p.id in pos_ids]
    brand_company = deal.kp_brand == "company"
    factories = {f.id: f for f in db.scalars(select(Factory)).all()}
    local_by_pos: dict[str, list[LocalOption]] = {}
    for lo in db.scalars(select(LocalOption).where(LocalOption.deal_id == deal.id)).all():
        local_by_pos.setdefault(lo.position_id, []).append(lo)
    items, params, options_rows, units = [], [], [], 0
    forbidden_names: list[str] = []
    for pos in positions:
        f = factories.get(pos.factory_id)
        if brand_company and f:
            forbidden_names += [x for x in (f.name, f.short_name, f.name_zh) if x and len(x) > 3]
        from .offers_service import kp_model_name
        designation = (kp_model_name(pos, db) or pos.model) if brand_company else pos.designation
        qty = pos.qty or 1
        units += qty
        tps = db.scalars(select(PositionTechParam).where(PositionTechParam.position_id == pos.id).order_by(PositionTechParam.no)).all()
        for tp in tps:
            if tp.value_ru or tp.customer_requirement_ru:
                params.append({"n": len(params) + 1, "position": designation, "name": tp.name_ru, "name_zh": "", "unit": tp.unit, "requirement": tp.customer_requirement_ru, "value": tp.value_ru, "value_zh": "",
                               "comment": "реализуется в РФ" if tp.local_ru else ""})
        opts = [o.get("name") or o.get("code") for o in (pos.options or []) if isinstance(o, dict) and (o.get("name") or o.get("code"))]
        for lo in local_by_pos.get(pos.id, []):
            options_rows.append({"n": len(options_rows) + 1, "name": lo.name, "qty": lo.qty, "position": designation, "comment": "доукомплектование в РФ"})
        # белый список полей позиции — без цен, себестоимости, условий оплаты заводу
        items.append({"n": len(items) + 1, "name": pos.machine_type or "Оборудование", "trademark": "" if brand_company else pos.trademark, "model": designation if brand_company else pos.model,
                      "designation": designation, "machine_type": pos.machine_type, "execution": "", "qty": qty, "unit": "шт", "options": "; ".join(opts),
                      "production_term": production_term_text(pos.production_term, pos.production_days), "warranty": warranty_text(pos),
                      "params_text": "; ".join(f"{t.name_ru}: {t.value_ru}{(' ' + t.unit) if t.unit else ''}" for t in tps if t.value_ru)[:1000]})
    src["items"] = f"выигранное предложение «{v.name}»" if v else "позиции сделки" + (" · режим бренда компании: завод не раскрывается" if brand_company else "")
    src["params"] = "таблица технических характеристик (требование заказчика / согласованное значение)"
    # цена клиенту — из утверждённого предложения (только итог, для протокола/копии; в техспецификацию не идёт)
    price = {"amount": v.price_amount if v and v.price_amount is not None else v.price_rub if v else None, "currency": (v.price_currency if v and v.price_currency else "RUB") if v else "RUB"}
    first = positions[0] if positions else None
    dterm = ""
    if ver and ver.calc_result and v:
        try:
            promised = (ver.calc_result.get("terms") or {}).get(v.id, [{}])[0].get("delivery_term", {}).get("promised")
            dterm = f"{promised} календарных дней" if promised else ""
        except Exception:  # noqa: BLE001
            dterm = ""
    contract = {"number": deal.contract_number or "", "date": _fmt_date(deal.contract_signed_at), "client_contract_number": deal.contract_number or "", "client_contract_date": _fmt_date(deal.contract_signed_at),
                "currency": price["currency"], "deal_number": deal.number, "deal_version": str(ver.version_no) if ver else "", "delivery_term": dterm or (f"{first.delivery_days} календарных дней" if first and first.delivery_days else ""),
                "warranty": warranty_text(first) if first else "", "subject": "поставка горно-шахтного оборудования", "language": "ru"}
    rule_ctx = {"delivery_days": (first.production_days or 0) + (first.delivery_days or 0) if first else "", "warranty_months": first.warranty_months if first else "", "warranty_hours": first.warranty_hours if first else "",
                "seller": seller, "currency": first.currency if first else "CNY"}
    totals = {"items_count": len(items), "units_count": units, "amount": price["amount"], "currency": price["currency"]}
    return {"own_entity": {"id": ent.id, "name": ent.name}, "client": {"id": client.id, "name": client.name}, "seller": seller, "buyer": buyer, "contract": contract, "items": items, "params": params,
            "options": options_rows, "zip": [], "totals": totals, "brand_company": brand_company, "forbidden_names": sorted(set(forbidden_names)), "rule_ctx": rule_ctx,
            "deal_version": {"id": ver.id if ver else None, "no": ver.version_no if ver else None}, "variant": {"id": v.id if v else None, "name": v.name if v else ""}, "sources": src, "language": "ru",
            "package": {"created_on": _fmt_date(date.today()), "author": "", "version": "", "status": "черновик — проверить юристу"}}


def check_sale(ctx: dict, has_source: bool, templates: dict[str, Optional[Template]]) -> dict:
    errors, warnings = [], []
    s, b = ctx.get("seller") or {}, ctx.get("buyer") or {}
    if not has_source:
        errors.append("Не загружен договор заказчика (DOCX)")
    for k, t in (("name_ru", "наименование продавца"), ("tax_number", "ИНН продавца"), ("kpp", "КПП продавца"), ("reg_number", "ОГРН продавца"), ("legal_address", "юридический адрес продавца"), ("signatory", "подписант продавца"), ("signatory_basis", "основание полномочий подписанта продавца")):
        if not s.get(k):
            errors.append(f"Карточка компании: не заполнено {t}")
    if not (s.get("bank") or {}).get("account"):
        errors.append("Карточка компании: не выбран банковский профиль (счёт для поступлений)")
    elif not s["bank"].get("verified"):
        warnings.append("Банковские реквизиты продавца не подтверждены ФД / юристом")
    if not b.get("name_ru"):
        warnings.append("У клиента не заполнен юридический профиль — реквизиты покупателя в протоколе будут пустыми")
    if not ctx["items"]:
        errors.append("В выигранном предложении нет позиций")
    if not ctx["deal_version"]["id"]:
        errors.append("У сделки нет утверждённой версии расчёта")
    if not ctx["params"]:
        warnings.append("Технические характеристики не заполнены — техспецификация будет без таблицы параметров")
    if ctx["totals"]["amount"] is None:
        warnings.append("Цена клиенту не назначена в предложении")
    for kind in ("disagreements_protocol", "tech_spec_client"):
        t = templates.get(kind)
        if t is None:
            warnings.append(f"{SALE_TITLES[kind]}: шаблон не загружен — встроенный генератор")
        elif (t.variables or {}).get("unknown"):
            errors.append(f"{SALE_TITLES[kind]}: в шаблоне неизвестные переменные {', '.join(t.variables['unknown'][:5])}")
    return {"errors": errors, "warnings": warnings, "ok": not errors}


def assert_client_safe(path: str, forbidden_names: list[str]) -> None:
    """Клиентский документ: нет стоп-слов себестоимости/маржи и (в режиме бренда компании) названий завода."""
    doc = Document(path)
    texts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            texts.extend(c.text for c in row.cells)
    blob = "\n".join(texts).lower()
    leaked = [w for w in STOP_WORDS + FACTORY_STOP_EXTRA if w in blob]
    if leaked:
        raise RuntimeError(f"Клиентский документ содержит закрытые данные: {leaked}")
    found = [w for w in forbidden_names if w and w.lower() in blob]
    if found:
        raise RuntimeError(f"Документ под брендом компании содержит название завода: {found}")


# ------------------------------------------------------------------ встроенные генераторы

def _setup(doc):
    for s in doc.sections:
        s.left_margin = s.right_margin = Mm(20)
        s.top_margin = s.bottom_margin = Mm(18)
    doc.styles["Normal"].font.name = "Arial"
    doc.styles["Normal"].font.size = Pt(10)


def build_protocol(ctx: dict, findings: list[Finding], path: str) -> str:
    doc = Document()
    _setup(doc)
    c, s, b = ctx["contract"], ctx["seller"], ctx["buyer"]
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(p, "ПРОТОКОЛ РАЗНОГЛАСИЙ", bold=True, color=ACCENT, size=16)
    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(p2, f"к договору поставки № {c.get('client_contract_number') or '________'} от {c.get('client_contract_date') or '«___» ________ 20__ г.'}", color=BLACK, size=11)
    pd = doc.add_paragraph()
    _run(pd, f"ЧЕРНОВИК · сформирован автоматически (правила {RULES_VERSION}{' + ИИ' if any(f.source.startswith('ai') for f in findings) else ''}) · требует проверки юристом · сделка {c.get('deal_number', '')}", color=ACCENT, size=8)
    doc.add_paragraph(f"{s.get('name_ru') or '________'} (Поставщик) и {b.get('name_ru') or '________'} (Покупатель) согласовали следующие редакции пунктов договора:")
    rows = [[str(r["n"]), r["clause"], r["client_text"], r["our_text"], r["comment"]] for r in protocol_rows(findings)]
    if not rows:
        doc.add_paragraph("Разногласий по тексту договора не выявлено.")
    else:
        _table(doc, ["№", "Пункт", "Редакция Покупателя", "Редакция Поставщика", "Примечание"], rows)
    doc.add_paragraph()
    doc.add_paragraph("Во всём остальном, что не урегулировано настоящим Протоколом, Стороны руководствуются текстом Договора. Протокол является неотъемлемой частью Договора с момента подписания обеими Сторонами.")
    doc.add_paragraph()
    tb = doc.add_table(rows=1, cols=2)
    for i, (party, ttl) in enumerate(((s, "Поставщик"), (b, "Покупатель"))):
        cell = tb.rows[0].cells[i]
        cell.text = ""
        _run(cell.paragraphs[0], f"{ttl}:\n{party.get('name_ru') or ''}\n\n_______________ / {party.get('signatory') or '________'} /\n{party.get('signatory_position') or ''}\nМ.П.", color=BLACK, size=9)
    doc.save(path)
    return path


def build_tech_spec(ctx: dict, path: str) -> str:
    doc = Document()
    _setup(doc)
    c = ctx["contract"]
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(p, "ТЕХНИЧЕСКАЯ СПЕЦИФИКАЦИЯ", bold=True, color=ACCENT, size=16)
    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(p2, f"Приложение к договору поставки № {c.get('client_contract_number') or '________'} от {c.get('client_contract_date') or '«___» ________ 20__ г.'}", color=BLACK, size=11)
    pd = doc.add_paragraph()
    _run(pd, f"ЧЕРНОВИК · по предложению «{ctx['variant'].get('name') or '—'}», версия сделки {c.get('deal_version') or '—'} · требует проверки", color=ACCENT, size=8)
    _heading(doc, "1. Состав поставки", 12)
    _table(doc, ["№", "Оборудование", "Кол-во", "Комплектация / опции", "Срок изготовления", "Гарантия"],
           [[str(i["n"]), i["designation"], f"{i['qty']} {i['unit']}", i.get("options") or "—", i.get("production_term") or "—", i.get("warranty") or "—"] for i in ctx["items"]])
    if ctx.get("options"):
        doc.add_paragraph()
        _heading(doc, "2. Доукомплектование в Российской Федерации", 12)
        _table(doc, ["№", "Наименование", "Кол-во", "К оборудованию", "Примечание"], [[str(o["n"]), o["name"], str(o["qty"]), o["position"], o.get("comment", "")] for o in ctx["options"]])
    if ctx.get("params"):
        doc.add_paragraph()
        _heading(doc, f"{3 if ctx.get('options') else 2}. Технические характеристики", 12)
        _table(doc, ["№", "Оборудование", "Параметр", "Ед.", "Требование Покупателя", "Согласованное значение", "Примечание"],
               [[str(r["n"]), r["position"], r["name"], r.get("unit", ""), r.get("requirement") or "—", r.get("value") or "—", r.get("comment", "")] for r in ctx["params"]])
    doc.add_paragraph()
    doc.add_paragraph(f"Срок поставки: {c.get('delivery_term') or '—'}. Гарантия: {c.get('warranty') or '—'}. Характеристики, не указанные в настоящей спецификации, соответствуют технической документации изготовителя.")
    doc.add_paragraph()
    tb = doc.add_table(rows=1, cols=2)
    for i, (party, ttl) in enumerate(((ctx["seller"], "Поставщик"), (ctx["buyer"], "Покупатель"))):
        cell = tb.rows[0].cells[i]
        cell.text = ""
        _run(cell.paragraphs[0], f"{ttl}:\n{party.get('name_ru') or ''}\n\n_______________ / {party.get('signatory') or '________'} /\nМ.П.", color=BLACK, size=9)
    doc.save(path)
    return path


def render_sale_documents(db: Session, ctx: dict, findings: list[Finding], templates: dict[str, Optional[Template]], source_path: str, replacements, approved: set[str], out_dir: str, author: str, version_no: int) -> list[dict]:
    from .docx_fill import fill_docx
    from .legal_client_contract import make_working_copy
    out = []
    num = ctx["contract"].get("client_contract_number") or "б-н"
    # 1. рабочая копия договора заказчика
    wc = os.path.join(out_dir, f"Договор {num} — рабочая копия (реквизиты БЕРГ-ХАУС).docx".replace("/", "-"))
    rep = make_working_copy(source_path, wc, replacements, approved, header_note="РАБОЧАЯ КОПИЯ · реквизиты Поставщика подставлены из CRM · проверить юристу")
    out.append({"kind": "client_contract_copy", "title": SALE_TITLES["client_contract_copy"], "path": wc, "unresolved": [], "leftovers": [], "template": None, "report": rep})
    # 2. протокол разногласий
    data = {k: ctx[k] for k in ("buyer", "seller", "contract", "totals", "items", "params", "options", "zip")}
    data["clauses"] = protocol_rows(findings)
    data["package"] = {"created_on": _fmt_date(date.today()), "author": author, "version": str(version_no), "status": "черновик — проверить юристу"}
    pr = os.path.join(out_dir, f"Протокол разногласий к договору {num}.docx".replace("/", "-"))
    t = templates.get("disagreements_protocol")
    if t is not None and os.path.isfile(t.file_path):
        res = fill_docx(t.file_path, pr, data)
        out.append({"kind": "disagreements_protocol", "title": SALE_TITLES["disagreements_protocol"], "path": pr, "unresolved": res.unresolved, "leftovers": res.leftovers, "template": {"id": t.id, "version": t.version, "title": t.title}})
    else:
        build_protocol(ctx, findings, pr)
        out.append({"kind": "disagreements_protocol", "title": SALE_TITLES["disagreements_protocol"], "path": pr, "unresolved": [], "leftovers": [], "template": None})
    # 3. техническая спецификация (белый список + проверка стоп-слов)
    ts = os.path.join(out_dir, f"Техническая спецификация к договору {num}.docx".replace("/", "-"))
    t = templates.get("tech_spec_client")
    safe = {"buyer": data["buyer"], "seller": data["seller"], "contract": {k: v for k, v in ctx["contract"].items()}, "items": [{k: v for k, v in i.items()} for i in ctx["items"]], "params": ctx["params"], "options": ctx["options"],
            "totals": {"items_count": ctx["totals"]["items_count"], "units_count": ctx["totals"]["units_count"]}, "package": data["package"], "zip": []}
    if t is not None and os.path.isfile(t.file_path):
        res = fill_docx(t.file_path, ts, safe)
        out.append({"kind": "tech_spec_client", "title": SALE_TITLES["tech_spec_client"], "path": ts, "unresolved": res.unresolved, "leftovers": res.leftovers, "template": {"id": t.id, "version": t.version, "title": t.title}})
    else:
        build_tech_spec(ctx, ts)
        out.append({"kind": "tech_spec_client", "title": SALE_TITLES["tech_spec_client"], "path": ts, "unresolved": [], "leftovers": [], "template": None})
    assert_client_safe(ts, ctx.get("forbidden_names") or [])
    return out
