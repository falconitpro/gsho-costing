"""Встроенные генераторы закупочных документов (юридический блок, этап 3) — работают, пока не загружены шаблоны владельца.

Договор поставки с иностранным поставщиком (БЕРГ-ХАУС — покупатель), приложение № 1 (спецификация оборудования),
приложение № 2 (спецификация ЗИП). Фирменный стиль: акцент #FF3131, Intro/Arial. Для языка ru-zh / ru-en заголовки разделов
и шапки таблиц даются на двух языках; текст статей — русский (юрист правит DOCX или загружает свой шаблон).
"""
from __future__ import annotations

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Mm, Pt

from .kp_generator import ACCENT, BLACK, _heading, _run, _table

ZH = {"contract": "供货合同", "subject": "1. 合同标的", "price": "2. 价格与合同总额", "payment": "3. 付款条件", "terms": "4. 生产与交货期", "delivery": "5. 交货条件",
      "quality": "6. 质量与保修", "acceptance": "7. 验收", "packing": "8. 包装与标识", "force": "9. 不可抗力", "arbitration": "10. 适用法律与仲裁", "misc": "11. 其他条款",
      "parties": "12. 双方地址与银行资料", "buyer": "买方", "seller": "卖方", "spec1": "附件1 — 设备规格", "spec2": "附件2 — 备件清单",
      "cols_items": ["序号", "名称 / 型号", "数量", "单价", "金额", "海关编码"], "cols_zip": ["序号", "零件号", "名称", "单位", "数量", "单价", "金额", "更换周期 (小时)"]}
EN = {"contract": "SUPPLY CONTRACT", "subject": "1. Subject of the Contract", "price": "2. Price and Total Amount", "payment": "3. Terms of Payment", "terms": "4. Production and Delivery Time",
      "delivery": "5. Delivery Terms", "quality": "6. Quality and Warranty", "acceptance": "7. Acceptance", "packing": "8. Packing and Marking", "force": "9. Force Majeure",
      "arbitration": "10. Applicable Law and Arbitration", "misc": "11. Miscellaneous", "parties": "12. Addresses and Bank Details", "buyer": "Buyer", "seller": "Seller",
      "spec1": "Appendix 1 — Equipment Specification", "spec2": "Appendix 2 — Spare Parts Specification",
      "cols_items": ["No.", "Description / Model", "Qty", "Unit price", "Amount", "HS code"], "cols_zip": ["No.", "Part No.", "Description", "Unit", "Qty", "Unit price", "Amount", "Interval, h"]}
RU = {"contract": "ДОГОВОР ПОСТАВКИ", "subject": "1. Предмет договора", "price": "2. Цена и общая сумма договора", "payment": "3. Условия оплаты", "terms": "4. Сроки производства и поставки",
      "delivery": "5. Условия поставки", "quality": "6. Качество и гарантия", "acceptance": "7. Приёмка", "packing": "8. Упаковка и маркировка", "force": "9. Форс-мажор",
      "arbitration": "10. Применимое право и арбитраж", "misc": "11. Прочие условия", "parties": "12. Адреса и банковские реквизиты сторон", "buyer": "Покупатель", "seller": "Продавец",
      "spec1": "Приложение № 1 — Спецификация оборудования", "spec2": "Приложение № 2 — Спецификация ЗИП",
      "cols_items": ["№", "Наименование / модель", "Кол-во", "Цена", "Сумма", "ТН ВЭД"], "cols_zip": ["№", "Артикул", "Наименование", "Ед.", "Кол-во", "Цена", "Сумма", "Замена, м·ч"]}


def _second(language: str) -> dict | None:
    return ZH if language == "ru-zh" else EN if language == "ru-en" else None


def _title(doc, key: str, language: str, size=12):
    p = doc.add_paragraph()
    _run(p, RU[key], bold=True, color=ACCENT, size=size)
    sec = _second(language)
    if sec:
        _run(p, f"  /  {sec[key]}", bold=True, color=ACCENT, size=size - 1)
    return p


def _cols(key: str, language: str) -> list[str]:
    sec = _second(language)
    return [f"{r} / {s}" for r, s in zip(RU[key], sec[key])] if sec else list(RU[key])


def _num(v) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, (int, float)):
        s = f"{float(v):,.2f}".replace(",", " ").replace(".", ",")
        return s[:-3] if s.endswith(",00") else s
    return str(v)


