"""Юридический блок, этап 2: библиотека DOCX-шаблонов (договоры закупки, спецификации, протокол разногласий, техспецификация)
и база примеров; каталог переменных, которые система подставляет в шаблоны.

Шаблон загружает юрист (или КД/ЗГД/админ): при загрузке система находит плейсхолдеры `{{…}}` и повторяемые строки `{{#…}}`,
сверяет их с каталогом и показывает неизвестные. «Тестовое формирование» заполняет шаблон образцовыми данными — так видно,
что вёрстка и переменные в порядке, до реальной сделки. Пока шаблона нет — пакеты собирает встроенный генератор (этап 3).
"""
from __future__ import annotations

import os
from typing import Any

from ..models import Template

LEGAL_KINDS: dict[str, dict[str, Any]] = {
    "purchase_contract_cn":    {"title": "Договор закупки с китайским поставщиком", "deal_kind": "purchase", "country": "CN", "language": "ru-zh", "loops": ["items", "zip"]},
    "purchase_contract_kg":    {"title": "Договор закупки с киргизской компанией", "deal_kind": "purchase", "country": "KG", "language": "ru", "loops": ["items", "zip"]},
    "purchase_contract_other": {"title": "Договор закупки с иностранным поставщиком", "deal_kind": "purchase", "country": "", "language": "ru-en", "loops": ["items", "zip"]},
    "spec_equipment":          {"title": "Приложение № 1 — спецификация оборудования", "deal_kind": "purchase", "country": "", "language": "ru-zh", "loops": ["items", "params"]},
    "spec_zip":                {"title": "Приложение № 2 — спецификация ЗИП", "deal_kind": "purchase", "country": "", "language": "ru-zh", "loops": ["zip"]},
    "disagreements_protocol":  {"title": "Протокол разногласий к договору заказчика", "deal_kind": "sale", "country": "RU", "language": "ru", "loops": ["clauses"]},
    "tech_spec_client":        {"title": "Техническая спецификация для заказчика", "deal_kind": "sale", "country": "RU", "language": "ru", "loops": ["items", "params", "options"]},
    "power_of_attorney":       {"title": "Доверенность от компании группы", "deal_kind": "corporate", "country": "RU", "language": "ru", "loops": ["powers"]},
    "poa_revocation":          {"title": "Уведомление об отмене доверенности", "deal_kind": "corporate", "country": "RU", "language": "ru", "loops": []},
}
EXAMPLE_KINDS: dict[str, str] = {"example_client_contract": "Пример договора заказчика", "example_protocol": "Пример протокола разногласий"}
LANGUAGES = {"ru": "русский", "ru-zh": "русский / китайский", "ru-en": "русский / английский", "en": "английский", "zh": "китайский"}

# ------------------------------------------------------------------ каталог переменных
# Стороны: buyer / seller — кто покупает и кто продаёт в данном документе. В закупке buyer = компания группы, seller = поставщик;
# в продаже клиенту seller = компания РФ, buyer = заказчик. Имена на языках: name_ru / name_en / name_zh.

