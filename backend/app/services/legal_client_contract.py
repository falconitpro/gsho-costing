"""Договор заказчика → рабочая копия с реквизитами БЕРГ-ХАУС (юридический блок, этап 4).

Не «найти и заменить по всему документу»: сначала определяются блоки сторон (раздел «Поставщик / Продавец» и колонка таблицы реквизитов),
затем внутри них — строки реквизитов (ИНН, КПП, ОГРН, адрес, банк, счёт, БИК, к/с, подписант). Каждая находка получает уверенность:
high — внутри блока продавца; medium — место похоже на реквизит, но принадлежность стороне неочевидна (подставляется только после подтверждения
пользователем). Исходный файл не изменяется; рабочая копия сохраняет структуру и форматирование (замена через docx_fill._replace_in_paragraph-подобную логику по runs).
"""
from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass
from typing import Any, Optional

from docx import Document
from docx.text.paragraph import Paragraph

SELLER_WORDS = re.compile(r"\b(поставщик|продавец|исполнитель|подрядчик)\b", re.I)
BUYER_WORDS = re.compile(r"\b(покупатель|заказчик|клиент)\b", re.I)
REQ_FIELDS: list[tuple[str, re.Pattern, str]] = [
    ("inn", re.compile(r"^(ИНН)\s*[:№]?\s*(.*)$", re.I), "tax_number"),
    ("kpp", re.compile(r"^(КПП)\s*[:№]?\s*(.*)$", re.I), "kpp"),
    ("ogrn", re.compile(r"^(ОГРН(?:ИП)?)\s*[:№]?\s*(.*)$", re.I), "reg_number"),
    ("okpo", re.compile(r"^(ОКПО)\s*[:№]?\s*(.*)$", re.I), "okpo"),
    ("legal_address", re.compile(r"^(Юридический адрес|Юр\.?\s*адрес|Место нахождения|Адрес(?: места нахождения)?)\s*[:]?\s*(.*)$", re.I), "legal_address"),
    ("postal_address", re.compile(r"^(Почтовый адрес|Фактический адрес|Адрес для корреспонденции)\s*[:]?\s*(.*)$", re.I), "postal_address"),
    ("account", re.compile(r"^(Р/с|Р/сч|Расч[её]тный сч[её]т|Расч\.?\s*сч[её]т|Счёт|Счет)\s*[:№]?\s*(.*)$", re.I), "bank.account"),
    ("corr", re.compile(r"^(К/с|К/сч|Корр\.?\s*сч[её]т|Корреспондентский сч[её]т)\s*[:№]?\s*(.*)$", re.I), "bank.corr_account"),
    ("bik", re.compile(r"^(БИК)\s*[:№]?\s*(.*)$", re.I), "bank.bik"),
    ("bank", re.compile(r"^(Банк|Наименование банка|в банке)\s*[:]?\s*(.*)$", re.I), "bank.bank_name"),
    ("phone", re.compile(r"^(Тел\.?|Телефон)\s*[:]?\s*(.*)$", re.I), "phone"),
    ("email", re.compile(r"^(E-?mail|Эл\.?\s*почта|Электронная почта)\s*[:]?\s*(.*)$", re.I), "email"),
]
INLINE_RE = re.compile(r"(ИНН|КПП|ОГРН|БИК|р/с|к/с|расч[её]тный сч[её]т|корр\.?\s*сч[её]т)\s*[:№]?\s*([0-9]{5,25})", re.I)
NAME_LINE_RE = re.compile(r"^(Наименование|Полное наименование|Организация|Общество)\s*[:]?\s*(.*)$", re.I)
BLANK_RE = re.compile(r"_{3,}")
MULTI_RE = re.compile(r"(ИНН|КПП|ОГРН(?:ИП)?|БИК|ОКПО|Р/с|К/с|Расч[её]тный сч[её]т|Корр\.?\s*сч[её]т)\b", re.I)
LABEL_FIELD = {"инн": "tax_number", "кпп": "kpp", "огрн": "reg_number", "огрнип": "reg_number", "бик": "bank.bik", "окпо": "okpo", "р/с": "bank.account", "расчётныйсчёт": "bank.account",
               "расчетныйсчет": "bank.account", "к/с": "bank.corr_account", "коррсчёт": "bank.corr_account", "коррсчет": "bank.corr_account"}