def _party_block(doc, title_ru: str, title2: str | None, p: dict):
    ph = doc.add_paragraph()
    _run(ph, title_ru + (f" / {title2}" if title2 else ""), bold=True, color=BLACK, size=10)
    name = " / ".join(x for x in (p.get("name_ru"), p.get("name_en"), p.get("name_zh")) if x)
    rows = [["Наименование", name], ["Рег. номер", p.get("reg_number", "")], ["Налоговый номер", f"{p.get('tax_number', '')}{' / КПП ' + p['kpp'] if p.get('kpp') else ''}"],
            ["Адрес", p.get("legal_address", "")], ["Подписант", f"{p.get('signatory', '')}{', ' + p['signatory_position'] if p.get('signatory_position') else ''}{', ' + p['signatory_basis'] if p.get('signatory_basis') else ''}"]]
    b = p.get("bank") or {}
    if b.get("account"):
        rows += [["Получатель", b.get("beneficiary", "")], ["Банк", f"{b.get('bank_name', '')}{', ' + b['bank_address'] if b.get('bank_address') else ''}"], ["Счёт", f"{b.get('account', '')} ({b.get('currency', '')})"],
                 ["SWIFT / БИК", " / ".join(x for x in (b.get("swift"), b.get("bik")) if x)], ["Корр. счёт", b.get("corr_account", "")]]
    _table(doc, ["Реквизит", "Значение"], [r for r in rows if r[1]])


def _setup(doc):
    for s in doc.sections:
        s.left_margin = s.right_margin = Mm(20)
        s.top_margin = s.bottom_margin = Mm(18)
    st = doc.styles["Normal"]
    st.font.name = "Arial"
    st.font.size = Pt(10)


def _head(doc, ctx: dict, title_key: str, language: str):
    c = ctx["contract"]
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(p, RU[title_key], bold=True, color=ACCENT, size=16)
    sec = _second(language)
    if sec:
        p2 = doc.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _run(p2, sec[title_key], bold=True, color=ACCENT, size=13)
    p3 = doc.add_paragraph()
    p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(p3, f"№ {c.get('number') or '________'} от {c.get('date') or '«___» ________ 20__ г.'}" + (f", {c['place']}" if c.get("place") else ""), color=BLACK, size=10)


def _draft_mark(doc, ctx: dict):
    p = doc.add_paragraph()
    _run(p, f"ЧЕРНОВИК · сформирован автоматически из сделки {ctx['contract'].get('deal_number', '')} (версия {ctx['contract'].get('deal_version') or '—'}) · требует проверки юристом · "
            f"встроенная форма (шаблон владельца не загружен)", color=ACCENT, size=8)