PARTY_FIELDS = {
    "name_ru": "полное наименование (RU)", "short_ru": "краткое наименование (RU)", "name_en": "наименование (EN)", "name_zh": "наименование (中文)",
    "country": "страна", "reg_number": "рег. номер (ОГРН / 统一社会信用代码)", "tax_number": "налоговый номер (ИНН)", "kpp": "КПП",
    "legal_address": "юридический адрес", "actual_address": "фактический адрес", "postal_address": "почтовый адрес",
    "director": "руководитель / законный представитель", "signatory": "ФИО подписанта", "signatory_position": "должность подписанта",
    "signatory_basis": "основание полномочий", "phone": "телефон", "email": "e-mail",
    "bank.title": "название банковского профиля", "bank.beneficiary": "получатель платежа", "bank.bank_name": "банк", "bank.bank_address": "адрес банка",
    "bank.account": "счёт", "bank.bik": "БИК", "bank.swift": "SWIFT", "bank.corr_account": "корр. счёт", "bank.corr_bank": "банк-корреспондент", "bank.currency": "валюта счёта",
}
VARIABLES: dict[str, dict[str, str]] = {
    "contract": {"number": "номер договора", "date": "дата договора (дд.мм.гггг)", "place": "место заключения", "subject": "предмет договора",
                 "currency": "валюта договора", "basis": "базис поставки (Incoterms)", "delivery_point": "пункт поставки", "production_term": "срок производства (текст)",
                 "delivery_term": "срок поставки (текст)", "warranty": "гарантия (текст)", "payment_terms": "условия оплаты (текст)", "language": "язык документа",
                 "deal_number": "номер сделки в CRM", "deal_version": "версия сделки", "client_contract_number": "номер договора заказчика (для протокола)",
                 "client_contract_date": "дата договора заказчика"},
    "buyer": PARTY_FIELDS, "seller": PARTY_FIELDS,
    "totals": {"equipment_amount": "сумма оборудования", "zip_amount": "сумма ЗИП", "options_amount": "сумма опций", "amount": "итого по договору", "amount_words": "итого прописью",
               "currency": "валюта", "vat_text": "НДС (текст)", "items_count": "число позиций", "units_count": "число единиц техники"},
    "items[]": {"n": "№ п/п", "name": "наименование", "trademark": "торговая марка", "model": "модель", "execution": "исполнение", "machine_type": "тип машины",
                "qty": "количество", "unit": "ед. изм.", "unit_price": "цена за единицу", "amount": "сумма", "currency": "валюта", "options": "опции (текст)",
                "hs_code": "код ТН ВЭД", "production_term": "срок производства", "warranty": "гарантия", "name_zh": "наименование (中文)", "params_text": "характеристики (текст)"},
    "params[]": {"n": "№", "position": "позиция / модель", "name": "параметр", "name_zh": "параметр (中文)", "unit": "ед.", "requirement": "требование заказчика",
                 "value": "значение", "value_zh": "значение (中文)", "comment": "комментарий"},
    "zip[]": {"n": "№", "article": "артикул", "name": "наименование", "name_zh": "наименование (中文)", "assembly": "узел / система", "unit": "ед.", "qty": "количество",
              "unit_price": "цена", "amount": "сумма", "currency": "валюта", "interval_hours": "периодичность замены, м·ч", "kind": "вид (поставка / ТО)",
              "lead_days": "срок поставки, дн", "included": "включено в стоимость (да/нет)"},
    "options[]": {"n": "№", "name": "наименование опции", "qty": "количество", "position": "к какой позиции", "comment": "комментарий"},
    "clauses[]": {"n": "№", "clause": "пункт договора", "client_text": "редакция заказчика", "our_text": "редакция БЕРГ-ХАУС", "comment": "комментарий"},
    "package": {"created_on": "дата формирования", "author": "автор", "version": "версия пакета", "status": "статус (черновик)"},
    "poa": {"number": "номер доверенности", "date": "дата выдачи (дд.мм.гггг)", "date_words": "дата выдачи прописью", "valid_to": "действительна до (дд.мм.гггг)", "place": "место выдачи",
            "kind": "вид (генеральная / специальная / разовая)", "substitution": "с правом / без права передоверия", "amount_limit": "ограничение по сумме (текст)",
            "counterparties": "ограничение по контрагентам", "territory": "территория действия", "extra": "прочие ограничения", "revoked_on": "дата отзыва (для уведомления)", "revoke_reason": "причина отзыва"},
    "principal": {"name": "доверитель — полное наименование", "short": "доверитель — краткое наименование", "inn": "ИНН", "kpp": "КПП", "ogrn": "ОГРН", "address": "адрес",
                  "signatory": "подписант (ФИО)", "signatory_position": "должность подписанта", "signatory_basis": "основание полномочий подписанта"},
    "attorney": {"full_name": "представитель — ФИО", "position": "должность", "text": "представитель — полный текст (ФИО, дата рождения, паспорт, адрес)", "birth_date": "дата рождения",
                 "address": "адрес регистрации", "passport": "паспорт (серия и номер)"},
    "powers[]": {"n": "№", "text": "полномочие (текст)"},
}


