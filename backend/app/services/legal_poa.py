"""Доверенности компаний группы (юридический блок): каталог полномочий, нумерация, статусы, печатная форма, уведомление об отмене, напоминания.

Оформляют и отзывают юрист и ЗГД. Статусы: черновик → действует (с даты выдачи) → истекла (по сроку) / отозвана; черновик можно аннулировать.
Печатная форма — встроенная (фирменный стиль) или шаблон вида «power_of_attorney» из библиотеки; уведомление об отмене — «poa_revocation».
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import date, datetime, timezone
from typing import Any, Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Mm, Pt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import LegalEntity, LegalProfile, PowerOfAttorney, User
from .kp_generator import ACCENT, BLACK, _heading, _run, _table

KINDS = {"general": "Генеральная", "special": "Специальная", "single": "Разовая"}
STATUSES = {"draft": "черновик", "active": "действует", "expired": "срок истёк", "revoked": "отозвана", "cancelled": "аннулирована"}
FILE_KINDS = {"draft": "Проект (DOCX)", "issued": "Оформленная (DOCX)", "signed_scan": "Подписанный скан", "revocation": "Уведомление об отмене", "other": "Прочее"}
SENSITIVE_ROLES = {"lawyer", "gd", "fd", "admin"}

# Каталог полномочий — конструктор. Группы и формулировки; {{…}} подставляются из ограничений доверенности.
POWERS_CATALOG: dict[str, dict[str, Any]] = {
    "contracts": {"title": "Договоры", "items": {
        "sign_contracts": "заключать (подписывать) от имени Доверителя договоры поставки, купли-продажи, оказания услуг и выполнения работ, дополнительные соглашения, спецификации и приложения к ним{{amount_clause}}",
        "sign_supply_contracts": "подписывать договоры поставки горно-шахтного оборудования и запасных частей, спецификации, протоколы разногласий и дополнительные соглашения к ним{{amount_clause}}",
        "sign_foreign": "заключать внешнеторговые контракты с иностранными поставщиками, подписывать инвойсы, спецификации и дополнительные соглашения к ним{{amount_clause}}",
        "negotiate": "вести переговоры с контрагентами, согласовывать условия договоров, подписывать протоколы переговоров и соглашения о намерениях",
        "terminate": "подписывать соглашения о расторжении договоров и уведомления об одностороннем отказе от договора"}},
    "documents": {"title": "Первичные документы", "items": {
        "sign_primary": "подписывать товарные накладные, универсальные передаточные документы, акты приёма-передачи, акты выполненных работ (оказанных услуг), счета и счета-фактуры",
        "sign_acceptance": "подписывать акты приёмки оборудования (предварительной и окончательной), акты о выявленных несоответствиях, рекламационные акты и акты ввода в эксплуатацию",
        "sign_claims": "подписывать и направлять претензии, ответы на претензии, уведомления и письма в адрес контрагентов",
        "receive_goods": "получать товарно-материальные ценности, оборудование и документы, расписываться в их получении"}},
    "customs_logistics": {"title": "Таможня и логистика", "items": {
        "customs": "представлять интересы Доверителя в таможенных органах, подписывать и подавать декларации на товары, документы и сведения, необходимые для таможенного оформления, получать выпущенные товары",
        "transport": "заключать договоры перевозки, транспортной экспедиции и страхования грузов, подписывать транспортные накладные, CMR, коносаменты и заявки на перевозку",
        "certification": "подавать заявки и получать сертификаты и декларации соответствия, разрешительные документы на ввоз и эксплуатацию оборудования"}},
    "state": {"title": "Государственные органы и суды", "items": {
        "state_bodies": "представлять интересы Доверителя в государственных органах, органах местного самоуправления, Ростехнадзоре, ФНС, фондах, подавать и получать документы",
        "courts": "представлять интересы Доверителя в судах общей юрисдикции, арбитражных и третейских судах со всеми правами, предоставленными истцу, ответчику, третьему лицу, в том числе подписывать исковое заявление, отзыв, заявлять ходатайства, заключать мировое соглашение, обжаловать судебные акты, получать исполнительные документы",
        "fssp": "представлять интересы Доверителя в органах принудительного исполнения, предъявлять и отзывать исполнительные документы"}},
    "bank_finance": {"title": "Банки и финансы", "items": {
        "bank_docs": "подписывать платёжные документы и распоряжаться денежными средствами на счетах Доверителя{{amount_clause}}",
        "bank_represent": "представлять интересы Доверителя в банках: получать выписки, справки, подавать заявления, подписывать договоры банковского обслуживания и аккредитивов",
        "currency_control": "подписывать документы валютного контроля, паспорта сделок, справки о подтверждающих документах"}},
    "hr_admin": {"title": "Кадры и администрирование", "items": {
        "hr": "подписывать трудовые договоры, приказы по личному составу, кадровые документы",
        "mail": "получать почтовую корреспонденцию, посылки, бандероли и переводы, расписываться за их получение",
        "tenders": "участвовать от имени Доверителя в закупочных процедурах и торгах, подписывать заявки, ценовые предложения и протоколы"}},
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def catalog() -> list[dict]:
    return [{"group": g, "title": v["title"], "items": [{"code": c, "text": t} for c, t in v["items"].items()]} for g, v in POWERS_CATALOG.items()]


def amount_clause(limits: dict) -> str:
    a = limits.get("amount_limit")
    if not a:
        return ""
    cur = limits.get("currency") or "RUB"
    return f" на сумму не более {float(a):,.2f} {cur} по одной сделке".replace(",", " ").replace(".00", "")


def resolve_powers(powers: list[dict], limits: dict) -> list[dict]:
    """Текст полномочий с подстановкой ограничений: [{n, code, text}]."""
    out = []
    ac = amount_clause(limits)
    for i, p in enumerate(powers, 1):
        code = p.get("code") or ""
        text = p.get("text") or ""
        if code:
            for g in POWERS_CATALOG.values():
                if code in g["items"] and not text:
                    text = g["items"][code]
        text = text.replace("{{amount_clause}}", ac).strip()
        if text:
            out.append({"n": i, "code": code, "text": text.rstrip(";.")})
    return out


def next_number(db: Session, entity: LegalEntity, on: date) -> tuple[str, int]:
    seq = (db.scalar(select(func.max(PowerOfAttorney.seq)).where(PowerOfAttorney.entity_id == entity.id, PowerOfAttorney.number.like(f"%-{on.year}-%"))) or 0) + 1
    return f"Д-{(entity.code or 'RU').upper()}-{on.year}-{seq:03d}", seq


def principal_snapshot(p: LegalProfile) -> dict:
    n = p.names or {}
    ru = n.get("ru") or {}
    return {"profile_id": p.id, "name": ru.get("full") or ru.get("short") or "", "short": ru.get("short") or "", "inn": p.tax_number, "kpp": p.kpp, "ogrn": p.reg_number,
            "address": p.legal_address, "signatory": p.signatory_name or p.director, "signatory_position": p.signatory_position or "Генеральный директор", "signatory_basis": p.signatory_basis or "Устава"}


def refresh_status(poa: PowerOfAttorney, today: Optional[date] = None) -> bool:
    """Действующая доверенность с истёкшим сроком → «срок истёк». Возвращает True, если статус изменён."""
    today = today or date.today()
    if poa.status == "active" and poa.valid_to and poa.valid_to < today:
        poa.status = "expired"
        poa.history = list(poa.history or []) + [{"at": _now().isoformat(), "by": None, "action": "expired", "comment": f"срок действия истёк {poa.valid_to:%d.%m.%Y}"}]
        return True
    return False


def days_left(poa: PowerOfAttorney, today: Optional[date] = None) -> Optional[int]:
    if not poa.valid_to:
        return None
    return (poa.valid_to - (today or date.today())).days


def payload(poa: PowerOfAttorney, names: dict[str, str], entity_name: str, sensitive: bool) -> dict:
    att = dict(poa.attorney or {})
    if not sensitive:
        att.pop("passport", None)
        for k in ("birth_date", "birth_place", "address"):
            att.pop(k, None)
    dl = days_left(poa)
    return {"id": poa.id, "entity_id": poa.entity_id, "entity_name": entity_name, "profile_id": poa.profile_id, "number": poa.number, "kind": poa.kind, "kind_title": KINDS.get(poa.kind, poa.kind),
            "status": poa.status, "status_title": STATUSES.get(poa.status, poa.status), "issued_on": poa.issued_on, "valid_to": poa.valid_to, "days_left": dl,
            "expiring": poa.status == "active" and dl is not None and dl <= 30, "place": poa.place, "principal": poa.principal or {}, "attorney_user_id": poa.attorney_user_id,
            "attorney": att, "powers": poa.powers or [], "powers_resolved": resolve_powers(poa.powers or [], poa.limits or {}), "limits": poa.limits or {},
            "files": [{**f, "kind_title": FILE_KINDS.get(f.get("kind", "other"), f.get("kind")), "uploaded_by_name": names.get(f.get("uploaded_by") or "", "")} for f in (poa.files or [])],
            "history": [{**h, "by_name": names.get(h.get("by") or "", "")} for h in reversed(poa.history or [])],
            "revoked_at": poa.revoked_at, "revoked_by_name": names.get(poa.revoked_by or "", ""), "revoke_reason": poa.revoke_reason, "notes": poa.notes,
            "created_by_name": names.get(poa.created_by or "", ""), "created_at": poa.created_at, "sensitive_visible": sensitive}


def user_names(db: Session) -> dict[str, str]:
    return {u.id: u.display_name for u in db.scalars(select(User)).all()}


# ------------------------------------------------------------------ печатные формы

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def date_words(d: Optional[date]) -> str:
    return f"«{d.day:02d}» {MONTHS[d.month - 1]} {d.year} г." if d else "«___» ________ 20__ г."


GENITIVE = {"генеральный директор": "Генерального директора", "директор": "Директора", "исполнительный директор": "Исполнительного директора", "коммерческий директор": "Коммерческого директора",
            "финансовый директор": "Финансового директора", "заместитель генерального директора": "Заместителя генерального директора", "президент": "Президента", "управляющий": "Управляющего",
            "индивидуальный предприниматель": "Индивидуального предпринимателя", "руководитель": "Руководителя", "директор филиала": "Директора филиала"}


def genitive_position(pos: Optional[str]) -> str:
    """Должность в родительном падеже для «в лице …»: по словарю типовых должностей, иначе как есть."""
    if not pos:
        return "Генерального директора"
    return GENITIVE.get(pos.strip().lower(), pos.strip())


def _setup(doc):
    for s in doc.sections:
        s.left_margin = s.right_margin = Mm(20)
        s.top_margin = s.bottom_margin = Mm(18)
    doc.styles["Normal"].font.name = "Arial"
    doc.styles["Normal"].font.size = Pt(11)


def _attorney_text(a: dict) -> str:
    pas = a.get("passport") or {}
    parts = [a.get("full_name") or "________"]
    if a.get("position"):
        parts.append(f"{a['position']}")
    if a.get("birth_date"):
        parts.append(f"{a['birth_date']} года рождения" if not re.match(r"\d{4}-\d{2}-\d{2}", str(a["birth_date"])) else f"{date.fromisoformat(a['birth_date']):%d.%m.%Y} года рождения")
    if a.get("birth_place"):
        parts.append(f"место рождения: {a['birth_place']}")
    if pas.get("series") or pas.get("number"):
        issued = f", выдан {pas.get('issued_by', '')}" if pas.get("issued_by") else ""
        issued += f" {date.fromisoformat(pas['issued_on']):%d.%m.%Y}" if pas.get("issued_on") and re.match(r"\d{4}-\d{2}-\d{2}", str(pas["issued_on"])) else (f" {pas['issued_on']}" if pas.get("issued_on") else "")
        issued += f", код подразделения {pas['code']}" if pas.get("code") else ""
        parts.append(f"паспорт {pas.get('series', '')} {pas.get('number', '')}{issued}".strip())
    if a.get("address"):
        parts.append(f"зарегистрирован(а) по адресу: {a['address']}")
    return ", ".join(parts)


def build_poa_docx(poa: PowerOfAttorney, path: str, draft: bool = False) -> str:
    doc = Document()
    _setup(doc)
    pr, a, lim = poa.principal or {}, poa.attorney or {}, poa.limits or {}
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(p, "ДОВЕРЕННОСТЬ", bold=True, color=ACCENT, size=18)
    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(p2, f"№ {poa.number or '________'}", bold=True, color=BLACK, size=12)
    row = doc.add_paragraph()
    _run(row, f"{poa.place or 'г. Москва'}", color=BLACK)
    row.add_run("\t" * 6)
    _run(row, date_words(poa.issued_on), color=BLACK)
    if draft:
        pd = doc.add_paragraph()
        _run(pd, "ПРОЕКТ — доверенность не оформлена (номер и дата присваиваются при оформлении)", color=ACCENT, size=8)
    doc.add_paragraph()
    doc.add_paragraph(f"{pr.get('name') or '________'} (ОГРН {pr.get('ogrn') or '________'}, ИНН {pr.get('inn') or '________'}{', КПП ' + pr['kpp'] if pr.get('kpp') else ''}, адрес: {pr.get('address') or '________'}), "
                      f"именуемое в дальнейшем «Доверитель», в лице {genitive_position(pr.get('signatory_position'))} {pr.get('signatory') or '________'}, действующего на основании {pr.get('signatory_basis') or 'Устава'}, "
                      f"настоящей доверенностью уполномочивает")
    pa = doc.add_paragraph()
    _run(pa, _attorney_text(a), bold=True, color=BLACK)
    doc.add_paragraph("(далее — «Представитель») представлять интересы Доверителя и совершать от его имени следующие действия:")
    for pw in resolve_powers(poa.powers or [], lim):
        doc.add_paragraph(f"{pw['n']}. {pw['text']};")
    extra = []
    if lim.get("counterparties"):
        extra.append(f"Полномочия ограничены отношениями со следующими контрагентами: {lim['counterparties']}.")
    if lim.get("territory"):
        extra.append(f"Территория действия доверенности: {lim['territory']}.")
    if lim.get("extra"):
        extra.append(str(lim["extra"]))
    for e in extra:
        doc.add_paragraph(e)
    doc.add_paragraph()
    term = f"Доверенность выдана сроком до {poa.valid_to:%d.%m.%Y}" if poa.valid_to else "Доверенность выдана сроком на один год"
    sub = "с правом передоверия" if lim.get("substitution") else "без права передоверия"
    doc.add_paragraph(f"{term} {sub}." + (" Доверенность выдана для совершения одного действия и прекращается после его совершения." if poa.kind == "single" else ""))
    doc.add_paragraph("Подпись Представителя _______________ удостоверяю.")
    doc.add_paragraph()
    doc.add_paragraph()
    tb = doc.add_table(rows=1, cols=2)
    c0, c1 = tb.rows[0].cells
    c0.text, c1.text = "", ""
    _run(c0.paragraphs[0], f"{pr.get('signatory_position') or 'Генеральный директор'}\n{pr.get('short') or pr.get('name') or ''}\n\n_______________ / {pr.get('signatory') or '________'} /\nМ.П.", color=BLACK, size=10)
    _run(c1.paragraphs[0], f"Представитель\n\n\n_______________ / {a.get('full_name') or '________'} /", color=BLACK, size=10)
    for s in doc.sections:
        f = s.footer.paragraphs[0]
        f.text = ""
        _run(f, f"Доверенность № {poa.number or '—'} · {pr.get('short') or pr.get('name') or ''} · CRM БЕРГ-ХАУС", color=ACCENT, size=8)
    doc.save(path)
    return path


def build_revocation_docx(poa: PowerOfAttorney, path: str, reason: str, on: date) -> str:
    doc = Document()
    _setup(doc)
    pr, a = poa.principal or {}, poa.attorney or {}
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(p, "УВЕДОМЛЕНИЕ ОБ ОТМЕНЕ ДОВЕРЕННОСТИ", bold=True, color=ACCENT, size=16)
    doc.add_paragraph(f"{pr.get('place') or ''}{date_words(on)}".strip())
    issued = poa.issued_on.strftime("%d.%m.%Y") if poa.issued_on else "—"
    valid = poa.valid_to.strftime("%d.%m.%Y") if poa.valid_to else "—"
    doc.add_paragraph(f"{pr.get('name') or '________'} (ИНН {pr.get('inn') or '—'}, ОГРН {pr.get('ogrn') or '—'}) в лице {genitive_position(pr.get('signatory_position'))} {pr.get('signatory') or '________'}, "
                      f"действующего на основании {pr.get('signatory_basis') or 'Устава'}, уведомляет об отмене с {on:%d.%m.%Y} доверенности № {poa.number} от {issued}, "
                      f"выданной {a.get('full_name') or '________'}" + (f" ({a['position']})" if a.get("position") else "") + f" сроком до {valid}.")
    if reason:
        doc.add_paragraph(f"Основание: {reason}.")
    doc.add_paragraph("С момента получения настоящего уведомления все действия, совершённые указанным лицом от имени Доверителя на основании отменённой доверенности, не имеют юридической силы. "
                      "Представителю надлежит немедленно возвратить оригинал доверенности Доверителю. Просим довести настоящее уведомление до сведения заинтересованных лиц.")
    doc.add_paragraph()
    doc.add_paragraph(f"{pr.get('signatory_position') or 'Генеральный директор'} _______________ / {pr.get('signatory') or '________'} /\nМ.П.")
    doc.save(path)
    return path


def to_pdf(docx_path: str, out_dir: str) -> Optional[str]:
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        return None
    subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", out_dir, docx_path], check=False, timeout=120, capture_output=True)
    pdf = os.path.join(out_dir, os.path.splitext(os.path.basename(docx_path))[0] + ".pdf")
    return pdf if os.path.isfile(pdf) else None


def template_data(poa: PowerOfAttorney) -> dict:
    """Данные для шаблона вида power_of_attorney / poa_revocation из библиотеки (группа переменных poa.*, строки powers[])."""
    pr, a, lim = poa.principal or {}, poa.attorney or {}, poa.limits or {}
    return {"poa": {"number": poa.number, "date": poa.issued_on.strftime("%d.%m.%Y") if poa.issued_on else "", "date_words": date_words(poa.issued_on), "valid_to": poa.valid_to.strftime("%d.%m.%Y") if poa.valid_to else "",
                    "place": poa.place, "kind": KINDS.get(poa.kind, poa.kind), "substitution": "с правом передоверия" if lim.get("substitution") else "без права передоверия",
                    "amount_limit": amount_clause(lim).strip(), "counterparties": lim.get("counterparties", ""), "territory": lim.get("territory", ""), "extra": lim.get("extra", "")},
            "principal": {"name": pr.get("name", ""), "short": pr.get("short", ""), "inn": pr.get("inn", ""), "kpp": pr.get("kpp", ""), "ogrn": pr.get("ogrn", ""), "address": pr.get("address", ""),
                          "signatory": pr.get("signatory", ""), "signatory_position": pr.get("signatory_position", ""), "signatory_basis": pr.get("signatory_basis", "")},
            "attorney": {"full_name": a.get("full_name", ""), "position": a.get("position", ""), "text": _attorney_text(a), "birth_date": a.get("birth_date", ""), "address": a.get("address", ""),
                         "passport": " ".join(x for x in ((a.get("passport") or {}).get("series", ""), (a.get("passport") or {}).get("number", "")) if x)},
            "powers": [{"n": p["n"], "text": p["text"]} for p in resolve_powers(poa.powers or [], lim)]}


# ------------------------------------------------------------------ напоминания

def check_expiring(db: Session, today: Optional[date] = None) -> int:
    """Раз в час: за 30 и за 7 дней до окончания и по истечении — уведомление юристам, ЗГД и представителю-сотруднику; статус «срок истёк»."""
    from .deal_service import notify
    today = today or date.today()
    n = 0
    users = db.scalars(select(User).where(User.is_active.is_(True))).all()
    bosses = [u for u in users if {"lawyer", "gd"} & set(u.roles or [])]
    for poa in db.scalars(select(PowerOfAttorney).where(PowerOfAttorney.status == "active")).all():
        dl = days_left(poa, today)
        if dl is None:
            continue
        key = "expired" if dl < 0 else "expiring_7" if dl <= 7 else "expiring_30" if dl <= 30 else None
        if key is None or key in (poa.notified or []):
            continue
        if key == "expired":
            refresh_status(poa, today)
        who = (poa.attorney or {}).get("full_name", "")
        text = (f"Доверенность № {poa.number} ({who}) — срок истёк {poa.valid_to:%d.%m.%Y}" if key == "expired"
                else f"Доверенность № {poa.number} ({who}) истекает {poa.valid_to:%d.%m.%Y} — через {dl} дн.; при необходимости оформите новую")
        for u in bosses:
            notify(db, None, u.id, "poa", None, text)
        if poa.attorney_user_id and all(u.id != poa.attorney_user_id for u in bosses):
            notify(db, None, poa.attorney_user_id, "poa", None, text)
        poa.notified = list(poa.notified or []) + [key]
        n += 1
    return n