def build_purchase_contract(ctx: dict, path: str) -> str:
    lang = ctx.get("language", "ru")
    doc = Document()
    _setup(doc)
    _head(doc, ctx, "contract", lang)
    _draft_mark(doc, ctx)
    b, s, c, t = ctx["buyer"], ctx["seller"], ctx["contract"], ctx["totals"]
    sec = _second(lang)
    p = doc.add_paragraph()
    _run(p, f"{s.get('name_ru') or s.get('name_en')}" + (f" ({s['name_zh']})" if s.get("name_zh") else "") + f", именуемое в дальнейшем «Продавец», в лице {s.get('signatory_position') or 'представителя'} "
            f"{s.get('signatory') or '________'}, действующего на основании {s.get('signatory_basis') or '________'}, с одной стороны, и {b.get('name_ru')}" + (f" ({b['name_en']})" if b.get("name_en") else "") +
            f", именуемое в дальнейшем «Покупатель», в лице {b.get('signatory_position') or 'представителя'} {b.get('signatory') or '________'}, действующего на основании {b.get('signatory_basis') or '________'}, "
            f"с другой стороны, заключили настоящий договор о нижеследующем.", color=BLACK)
    _title(doc, "subject", lang)
    doc.add_paragraph(f"1.1. Продавец обязуется поставить, а Покупатель — принять и оплатить {c.get('subject')} (далее — Оборудование) в количестве, ассортименте и по ценам согласно Приложению № 1, "
                      f"а также запасные части согласно Приложению № 2, являющимся неотъемлемой частью договора.")
    doc.add_paragraph(f"1.2. Всего поставляется {t['units_count']} ед. оборудования ({t['items_count']} поз.). Технические характеристики — в Приложении № 1.")
    _title(doc, "price", lang)
    doc.add_paragraph(f"2.1. Цены указаны в {c['currency']} на условиях {c.get('basis') or '____'}{' ' + c['delivery_point'] if c.get('delivery_point') else ''} (Incoterms 2020) и включают стоимость упаковки, маркировки и документации.")
    zip_line = "ЗИП по Приложению № 2 включён в стоимость Оборудования." if t.get("zip_included") else f"Стоимость ЗИП по Приложению № 2: {_num(t['zip_amount'])} {c['currency']}, оплачивается отдельно."
    doc.add_paragraph(f"2.2. Стоимость Оборудования: {_num(t['equipment_amount'])} {c['currency']}. {zip_line} Общая сумма договора: {_num(t['amount'])} {c['currency']} ({t.get('amount_words', '')}). {t.get('vat_text', '')}.")
    _title(doc, "payment", lang)
    doc.add_paragraph(f"3.1. Оплата производится банковским переводом на счёт Продавца, указанный в разделе 12, в следующем порядке: {c.get('payment_terms') or '________'}.")
    doc.add_paragraph("3.2. Датой платежа считается дата списания средств со счёта Покупателя. Банковские расходы на территории Покупателя несёт Покупатель, вне её — Продавец. "
                      "Изменение банковских реквизитов Продавца оформляется письмом за подписью уполномоченного лица и печатью; до получения такого письма платежи по новым реквизитам не производятся.")
    _title(doc, "terms", lang)
    doc.add_paragraph(f"4.1. Срок изготовления Оборудования: {c.get('production_term') or '________'}. Продавец письменно уведомляет Покупателя о готовности Оборудования к отгрузке и приёмке.")
    doc.add_paragraph("4.2. Отгрузка производится после приёмки (онлайн или на заводе) и устранения замечаний. Просрочка поставки по вине Продавца свыше 15 дней даёт Покупателю право требовать неустойку "
                      "0,1 % стоимости не поставленного в срок Оборудования за каждый день, но не более 5 %.")
    _title(doc, "delivery", lang)
    doc.add_paragraph(f"5.1. Базис поставки — {c.get('basis') or '____'}{' ' + c['delivery_point'] if c.get('delivery_point') else ''}. Право собственности и риски переходят к Покупателю в момент передачи перевозчику согласно базису.")
    doc.add_paragraph("5.2. Продавец передаёт с Оборудованием: коммерческий инвойс, упаковочный лист, сертификат качества/происхождения, паспорт и руководство по эксплуатации на русском языке, "
                      "а также документы, необходимые для таможенного оформления в Российской Федерации.")
    _title(doc, "quality", lang)
    doc.add_paragraph(f"6.1. Качество Оборудования соответствует стандартам завода-изготовителя и техническим характеристикам Приложения № 1. Гарантийный срок: {c.get('warranty') or '________'}; "
                      "исчисляется с даты ввода в эксплуатацию, но не позднее 6 месяцев с даты поставки.")
    doc.add_paragraph("6.2. В гарантийный период Продавец за свой счёт заменяет или ремонтирует дефектные узлы в срок до 30 дней с момента подтверждения рекламации; расходы по доставке запасных частей несёт Продавец.")
    _title(doc, "acceptance", lang)
    doc.add_paragraph("7.1. Предварительная приёмка проводится на заводе Продавца (или онлайн) по чек-листу Покупателя; окончательная — по количеству и внешнему виду при получении, по качеству — при вводе в эксплуатацию. "
                      "Выявленные несоответствия фиксируются актом и устраняются Продавцом.")
    _title(doc, "packing", lang)
    doc.add_paragraph("8.1. Упаковка обеспечивает сохранность при морской/железнодорожной/автомобильной перевозке и перевалках. Маркировка — на русском и английском языках с номером договора и местами.")
    _title(doc, "force", lang)
    doc.add_paragraph("9.1. Стороны освобождаются от ответственности за частичное или полное неисполнение обязательств вследствие обстоятельств непреодолимой силы, подтверждённых торгово-промышленной палатой страны стороны; сроки продлеваются соразмерно.")
    _title(doc, "arbitration", lang)
    doc.add_paragraph("10.1. Применимое право — материальное право Российской Федерации. Споры разрешаются путём переговоров, при недостижении согласия — в Международном коммерческом арбитражном суде "
                      "при ТПП РФ (г. Москва) в соответствии с его регламентом; язык разбирательства — русский.")
    _title(doc, "misc", lang)
    doc.add_paragraph(f"11.1. Договор составлен на {'русском и китайском' if lang == 'ru-zh' else 'русском и английском' if lang == 'ru-en' else 'русском'} языках"
                      + ("; в случае разночтений преимущество имеет текст на русском языке" if lang != "ru" else "") + ". Договор вступает в силу с даты подписания и действует до полного исполнения обязательств. "
                      "Документы, переданные по электронной почте, имеют силу до обмена оригиналами.")
    _title(doc, "parties", lang)
    _party_block(doc, RU["seller"], sec["seller"] if sec else None, s)
    doc.add_paragraph()
    _party_block(doc, RU["buyer"], sec["buyer"] if sec else None, b)
    doc.add_paragraph()
    tb = doc.add_table(rows=1, cols=2)
    for i, (party, ttl) in enumerate(((s, RU["seller"]), (b, RU["buyer"]))):
        cell = tb.rows[0].cells[i]
        cell.text = ""
        _run(cell.paragraphs[0], f"{ttl}:\n\n_______________ / {party.get('signatory') or '________'} /\n{party.get('signatory_position') or ''}\nМ.П.", color=BLACK, size=9)
    doc.save(path)
    return path


