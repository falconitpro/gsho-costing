"""Карточка компании для отправки контрагенту (юридический блок): реквизиты, банковские счета, подписант, контакты — DOCX в фирменном стиле."""
from __future__ import annotations

import os
import shutil
import subprocess
from datetime import date

from docx import Document
from docx.shared import Mm, Pt

from ..models import BankProfile, LegalProfile
from .kp_generator import ACCENT, BLACK, DEFAULT_LOGO, _heading, _run, _table

LANG_T = {"ru": "RU", "en": "EN", "zh": "中文"}


def _rows_party(p: LegalProfile) -> list[list[str]]:
    n = p.names or {}
    ru, en, zh = n.get("ru") or {}, n.get("en") or {}, n.get("zh") or {}
    is_ru = (p.country or "RU").upper() == "RU"
    rows = [["Полное наименование", ru.get("full", "")], ["Краткое наименование", ru.get("short", "")]]
    if en.get("full"):
        rows.append(["Наименование (EN)", en["full"]])
    if zh.get("full"):
        rows.append(["Наименование (中文)", zh["full"]])
    if p.opf:
        rows.append(["Организационно-правовая форма", p.opf])
    rows += [["ИНН" if is_ru else "Налоговый номер", p.tax_number], ["КПП", p.kpp] if is_ru else None, ["ОГРН" if is_ru else "Регистрационный номер", p.reg_number],
             ["Юридический адрес", p.legal_address], ["Фактический адрес", p.actual_address] if p.actual_address else None, ["Почтовый адрес", p.postal_address] if p.postal_address else None,
             ["Руководитель", p.director], ["Подписант", f"{p.signatory_name}{', ' + p.signatory_position if p.signatory_position else ''}{', действует на основании ' + p.signatory_basis if p.signatory_basis else ''}"]]
    for k, v in (p.extra or {}).items():
        if v and k not in ("requisites_set_id",):
            rows.append([{"okpo": "ОКПО", "okved": "ОКВЭД", "vat_number": "VAT", "business_license_no": "Бизнес-лицензия", "legal_representative": "Законный представитель"}.get(k, k), str(v)])
    return [r for r in rows if r and r[1]]


def build_company_card(p: LegalProfile, banks: list[BankProfile], path: str, logo: str | None = None) -> str:
    doc = Document()
    for s in doc.sections:
        s.left_margin = s.right_margin = Mm(18)
        s.top_margin = s.bottom_margin = Mm(15)
    logo = logo or DEFAULT_LOGO
    if logo and os.path.isfile(logo):
        doc.add_picture(logo, width=Mm(45))
    ru = (p.names or {}).get("ru") or {}
    _heading(doc, f"Карточка компании — {ru.get('short') or ru.get('full') or p.title}", 16)
    pr = doc.add_paragraph()
    _run(pr, f"Реквизиты для договоров и счетов · по состоянию на {date.today():%d.%m.%Y}", color=BLACK, size=9)
    _table(doc, ["Реквизит", "Значение"], _rows_party(p))
    active = [b for b in banks if b.status == "active"]
    if active:
        doc.add_paragraph()
        _heading(doc, "Банковские реквизиты", 12)
        for b in active:
            rows = [["Получатель", b.beneficiary or ru.get("full", "")], ["Банк", b.bank_name], ["Адрес банка", b.bank_address] if b.bank_address else None,
                    ["Счёт", b.account], ["БИК", b.bik] if b.bik else None, ["SWIFT", b.swift] if b.swift else None, ["Корр. счёт", b.corr_account] if b.corr_account else None,
                    ["Банк-корреспондент", b.corr_bank] if b.corr_bank else None, ["Валюта", b.currency], ["Назначение", b.purpose] if b.purpose else None]
            ph = doc.add_paragraph()
            _run(ph, f"{b.title or b.bank_name} · {b.currency}" + (" · реквизиты подтверждены" if b.verified else ""), bold=True, color=ACCENT, size=10)
            _table(doc, ["Реквизит", "Значение"], [r for r in rows if r and r[1]])
    if p.contacts:
        doc.add_paragraph()
        _heading(doc, "Контактные лица", 12)
        _table(doc, ["Имя", "Должность", "Телефон", "E-mail"], [[c.get("name", ""), c.get("position", ""), c.get("phone", ""), c.get("email", "")] for c in p.contacts if isinstance(c, dict)])
    doc.add_paragraph()
    pf = doc.add_paragraph()
    _run(pf, "Документ сформирован автоматически из карточки компании в CRM БЕРГ-ХАУС. При изменении реквизитов направляется новая карточка; "
             "изменение банковских реквизитов подтверждается подписанным письмом.", color=BLACK, size=8)
    for s in doc.sections:
        f = s.footer.paragraphs[0]
        f.text = ""
        _run(f, ru.get("short") or p.title, color=ACCENT, size=8)
    doc.save(path)
    return path


def to_pdf(docx_path: str, out_dir: str) -> str | None:
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        return None
    subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", out_dir, docx_path], check=False, timeout=120, capture_output=True)
    pdf = os.path.join(out_dir, os.path.splitext(os.path.basename(docx_path))[0] + ".pdf")
    return pdf if os.path.isfile(pdf) else None