def known_variable(name: str) -> bool:
    """Переменная известна каталогу: group.field, buyer.bank.account, или поле строки цикла (items[] …)."""
    if "." not in name:
        return False
    group, _, rest = name.partition(".")
    fields = VARIABLES.get(group)
    if fields is None:
        return False
    return rest in fields


def known_loop_field(loop: str, field: str) -> bool:
    fields = VARIABLES.get(f"{loop}[]")
    return fields is not None and field in fields


def catalog() -> list[dict]:
    out = []
    for group, fields in VARIABLES.items():
        out.append({"group": group, "loop": group.endswith("[]"), "title": GROUP_TITLES.get(group, group),
                    "fields": [{"name": (f"{group[:-2]}" if group.endswith("[]") else group) + ("." if not group.endswith("[]") else ": ") + k, "key": k, "title": v} for k, v in fields.items()]})
    return out


GROUP_TITLES = {"contract": "Договор", "buyer": "Покупатель", "seller": "Продавец", "totals": "Итоги", "items[]": "Строки спецификации оборудования (таблица, маркер {{#items}})",
                "params[]": "Строки технических характеристик ({{#params}})", "zip[]": "Строки спецификации ЗИП ({{#zip}})", "options[]": "Строки опций ({{#options}})",
                "clauses[]": "Строки протокола разногласий ({{#clauses}})", "package": "Пакет документов"}


def analyze_template(path: str) -> dict:
    """Сканирует шаблон: переменные, циклы, неизвестные каталогу имена."""
    from .docx_fill import scan_variables
    found = scan_variables(path)
    unknown = [v for v in found["variables"] if not known_variable(v)]
    unknown_loop: dict[str, list[str]] = {}
    for loop, fields in found["loops"].items():
        if f"{loop}[]" not in VARIABLES:
            unknown_loop[loop] = fields
            continue
        bad = [f for f in fields if f != "n" and not known_loop_field(loop, f) and not known_variable(f)]
        if bad:
            unknown_loop[loop] = bad
    return {**found, "unknown": unknown, "unknown_loops": unknown_loop}


# ------------------------------------------------------------------ образцовые данные для тестового формирования

def _party(prefix: str, ru: str, en: str, zh: str, country: str, tax: str, bank_cur: str) -> dict:
    return {"name_ru": ru, "short_ru": ru.split("«")[-1].rstrip("»") if "«" in ru else ru, "name_en": en, "name_zh": zh, "country": country,
            "reg_number": "1027700000000" if country == "RU" else "91370800MA3XXXXXXX", "tax_number": tax, "kpp": "770101001" if country == "RU" else "",
            "legal_address": "123456, г. Москва, ул. Примерная, д. 1" if country == "RU" else "No. 1 Example Road, Jining, Shandong, China",
            "actual_address": "", "postal_address": "", "director": "Иванов Иван Иванович" if country == "RU" else "Li Wei", "signatory": "Иванов Иван Иванович" if country == "RU" else "Li Wei",
            "signatory_position": "Генеральный директор" if country == "RU" else "General Manager", "signatory_basis": "Устава" if country == "RU" else "Articles of Association",
            "phone": "+7 495 000-00-00", "email": f"{prefix}@example.com",
            "bank": {"title": f"Основной {bank_cur}", "beneficiary": en, "bank_name": "Example Bank" if country != "RU" else "АО «Пример-Банк»", "bank_address": "Bank street 1",
                     "account": "40702810000000000001", "bik": "044525000" if country == "RU" else "", "swift": "EXAMCNBJ" if country != "RU" else "EXAMRUMM",
                     "corr_account": "30101810000000000000" if country == "RU" else "", "corr_bank": "", "currency": bank_cur}}


