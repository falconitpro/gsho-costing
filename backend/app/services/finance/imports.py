"""Импорт банковской выписки CSV/XLSX (docs/finance/04, раздел 8): preview → confirm.

Поддерживается только реально реализованное: таблица CSV (разделитель `;`, `,` или табуляция; UTF-8 или Windows-1251) или XLSX
(первый лист), одна строка — одна операция. Колонки распознаются по заголовкам (Дата, Сумма / Приход–Расход / Дебет–Кредит,
Контрагент, Назначение, Номер документа, Валюта); сопоставление можно задать явно. Любой другой формат — понятная ошибка.

- preview ничего не проводит; повтор того же файла по счёту, уже подтверждённого, — предупреждение;
- строка с банковским номером, уже проведённым по счёту, — дубль; без номера — ключ по содержимому строки;
- confirm создаёт проведённые операции (`origin_type=bank_import`) только из выбранных валидных строк, повтор — тот же результат;
- валюта строки, если указана, должна совпадать с валютой счёта.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import FinanceAccount, FinanceImportBatch, FinanceTransaction
from ..deal_service import audit
from .money import dec, s

MSK = ZoneInfo("Europe/Moscow")
FIELDS = ("date", "amount", "inflow", "outflow", "direction", "counterparty", "purpose", "external_ref", "currency")
FIELD_TITLES = {"date": "Дата операции", "amount": "Сумма (± или с направлением)", "inflow": "Поступление / Кредит", "outflow": "Списание / Дебет",
                "direction": "Направление (приход/расход)", "counterparty": "Контрагент", "purpose": "Назначение платежа",
                "external_ref": "Номер документа / ID операции банка", "currency": "Валюта"}
HEADERS = {   # распознавание по подстроке заголовка (нижний регистр); порядок важен — более конкретные раньше
    "inflow": ("поступлени", "приход", "кредит", "зачислен", "credit"),
    "outflow": ("списани", "расход", "дебет", "debit"),
    "date": ("дата операции", "дата проводки", "дата", "date"),
    "amount": ("сумма", "amount"),
    "direction": ("направлен", "тип операции", "вид операции"),
    "counterparty": ("контрагент", "плательщик / получатель", "плательщик", "получатель", "корреспондент", "counterparty"),
    "purpose": ("назначени", "описание", "комментар", "purpose", "description"),
    "external_ref": ("номер документа", "№ документа", "номер п/п", "id операции", "идентификатор", "номер", "№", "reference", "ref"),
    "currency": ("валюта", "currency"),
}
SUMMARY = re.compile(r"итог|всего|оборот|остаток|сальдо|total|balance", re.I)
IN_WORDS = ("приход", "поступ", "зачисл", "кредит", "credit", "in", "+")
OUT_WORDS = ("расход", "списан", "дебет", "debit", "out", "-")


def _read_table(content: bytes, file_name: str) -> list[list[str]]:
    name = (file_name or "").lower()
    if name.endswith(".xlsx"):
        try:
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception:
            raise HTTPException(422, "Не удалось прочитать XLSX: файл повреждён или это не таблица Excel")
        ws = wb.worksheets[0]
        return [["" if v is None else (v.isoformat() if isinstance(v, (datetime, date)) else str(v)) for v in row] for row in ws.iter_rows(values_only=True)]
    if name.endswith(".csv") or name.endswith(".txt"):
        for enc in ("utf-8-sig", "cp1251"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise HTTPException(422, "Кодировка CSV не распознана: сохраните файл в UTF-8 или Windows-1251")
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        except csv.Error:
            class dialect(csv.excel):   # noqa: N801 — локальный диалект, глобальный csv.excel не трогаем
                delimiter = ";" if sample.count(";") >= sample.count(",") else ","
        return [row for row in csv.reader(io.StringIO(text), dialect)]
    raise HTTPException(422, "Поддерживаются выписки CSV и XLSX (одна строка — одна операция). Другие форматы банка пока не поддерживаются.")


def _header_row(rows: list[list[str]]) -> int:
    for i, row in enumerate(rows[:20]):
        low = [c.strip().lower() for c in row]
        if any(any(k in c for k in HEADERS["date"]) for c in low) and any(any(k in c for k in HEADERS["amount"] + HEADERS["inflow"] + HEADERS["outflow"]) for c in low):
            return i
    raise HTTPException(422, "Не найдена строка заголовков: нужны как минимум колонки «Дата» и «Сумма» (или «Поступление»/«Списание»). Укажите сопоставление колонок вручную.")


def detect_mapping(headers: list[str]) -> dict[str, int]:
    low = [h.strip().lower() for h in headers]
    used: set[int] = set()
    out: dict[str, int] = {}
    for field in ("inflow", "outflow", "date", "amount", "direction", "currency", "counterparty", "purpose", "external_ref"):
        for kw in HEADERS[field]:
            idx = next((i for i, h in enumerate(low) if i not in used and kw in h and not (field == "date" and "валют" in h)), None)
            if idx is not None:
                out[field] = idx
                used.add(idx)
                break
    return out


def _parse_date(v: str) -> Optional[date]:
    v = (v or "").strip()
    if not v:
        return None
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(v[:19], fmt).date()
        except ValueError:
            continue
    if re.fullmatch(r"\d{5}(\.0+)?", v):   # серийная дата Excel
        return date(1899, 12, 30) + timedelta(days=int(float(v)))
    return None


def _num(v: str) -> Optional[Decimal]:
    t = (v or "").strip().replace(" ", "").replace(" ", "")
    if not t:
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    if t.count(",") == 1 and t.count(".") >= 1 and t.rfind(",") > t.rfind("."):
        t = t.replace(".", "")
    t = t.replace(",", ".") if t.count(",") == 1 and "." not in t else t.replace(",", "")
    try:
        d = dec(t)
    except ValueError:
        return None
    return -d if (neg and d is not None) else d


def _row_key(account_id: str, r: dict) -> str:
    sig = "|".join([account_id, r["booked_on"], r["direction"], r["amount"], r.get("external_ref", ""), r.get("purpose", "")[:200], r.get("counterparty", "")[:100]])
    return hashlib.sha256(sig.encode()).hexdigest()[:40]


def preview(db: Session, user, account: FinanceAccount, file_name: str, content: bytes, mapping: Optional[dict] = None) -> FinanceImportBatch:
    if account.status != "active":
        raise HTTPException(409, "Счёт в архиве")
    if not content:
        raise HTTPException(422, "Файл пустой")
    sha = hashlib.sha256(content).hexdigest()
    table = [r for r in _read_table(content, file_name) if any((c or "").strip() for c in r)]
    if mapping and "header_row" in mapping:
        hdr = int(mapping["header_row"])
    else:
        hdr = _header_row(table)
    headers = [str(h or "").strip() for h in table[hdr]] if hdr < len(table) else []
    cols = {k: int(v) for k, v in ((mapping or {}).get("columns") or {}).items() if k in FIELDS and v not in (None, "")} or detect_mapping(headers)
    if "date" not in cols or not ({"amount"} <= set(cols) or {"inflow", "outflow"} & set(cols)):
        raise HTTPException(422, {"message": "Не удалось сопоставить колонки: нужны «Дата» и «Сумма» (или «Поступление»/«Списание»)",
                                  "headers": headers, "detected": cols})
    seen_refs: dict[str, int] = {}
    parsed = []
    for n, row in enumerate(table[hdr + 1:], start=hdr + 2):
        cell = lambda f: (str(row[cols[f]]).strip() if f in cols and cols[f] < len(row) and row[cols[f]] is not None else "")   # noqa: E731
        errs: list[str] = []
        d = _parse_date(cell("date"))
        if d is None:
            if not cell("amount") and not cell("inflow") and not cell("outflow"):
                continue   # пустые строки
            if SUMMARY.search(" ".join(str(x or "") for x in row[:3])):
                continue   # «Итого», «Обороты», «Остаток на конец» внизу выписки — не операции
            errs.append(f"дата не распознана: «{cell('date')}»")
        direction, amount = None, None
        if "inflow" in cols or "outflow" in cols:
            i, o = _num(cell("inflow")), _num(cell("outflow"))
            if i and i > 0 and not (o and o > 0):
                direction, amount = "in", i
            elif o and o > 0 and not (i and i > 0):
                direction, amount = "out", o
        if direction is None and "amount" in cols:
            a = _num(cell("amount"))
            if a is not None and a != 0:
                hint = cell("direction").lower()
                if hint:
                    direction = "in" if any(w in hint for w in IN_WORDS) and not any(w in hint for w in OUT_WORDS[:4]) else ("out" if any(w in hint for w in OUT_WORDS) else None)
                    if direction is None:
                        errs.append(f"направление не распознано: «{cell('direction')}»")
                else:
                    direction = "in" if a > 0 else "out"
                amount = abs(a)
        if amount is None or amount <= 0:
            errs.append("сумма не распознана или равна нулю")
        cur = cell("currency").upper()
        if cur and cur in ("RUR", "РУБ", "РУБ.", "RUB"):
            cur = "RUB"
        if cur and cur != account.currency:
            errs.append(f"валюта строки {cur} ≠ валюта счёта {account.currency}")
        ref = cell("external_ref")[:128]
        r = {"n": n, "booked_on": d.isoformat() if d else "", "direction": direction or "", "amount": s(amount) if amount else "",
             "counterparty": cell("counterparty")[:255], "purpose": cell("purpose")[:1000], "external_ref": ref, "currency": account.currency, "errors": errs}
        status = "error" if errs else "valid"
        if status == "valid":
            if ref and ref in seen_refs:
                status, r["errors"] = "error", [f"номер «{ref}» повторяется в файле (строка {seen_refs[ref]})"]
            elif ref and db.scalar(select(FinanceTransaction.id).where(FinanceTransaction.account_id == account.id, FinanceTransaction.external_ref == ref,
                                                                       FinanceTransaction.status.in_(("posted", "reversed")), FinanceTransaction.origin_type != "reversal")):
                status = "duplicate"
            else:
                r["key"] = _row_key(account.id, r)
                if db.scalar(select(FinanceTransaction.id).where(FinanceTransaction.idempotency_key == f"import:{account.id}:{r['key']}")):
                    status = "duplicate"
        if ref:
            seen_refs.setdefault(ref, n)
        r["status"] = status
        parsed.append(r)
    if not parsed:
        raise HTTPException(422, "В файле нет строк операций под заголовком")
    prev = db.scalar(select(FinanceImportBatch).where(FinanceImportBatch.account_id == account.id, FinanceImportBatch.file_sha256 == sha,
                                                      FinanceImportBatch.status == "confirmed"))
    b = FinanceImportBatch(account_id=account.id, file_name=(file_name or "")[:255], file_sha256=sha, mapping={"header_row": hdr, "columns": cols, "headers": headers},
                           rows=parsed, rows_total=len(parsed), rows_valid=sum(1 for r in parsed if r["status"] == "valid"),
                           rows_duplicates=sum(1 for r in parsed if r["status"] == "duplicate"), rows_errors=sum(1 for r in parsed if r["status"] == "error"),
                           status="preview", created_by=user.id, result={"same_file_confirmed_at": prev.confirmed_at.isoformat() if prev and prev.confirmed_at else None})
    db.add(b)
    db.flush()
    audit(db, user.id, None, "finance_import", b.id, "preview", after={"account_id": account.id, "file": b.file_name, "sha256": sha, "rows": b.rows_total,
                                                                       "valid": b.rows_valid, "duplicates": b.rows_duplicates, "errors": b.rows_errors})
    return b


def confirm(db: Session, user, b: FinanceImportBatch, rows: Optional[list[int]], key: str) -> FinanceImportBatch:
    if b.status == "confirmed":
        return b   # повтор подтверждения — тот же результат
    if b.status != "preview":
        raise HTTPException(409, "Пакет импорта отменён")
    other = db.scalar(select(FinanceImportBatch).where(FinanceImportBatch.idempotency_key == key))
    if other is not None and other.id != b.id:
        raise HTTPException(409, "Ключ идемпотентности уже использован другим импортом")
    a = db.get(FinanceAccount, b.account_id)
    if a is None or a.status != "active":
        raise HTTPException(409, "Счёт в архиве")
    chosen = set(rows) if rows is not None else None
    created, skipped, errors = [], 0, 0
    now = datetime.now(MSK)
    for r in b.rows:
        if chosen is not None and r["n"] not in chosen:
            continue
        if r["status"] == "error":
            errors += 1
            continue
        if r["status"] == "duplicate":
            skipped += 1
            continue
        ik = f"import:{a.id}:{r['key']}"
        if db.scalar(select(FinanceTransaction.id).where(FinanceTransaction.idempotency_key == ik)) or (
                r["external_ref"] and db.scalar(select(FinanceTransaction.id).where(FinanceTransaction.account_id == a.id, FinanceTransaction.external_ref == r["external_ref"],
                                                                                    FinanceTransaction.status.in_(("posted", "reversed")), FinanceTransaction.origin_type != "reversal"))):
            skipped += 1   # появилась после preview (параллельный импорт или ручной ввод)
            continue
        booked = datetime.combine(date.fromisoformat(r["booked_on"]), time(12, 0), tzinfo=MSK)
        tx = FinanceTransaction(account_id=a.id, direction=r["direction"], amount=Decimal(r["amount"]), currency=a.currency, booked_at=booked,
                                value_date=date.fromisoformat(r["booked_on"]), counterparty_name=r["counterparty"], purpose=r["purpose"], external_ref=r["external_ref"],
                                import_batch_id=b.id, status="posted", origin_type="bank_import", origin_id=f"{b.id}:{r['n']}", idempotency_key=ik,
                                created_by=user.id, posted_by=user.id, posted_at=now)
        db.add(tx)
        db.flush()
        created.append(tx.id)
    b.status, b.confirmed_at, b.idempotency_key = "confirmed", now, key
    b.result = {**(b.result or {}), "created": len(created), "skipped_duplicates": skipped, "errors": errors, "transaction_ids": created}
    audit(db, user.id, None, "finance_import", b.id, "confirm", after={**b.result, "idempotency_key": key})
    return b


def dto(b: FinanceImportBatch) -> dict:
    return {"id": b.id, "account_id": b.account_id, "file_name": b.file_name, "file_sha256": b.file_sha256, "mapping": b.mapping, "status": b.status,
            "rows_total": b.rows_total, "rows_valid": b.rows_valid, "rows_duplicates": b.rows_duplicates, "rows_errors": b.rows_errors,
            "rows": [{k: v for k, v in r.items() if k != "key"} for r in b.rows], "result": b.result, "fields": FIELD_TITLES,
            "totals": _totals(b.rows), "confirmed_at": b.confirmed_at.isoformat() if b.confirmed_at else None}


def _totals(rows: list[dict]) -> dict:
    t = {"in": Decimal(0), "out": Decimal(0)}
    for r in rows:
        if r["status"] == "valid":
            t[r["direction"]] += Decimal(r["amount"])
    return {k: s(v) for k, v in t.items()}