@dataclass
class Replacement:
    id: str
    kind: str            # field key (inn, kpp, account …, name, signatory, blank)
    where: str           # "p:12" абзац | "t:0:r3:c1" ячейка
    old: str
    new: str
    confidence: str      # high | medium
    reason: str
    apply: bool = True   # для high — по умолчанию да; medium — только после подтверждения


def _party_value(party: dict, key: str) -> str:
    cur: Any = party
    for part in key.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None:
            return ""
    return str(cur or "")


def _paragraph_nodes(doc: Document):
    """Все абзацы с адресом (абзацы тела — p:i; ячейки таблиц — t:ti:r:c:p) и текущей «стороной» контекста."""
    nodes = []
    for i, p in enumerate(doc.paragraphs):
        nodes.append((f"p:{i}", p, None))
    for ti, t in enumerate(doc.tables):
        # сторона колонки — по заголовкам первой строки или по первой непустой ячейке колонки
        col_side: dict[int, str] = {}
        for ci in range(len(t.columns)):
            texts = " ".join(t.cell(ri, ci).text for ri in range(min(2, len(t.rows))))
            if SELLER_WORDS.search(texts) and not BUYER_WORDS.search(texts):
                col_side[ci] = "seller"
            elif BUYER_WORDS.search(texts) and not SELLER_WORDS.search(texts):
                col_side[ci] = "buyer"
        for ri, row in enumerate(t.rows):
            for ci, cell in enumerate(row.cells):
                for pi, p in enumerate(cell.paragraphs):
                    nodes.append((f"t:{ti}:{ri}:{ci}:{pi}", p, col_side.get(ci)))
    return nodes