def build_spec_equipment(ctx: dict, path: str) -> str:
    lang = ctx.get("language", "ru")
    doc = Document()
    _setup(doc)
    _head(doc, ctx, "spec1", lang)
    _draft_mark(doc, ctx)
    c, t = ctx["contract"], ctx["totals"]
    rows = []
    for it in ctx["items"]:
        desc = it.get("designation") or f"{it.get('trademark', '')} {it.get('model', '')}".strip()
        if it.get("options"):
            desc += f"\nОпции: {it['options']}"
        if it.get("params_text"):
            desc += f"\n{it['params_text']}"
        rows.append([str(it["n"]), desc, f"{_num(it['qty'])} {it.get('unit', 'шт')}", _num(it["unit_price"]), _num(it["amount"]), it.get("hs_code") or "—"])
    rows.append(["", "Итого / Total", f"{t['units_count']}", "", f"{_num(t['equipment_amount'])} {c['currency']}", ""])
    _table(doc, _cols("cols_items", lang), rows, accent_last_row=True)
    doc.add_paragraph()
    for it in ctx["items"]:
        p = doc.add_paragraph()
        _run(p, f"{it['n']}. {it.get('designation')}: ", bold=True, color=BLACK)
        _run(p, f"срок производства — {it.get('production_term') or '—'}; гарантия — {it.get('warranty') or '—'}" + (f"; опции, реализуемые в РФ (в договор не входят): {', '.join(it['local_options_excluded'])}" if it.get("local_options_excluded") else ""), color=BLACK)
    if ctx.get("params"):
        doc.add_paragraph()
        _heading(doc, "Технические характеристики / Technical parameters", 11)
        _table(doc, ["№", "Позиция", "Параметр", "Ед.", "Требование", "Значение"], [[str(r["n"]), r["position"], r["name"] + (f" / {r['name_zh']}" if r.get("name_zh") and lang == "ru-zh" else ""), r.get("unit", ""), r.get("requirement", ""), r.get("value", "")] for r in ctx["params"]])
    doc.add_paragraph()
    p = doc.add_paragraph()
    _run(p, f"Базис поставки: {c.get('basis') or '—'} {c.get('delivery_point') or ''}. Валюта: {c['currency']}. Условия оплаты: {c.get('payment_terms') or '—'}.", color=BLACK, size=9)
    doc.save(path)
    return path


def build_spec_zip(ctx: dict, path: str) -> str:
    lang = ctx.get("language", "ru")
    doc = Document()
    _setup(doc)
    _head(doc, ctx, "spec2", lang)
    _draft_mark(doc, ctx)
    c, t = ctx["contract"], ctx["totals"]
    rows = [[str(z["n"]), z.get("article", ""), z.get("name", "") + (f" / {z['name_zh']}" if z.get("name_zh") and lang == "ru-zh" else "") + (f" ({z['assembly']})" if z.get("assembly") else ""), z.get("unit", "шт"),
             _num(z["qty"]), _num(z.get("unit_price")), _num(z.get("amount")), _num(z.get("interval_hours"))] for z in ctx["zip"]]
    if not rows:
        doc.add_paragraph("Запасные части в комплект поставки не включены.")
    else:
        rows.append(["", "", "Итого / Total", "", "", "", f"{_num(t['zip_amount'])} {c['currency']}", ""])
        _table(doc, _cols("cols_zip", lang), rows, accent_last_row=True)
        doc.add_paragraph()
        doc.add_paragraph("Стоимость ЗИП включена в стоимость Оборудования по договору." if t.get("zip_included") else "Стоимость ЗИП оплачивается отдельно в порядке, предусмотренном разделом 3 договора.")
    doc.save(path)
    return path