def sample_data(kind: str) -> dict:
    """Образцовые данные для тестового формирования шаблона (без связи со сделкой)."""
    own = _party("berghaus", "ООО «БЕРГ-ХАУС»", "BERG-HAUS LLC", "БЕРГ-ХАУС有限公司", "RU", "7700000000", "CNY")
    supplier = _party("supplier", "Шаньдун Шили Хэви Индастри Ко., Лтд.", "Shandong Shili Heavy Industry Co., Ltd.", "山东世力重工有限公司", "CN", "91370800MA3XXXXXXX", "CNY")
    client = _party("client", "Акционерное общество «Полюс Алдан»", "Polyus Aldan JSC", "", "RU", "1402046085", "RUB")
    purchase = kind.startswith("purchase") or kind.startswith("spec_")
    items = [{"n": 1, "name": "Самосвал шахтный", "trademark": "SHACMAN", "model": "X3000", "execution": "6×4, 400 л. с.", "machine_type": "самосвал", "qty": 2, "unit": "шт",
              "unit_price": 1500000.0, "amount": 3000000.0, "currency": "CNY", "options": "кондиционер; система пожаротушения", "hs_code": "8704101090", "production_term": "90 календарных дней",
              "warranty": "12 месяцев или 2000 м·ч", "name_zh": "矿用自卸车", "params_text": "грузоподъёмность 40 т; двигатель Weichai WP12"},
             {"n": 2, "name": "Погрузочно-доставочная машина", "trademark": "SHILI", "model": "SHL-4000", "execution": "дизель", "machine_type": "ПДМ", "qty": 1, "unit": "шт",
              "unit_price": 2200000.0, "amount": 2200000.0, "currency": "CNY", "options": "", "hs_code": "8429510000", "production_term": "120 календарных дней", "warranty": "12 месяцев", "name_zh": "铲运机", "params_text": "ковш 4 м³"}]
    zip_rows = [{"n": 1, "article": "HF-1001", "name": "Фильтр гидравлический", "name_zh": "液压滤芯", "assembly": "Гидросистема", "unit": "шт", "qty": 4, "unit_price": 350.0, "amount": 1400.0, "currency": "CNY",
                 "interval_hours": 500, "kind": "ЗИП на ТО", "lead_days": 30, "included": "да"},
                {"n": 2, "article": "OF-2201", "name": "Фильтр масляный", "name_zh": "机油滤芯", "assembly": "Двигатель", "unit": "шт", "qty": 8, "unit_price": 120.0, "amount": 960.0, "currency": "CNY",
                 "interval_hours": 250, "kind": "ЗИП на ТО", "lead_days": 30, "included": "да"}]
    params = [{"n": 1, "position": "SHACMAN X3000", "name": "Грузоподъёмность", "name_zh": "载重量", "unit": "т", "requirement": "не менее 40", "value": "40", "value_zh": "40", "comment": ""},
              {"n": 2, "position": "SHACMAN X3000", "name": "Мощность двигателя", "name_zh": "发动机功率", "unit": "л. с.", "requirement": "не менее 380", "value": "400", "value_zh": "400", "comment": ""}]
    clauses = [{"n": 1, "clause": "4.2", "client_text": "Оплата 100 % в течение 60 дней после поставки.", "our_text": "Аванс 30 % в течение 5 банковских дней с даты подписания, 70 % — аккредитив, раскрываемый против отгрузочных документов.", "comment": ""},
               {"n": 2, "clause": "7.1", "client_text": "Неустойка 1 % за каждый день просрочки без ограничения.", "our_text": "Неустойка 0,1 % за каждый день просрочки, но не более 5 % стоимости не поставленного в срок оборудования.", "comment": ""}]
    options = [{"n": 1, "name": "Система пожаротушения", "qty": 2, "position": "SHACMAN X3000", "comment": "устанавливается в РФ"}]
    amount = sum(i["amount"] for i in items)
    return {"contract": {"number": "BH-2026-001", "date": "15.11.2026", "place": "г. Москва", "subject": "поставка горно-шахтного оборудования", "currency": "CNY" if purchase else "RUB",
                         "basis": "FCA", "delivery_point": "Jining, Shandong", "production_term": "90–120 календарных дней с даты аванса", "delivery_term": "45 календарных дней с даты готовности",
                         "warranty": "12 месяцев или 2000 м·ч", "payment_terms": "30 % аванс, 70 % против отгрузочных документов", "language": "ru-zh" if purchase else "ru",
                         "deal_number": "ЗК-2026-0090", "deal_version": "3", "client_contract_number": "К-2026-11", "client_contract_date": "01.09.2026"},
            "buyer": own if purchase else client, "seller": supplier if purchase else own,
            "totals": {"equipment_amount": amount, "zip_amount": 2360.0, "options_amount": 0.0, "amount": amount + 2360.0, "amount_words": "пять миллионов двести две тысячи триста шестьдесят юаней 00 фэней",
                       "currency": "CNY" if purchase else "RUB", "vat_text": "НДС не облагается" if purchase else "в том числе НДС 20 %", "items_count": len(items), "units_count": 3},
            "items": items, "zip": zip_rows, "params": params, "options": options, "clauses": clauses,
            "package": {"created_on": "15.11.2026", "author": "Петров А.", "version": "1", "status": "черновик — требует проверки юристом"},
            "poa": {"number": "Д-RU-2026-001", "date": "15.11.2026", "date_words": "пятнадцатое ноября две тысячи двадцать шестого года", "valid_to": "31.12.2027", "place": "г. Москва",
                    "kind": "Специальная", "substitution": "без права передоверия", "amount_limit": "на сумму не более 5 000 000 (пяти миллионов) рублей по одной сделке",
                    "counterparties": "", "territory": "Российская Федерация", "extra": "", "revoked_on": "01.03.2027", "revoke_reason": "прекращение трудовых отношений"},
            "principal": {"name": "Общество с ограниченной ответственностью «БЕРГ-ХАУС»", "short": "ООО «БЕРГ-ХАУС»", "inn": "7700000000", "kpp": "770001001", "ogrn": "1177700000000",
                          "address": "123456, г. Москва, ул. Примерная, д. 1", "signatory": "Иванов Иван Иванович", "signatory_position": "Генеральный директор", "signatory_basis": "Устава"},
            "attorney": {"full_name": "Петров Пётр Петрович", "position": "Менеджер по продажам", "birth_date": "01.01.1990", "address": "г. Москва, ул. Лесная, д. 5, кв. 7", "passport": "4510 123456",
                         "text": "Петров Пётр Петрович, 01.01.1990 года рождения, паспорт 4510 123456, выдан ОВД района Примерный г. Москвы 01.02.2010, код подразделения 770-001, зарегистрированный по адресу: г. Москва, ул. Лесная, д. 5, кв. 7"},
            "powers": [{"n": 1, "text": "представлять интересы Общества во взаимоотношениях с контрагентами"}, {"n": 2, "text": "подписывать договоры поставки на сумму не более 5 000 000 рублей по одной сделке"}]}


def template_payload(t: Template, names: dict[str, str]) -> dict:
    kinds = {**{k: v["title"] for k, v in LEGAL_KINDS.items()}, **EXAMPLE_KINDS}
    return {"id": t.id, "kind": t.kind, "kind_title": kinds.get(t.kind, t.kind), "title": t.title or kinds.get(t.kind, t.kind), "version": t.version, "language": t.language,
            "language_title": LANGUAGES.get(t.language, t.language), "country": t.country, "deal_kind": t.deal_kind, "is_active": t.is_active, "is_example": t.is_example,
            "variables": t.variables or {}, "notes": t.notes, "file_name": os.path.basename(t.file_path or ""), "file_exists": bool(t.file_path) and os.path.isfile(t.file_path),
            "uploaded_by_name": names.get(t.uploaded_by or "", ""), "uploaded_at": t.uploaded_at}