def plan_replacements(path: str, seller: dict) -> tuple[list[Replacement], dict]:
    """План подстановки реквизитов продавца. seller — party dict (как в legal_packages.party_from_profile)."""
    doc = Document(path)
    nodes = _paragraph_nodes(doc)
    reps: list[Replacement] = []
    side = None                 # текущая сторона по заголовкам в теле документа
    in_requisites = False       # раздел «Адреса и реквизиты сторон»
    stats = {"paragraphs": len(doc.paragraphs), "tables": len(doc.tables), "seller_blocks": 0, "buyer_blocks": 0}
    for addr, p, col_side in nodes:
        text = (p.text or "").strip()
        if not text:
            continue
        if addr.startswith("p:"):
            if re.search(r"реквизиты|адреса и реквизиты|юридические адреса", text, re.I) and len(text) < 80:
                in_requisites = True
            is_seller_head = bool(SELLER_WORDS.search(text)) and not BUYER_WORDS.search(text) and len(text) < 60
            is_buyer_head = bool(BUYER_WORDS.search(text)) and not SELLER_WORDS.search(text) and len(text) < 60
            if is_seller_head:
                side = "seller"
                stats["seller_blocks"] += 1
            elif is_buyer_head:
                side = "buyer"
                stats["buyer_blocks"] += 1
            cur_side = side
        else:
            cur_side = col_side
            if cur_side is None:
                # ячейка без определённой колонки — если в ячейке есть маркер стороны, используем его
                if SELLER_WORDS.search(text) and not BUYER_WORDS.search(text):
                    cur_side = "seller"
                elif BUYER_WORDS.search(text):
                    cur_side = "buyer"
        if cur_side == "buyer":
            continue
        conf = "high" if cur_side == "seller" else "medium"
        # несколько реквизитов в одной строке: «ИНН ______ КПП ______» → собираем строку заново по меткам
        labels = list(MULTI_RE.finditer(text))
        if len(labels) > 1 and (cur_side == "seller" or in_requisites):
            parts = []
            for j, m in enumerate(labels):
                lbl = m.group(1)
                field = LABEL_FIELD.get(re.sub(r"[\s.]", "", lbl.lower()))
                val = _party_value(seller, field) if field else ""
                sep = ":" if ":" in text[m.end():m.end() + 2] else ""
                parts.append(f"{lbl}{sep} {val}".rstrip() if val else text[m.start():(labels[j + 1].start() if j + 1 < len(labels) else len(text))].strip())
            new_text = " ".join(parts)
            if new_text != text:
                reps.append(Replacement(id=f"{addr}:multi", kind="inline", where=addr, old=text, new=new_text, confidence=conf, reason="несколько реквизитов в строке" + ("" if conf == "high" else " — подтвердите"), apply=(conf == "high")))
            continue
        # строки «ИНН: …», «Банк: …» (с заменой значения после ключа)
        matched = False
        for key, rx, field in REQ_FIELDS:
            m = rx.match(text)
            if not m:
                continue
            new_val = _party_value(seller, field)
            if not new_val:
                break
            old_val = m.group(2).strip()
            if old_val and old_val == new_val:
                break
            new_text = f"{m.group(1)}: {new_val}" if ":" in text[:len(m.group(1)) + 3] or not old_val else text.replace(old_val, new_val, 1)
            reps.append(Replacement(id=f"{addr}:{key}", kind=key, where=addr, old=text, new=new_text, confidence=conf,
                                    reason="блок продавца" if conf == "high" else "сторона не определена — подтвердите", apply=(conf == "high")))
            matched = True
            break
        if matched:
            continue
        # встроенные реквизиты в одной строке: «ИНН 7707083893, КПП 770701001»
        if INLINE_RE.search(text) and (cur_side == "seller" or in_requisites):
            new_text = text
            for m in INLINE_RE.finditer(text):
                lbl = m.group(1).lower()
                field = "tax_number" if lbl == "инн" else "kpp" if lbl == "кпп" else "reg_number" if lbl == "огрн" else "bank.bik" if lbl == "бик" else "bank.account" if lbl.startswith("р/с") or "расч" in lbl else "bank.corr_account"
                val = _party_value(seller, field)
                if val:
                    new_text = new_text.replace(m.group(2), val, 1)
            if new_text != text:
                reps.append(Replacement(id=f"{addr}:inline", kind="inline", where=addr, old=text, new=new_text, confidence=conf, reason="реквизиты в строке" + ("" if conf == "high" else " — подтвердите"), apply=(conf == "high")))
            continue
        # наименование продавца / прочерки в блоке продавца
        if cur_side == "seller":
            if BLANK_RE.search(text) and len(text) < 200:
                new_text = BLANK_RE.sub(seller.get("name_ru") or "", text, count=1) if NAME_LINE_RE.match(text) or SELLER_WORDS.search(text) else text
                if new_text != text:
                    reps.append(Replacement(id=f"{addr}:blank", kind="name", where=addr, old=text, new=new_text, confidence="medium", reason="прочерк в блоке продавца — подтвердите", apply=False))
            m = NAME_LINE_RE.match(text)
            if m and seller.get("name_ru") and m.group(2).strip() != seller["name_ru"]:
                reps.append(Replacement(id=f"{addr}:name", kind="name", where=addr, old=text, new=f"{m.group(1)}: {seller['name_ru']}", confidence="high", reason="наименование в блоке продавца"))
            if re.match(r"^(Генеральный директор|Директор|Руководитель|Должность)\b", text, re.I) and seller.get("signatory"):
                reps.append(Replacement(id=f"{addr}:sign", kind="signatory", where=addr, old=text, new=f"{seller.get('signatory_position') or 'Генеральный директор'}: {seller['signatory']}", confidence="medium", reason="подписант продавца — подтвердите", apply=False))
    # преамбула: «…, и ООО «___», именуемое в дальнейшем «Поставщик», в лице …, действующего на основании …» —
    # наименование берётся из сегмента непосредственно перед фразой о Поставщике, подписант — только из части ПОСЛЕ неё (не трогаем сторону Покупателя)
    for i, p in enumerate(doc.paragraphs[:40]):
        t = p.text or ""
        m = re.search(r",?\s*именуем\w+\s+в\s+дальнейшем\s*[«\"]?(Поставщик|Продавец|Исполнитель)[»\"]?", t, re.I)
        if not m or not seller.get("name_ru"):
            continue
        pre = t[:m.start()]
        seg = re.split(r",\s*и\s+|\bи\s+(?=[A-ZА-ЯЁ«\"_])", pre)[-1].strip()       # «ООО «___»» или «ООО «Старое имя»»
        new_t = t
        if seg and seller["name_ru"] not in seg and (BLANK_RE.search(seg) or re.search(r"\b(ООО|АО|ПАО|ЗАО|ИП)\b|«", seg)):
            new_t = t[:m.start() - len(seg)] + seller["name_ru"] + t[m.start():] if t[:m.start()].endswith(seg) else t.replace(seg, seller["name_ru"], 1)
        tail_start = len(new_t) - (len(t) - m.end())
        tail = new_t[tail_start:]
        sig = re.search(r"в\s+лице\s+(.{3,120}?),?\s*действующ\w+\s+на\s+основании\s+([^,;.]{2,80})", tail, re.I)
        if sig and seller.get("signatory"):
            new_tail = tail[:sig.start(1)] + f"{seller.get('signatory_position', '')} {seller['signatory']}".strip() + tail[sig.end(1):sig.start(2)] + (seller.get("signatory_basis") or sig.group(2)) + tail[sig.end(2):]
            new_t = new_t[:tail_start] + new_tail
        if new_t != t:
            reps.append(Replacement(id=f"p:{i}:preamble", kind="preamble", where=f"p:{i}", old=t, new=new_t, confidence="medium", reason="преамбула: наименование и подписант Поставщика — подтвердите", apply=False))
        break
    # дедупликация по адресу (одна замена на абзац)
    seen, uniq = set(), []
    for r in reps:
        if r.where in seen:
            continue
        seen.add(r.where)
        uniq.append(r)
    stats["high"] = sum(1 for r in uniq if r.confidence == "high")
    stats["medium"] = sum(1 for r in uniq if r.confidence == "medium")
    return uniq, stats


def _set_paragraph_text(p: Paragraph, new: str) -> None:
    """Текст целиком в первый run (его форматирование), остальные очищаются — для коротких строк реквизитов это сохраняет вид строки."""
    runs = p.runs
    if not runs:
        p.add_run(new)
        return
    runs[0].text = new
    for r in runs[1:]:
        r.text = ""


def _resolve(doc: Document, where: str) -> Optional[Paragraph]:
    parts = where.split(":")
    try:
        if parts[0] == "p":
            return doc.paragraphs[int(parts[1])]
        _, ti, ri, ci, pi = parts
        return doc.tables[int(ti)].rows[int(ri)].cells[int(ci)].paragraphs[int(pi)]
    except (IndexError, ValueError):
        return None


def make_working_copy(src: str, dst: str, reps: list[Replacement], approved_ids: set[str], header_note: str = "") -> dict:
    """Рабочая копия: применяются high-замены и подтверждённые medium; остальное не трогается. Возвращает отчёт."""
    doc = Document(src)
    applied, skipped, pending = [], [], []
    for r in reps:
        ok = r.confidence == "high" or r.id in approved_ids
        if r.id in approved_ids:
            ok = True
        if not ok:
            pending.append(r.id)
            continue
        p = _resolve(doc, r.where)
        if p is None or (p.text or "").strip() != r.old.strip():
            skipped.append(r.id)
            continue
        _set_paragraph_text(p, r.new)
        applied.append(r.id)
    if header_note:
        for s in doc.sections:
            hp = s.header.paragraphs[0] if s.header.paragraphs else s.header.add_paragraph()
            hp.text = (hp.text + "  " if hp.text else "") + header_note
    doc.save(dst)
    return {"applied": applied, "skipped": skipped, "pending": pending}


def replacements_payload(reps: list[Replacement]) -> list[dict]:
    return [asdict(r) for r in reps]
