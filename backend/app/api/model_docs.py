"""Документы модели (руководства, чертежи, схемы, каталог запчастей) в каталоге и в сделке; ЗИП позиции сделки."""
from __future__ import annotations

import os
import tempfile
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .. import adapters
from ..core.auth import CurrentUser, get_current_user
from ..core.files import file_response
from ..core.roles import Role
from ..db import get_db
from ..models import EquipmentExecution, EquipmentModel, ModelDocument, Position, User
from ..services.acceptance_service import DOC_KINDS
from ..services.deal_service import audit
from .common import get_deal_or_404, serialize

router = APIRouter(prefix="/api", tags=["model-docs"])
LANGS = {"ru": "русский", "en": "английский", "zh": "китайский", "de": "немецкий", "other": "другой"}


def _writer(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.roles or user.roles <= {Role.OBSERVER}:
        raise HTTPException(403, "Наблюдатель не загружает документы")
    return user


def _doc_payload(db: Session, d: ModelDocument) -> dict:
    x = serialize(d)
    x.pop("storage_path", None)
    u = db.get(User, d.uploaded_by) if d.uploaded_by else None
    x.update(kind_title=DOC_KINDS.get(d.kind, d.kind), language_title=LANGS.get(d.language, d.language), uploaded_by_name=u.display_name if u else "",
             url=f"/api/catalog/documents/{d.id}")
    return x


def model_docs(db: Session, model_id: str) -> list[ModelDocument]:
    return db.scalars(select(ModelDocument).where(ModelDocument.model_id == model_id, ModelDocument.active.is_(True))
                      .order_by(ModelDocument.kind, ModelDocument.language, ModelDocument.uploaded_at.desc())).all()


def _store(db: Session, model: EquipmentModel, file: UploadFile, kind: str, language: str, version: str, title: str, comment: str,
           user: CurrentUser, deal_id: Optional[str] = None) -> ModelDocument:
    if kind not in DOC_KINDS:
        raise HTTPException(422, "Вид документа: руководство, каталог запчастей, чертежи, схемы, сертификаты или прочее")
    name = os.path.basename(file.filename or "document").replace("/", "-")
    fd, path = tempfile.mkstemp()
    with os.fdopen(fd, "wb") as f:
        f.write(file.file.read())
    try:
        size = os.path.getsize(path)
        stored = adapters.storage().upload(path, f"Каталог/{model.model.replace('/', '-')}/Документы", "07_fact",
                                           f"{kind}{('-' + language) if language else ''}{('-' + version) if version else ''} {name}")
    finally:
        os.unlink(path)
    d = ModelDocument(model_id=model.id, kind=kind, title=title.strip() or DOC_KINDS[kind], language=language or "ru", version=version.strip(),
                      file_name=name, storage_path=stored.path, size=size, source_deal_id=deal_id, comment=comment, uploaded_by=user.id)
    db.add(d)
    db.flush()
    audit(db, user.id, deal_id, "model_document", d.id, "upload", None, {"model": model.model, "kind": kind, "language": language, "version": version, "file": name})
    return d


@router.get("/catalog/models/{model_id}/documents")
def list_docs(model_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if db.get(EquipmentModel, model_id) is None:
        raise HTTPException(404)
    return [_doc_payload(db, d) for d in model_docs(db, model_id)]


@router.post("/catalog/models/{model_id}/documents", status_code=201)
def upload_doc(model_id: str, file: UploadFile = File(...), kind: str = Form("manual"), language: str = Form("ru"), version: str = Form(""),
               title: str = Form(""), comment: str = Form(""), db: Session = Depends(get_db), user: CurrentUser = Depends(_writer)):
    m = db.get(EquipmentModel, model_id)
    if m is None:
        raise HTTPException(404)
    d = _store(db, m, file, kind, language, version, title, comment, user)
    db.commit()
    return _doc_payload(db, d)


@router.get("/catalog/documents/{doc_id}")
def get_doc(doc_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    d = db.get(ModelDocument, doc_id)
    if d is None:
        raise HTTPException(404)
    fd, path = tempfile.mkstemp(suffix=os.path.splitext(d.file_name)[1])
    os.close(fd)
    adapters.storage().download(d.storage_path, path)
    return file_response(path, d.file_name, background=BackgroundTask(os.unlink, path))


@router.delete("/catalog/documents/{doc_id}")
def remove_doc(doc_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_writer)):
    """Убрать документ из модели (файл в хранилище остаётся): загрузивший или КД / ЗГД."""
    d = db.get(ModelDocument, doc_id)
    if d is None:
        raise HTTPException(404)
    if d.uploaded_by != user.id and not user.can("catalog", 2):
        raise HTTPException(403, "Убрать документ может загрузивший его или КД / ЗГД")
    d.active = False
    audit(db, user.id, d.source_deal_id, "model_document", d.id, "remove", None, None)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ в сделке: документы модели позиции

def position_model(db: Session, pos: Position, create: bool = False, user_id: Optional[str] = None) -> Optional[EquipmentModel]:
    """Модель каталога позиции: по исполнению, по ссылке позиции, по заводу и модели; при загрузке документа — заводится на проверку."""
    if pos.execution_id:
        ex = db.get(EquipmentExecution, pos.execution_id)
        if ex:
            return db.get(EquipmentModel, ex.model_id)
    if pos.equipment_model_id:
        m = db.get(EquipmentModel, pos.equipment_model_id)
        if m:
            return m
    m = db.scalar(select(EquipmentModel).where(EquipmentModel.factory_id == pos.factory_id, EquipmentModel.model == pos.model)) if pos.model else None
    if m is None and create:
        if not (pos.model or "").strip():
            raise HTTPException(422, "Укажите модель позиции — документы привязываются к модели")
        m = EquipmentModel(factory_id=pos.factory_id, machine_type=pos.machine_type or "", model=pos.model.strip(), trademark=pos.trademark or "",
                           status="pending", proposed_by=user_id)
        db.add(m)
        db.flush()
    if m is not None and not pos.equipment_model_id:
        pos.equipment_model_id = m.id
    return m


def _pos(db: Session, deal_id: str, pos_id: str, user: CurrentUser) -> Position:
    deal = get_deal_or_404(db, deal_id, user)
    pos = db.get(Position, pos_id)
    if pos is None or pos.deal_id != deal.id:
        raise HTTPException(404, "Позиция не найдена")
    return pos


@router.get("/deals/{deal_id}/positions/{pos_id}/documents")
def pos_docs(deal_id: str, pos_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    pos = _pos(db, deal_id, pos_id, user)
    m = position_model(db, pos)
    return {"model_id": m.id if m else None, "model": m.model if m else pos.model, "documents": [_doc_payload(db, d) for d in model_docs(db, m.id)] if m else []}


@router.post("/deals/{deal_id}/positions/{pos_id}/documents", status_code=201)
def pos_upload(deal_id: str, pos_id: str, file: UploadFile = File(...), kind: str = Form("manual"), language: str = Form("ru"), version: str = Form(""),
               title: str = Form(""), comment: str = Form(""), db: Session = Depends(get_db), user: CurrentUser = Depends(_writer)):
    """Документ загружается из сделки, но хранится у модели — виден в каталоге и во всех сделках с этой моделью."""
    pos = _pos(db, deal_id, pos_id, user)
    m = position_model(db, pos, create=True, user_id=user.id)
    d = _store(db, m, file, kind, language, version, title, comment, user, deal_id)
    db.commit()
    return _doc_payload(db, d)


# ------------------------------------------------------------------ ЗИП позиции

ZIP_KINDS = {"supply": "в комплекте поставки", "to": "ЗИП на ТО"}


class ZipItem(BaseModel):
    assembly: str = ""                 # узел / система машины
    article: str = ""
    name: str = ""
    unit: str = "шт"
    qty: float = Field(default=1, ge=0)
    price: Optional[float] = Field(default=None, ge=0)
    currency: str = "CNY"
    interval_hours: Optional[float] = Field(default=None, ge=0)   # периодичность замены, м·ч
    kind: str = "supply"               # supply — в комплекте поставки | to — ЗИП на ТО
    lead_days: Optional[int] = Field(default=None, ge=0)          # срок поставки, дн
    comment: str = ""


class ZipIn(BaseModel):
    items: list[ZipItem]


@router.get("/deals/{deal_id}/positions/{pos_id}/zip")
def get_zip(deal_id: str, pos_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    pos = _pos(db, deal_id, pos_id, user)
    return {"items": pos.zip_items or [], "kinds": ZIP_KINDS}


@router.put("/deals/{deal_id}/positions/{pos_id}/zip")
def put_zip(deal_id: str, pos_id: str, body: ZipIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """ЗИП позиции: в комплекте поставки (идёт в чек-лист приёмки) и на ТО (в ТО-комплекты парка машин)."""
    if not user.has(Role.MANAGER, Role.SERVICE_HEAD, Role.CD, Role.GD):
        raise HTTPException(403, "ЗИП ведут менеджер, сервис, КД и ЗГД")
    pos = _pos(db, deal_id, pos_id, user)
    items = []
    for z in body.items:
        if not (z.article.strip() or z.name.strip()):
            continue
        if z.kind not in ZIP_KINDS:
            raise HTTPException(422, "Вид ЗИП: в комплекте поставки или на ТО")
        items.append({**z.model_dump(), "article": z.article.strip(), "name": z.name.strip(), "currency": (z.currency or "CNY").upper()[:3]})
    before = len(pos.zip_items or [])
    pos.zip_items = items
    audit(db, user.id, deal_id, "position", pos.id, "zip", {"items": before}, {"items": len(items)})
    db.commit()
    return {"items": pos.zip_items, "kinds": ZIP_KINDS}


# ------------------------------------------------------------------ ЗИП: Excel (шаблон, загрузка, выгрузка на N часов)

ZIP_COLUMNS = [("kind", "Вид (поставка / ТО)"), ("assembly", "Узел / система"), ("article", "Артикул"), ("name", "Наименование"), ("unit", "Ед."),
               ("qty", "Кол-во на 1 замену"), ("price", "Цена"), ("currency", "Валюта"), ("interval_hours", "Периодичность замены, м·ч"),
               ("lead_days", "Срок поставки, дн"), ("comment", "Комментарий")]
_KIND_WORDS = {"supply": ("supply", "поставка", "в комплекте", "комплект"), "to": ("to", "то", "зип на то", "обслуживание")}


def _zip_kind(v) -> Optional[str]:
    s = str(v or "").strip().lower()
    if not s:
        return "supply"
    for k, words in _KIND_WORDS.items():
        if any(s.startswith(w) or w in s for w in words):
            return k
    return None


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ".").replace(" ", ""))
    except ValueError:
        raise ValueError(f"не число: {v}")


def _zip_workbook(rows: list[dict], title: str, hours: Optional[float] = None):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "ЗИП"
    heads = [t for _, t in ZIP_COLUMNS] + (["Замен за период", "Кол-во на период", "Сумма"] if hours is not None else [])
    ws.append([title])
    ws["A1"].font = Font(bold=True, size=12)
    ws.append(heads)
    for c in ws[2]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="FF3131")
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for r in rows:
        line = [ZIP_KINDS.get(r.get("kind", "supply"), "в комплекте поставки"), r.get("assembly", ""), r.get("article", ""), r.get("name", ""), r.get("unit", "шт"),
                r.get("qty"), r.get("price"), r.get("currency", "CNY"), r.get("interval_hours"), r.get("lead_days"), r.get("comment", "")]
        if hours is not None:
            line += [r.get("replacements"), r.get("qty_period"), r.get("amount")]
        ws.append(line)
    for i, w in enumerate([22, 18, 14, 36, 6, 14, 12, 8, 14, 12, 28, 12, 14, 14], start=1):
        ws.column_dimensions[ws.cell(row=2, column=i).column_letter].width = w
    ws.freeze_panes = "A3"
    return wb


def _send_wb(wb, name: str):
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    wb.save(path)
    return file_response(path, name, inline=False, background=BackgroundTask(os.unlink, path))


@router.get("/deals/{deal_id}/positions/{pos_id}/zip/template.xlsx")
def zip_template(deal_id: str, pos_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Шаблон Excel для загрузки ЗИП: заголовки + пример строк (текущий ЗИП позиции, если есть)."""
    pos = _pos(db, deal_id, pos_id, user)
    rows = pos.zip_items or [{"kind": "supply", "assembly": "Гидросистема", "article": "HF-1001", "name": "Фильтр гидравлический", "unit": "шт", "qty": 1, "price": 350, "currency": "CNY", "comment": "пример"},
                             {"kind": "to", "assembly": "Двигатель", "article": "OF-2201", "name": "Фильтр масляный", "unit": "шт", "qty": 2, "price": 120, "currency": "CNY", "interval_hours": 500, "lead_days": 30, "comment": "пример"}]
    return _send_wb(_zip_workbook(rows, f"ЗИП — {pos.model}"), f"zip_{pos.model}.xlsx".replace("/", "-"))


def parse_zip_xlsx(path: str) -> tuple[list[dict], list[str]]:
    """Читает лист ЗИП: ищет строку заголовков по словам «Артикул»/«Наименование», дальше строки до пустой. Возвращает (items, errors)."""
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    items, errors = [], []
    col: dict[str, int] = {}
    heads_by_word = {"вид": "kind", "узел": "assembly", "артикул": "article", "наимен": "name", "ед": "unit", "кол": "qty", "цена": "price", "валют": "currency",
                     "период": "interval_hours", "замен": "interval_hours", "м·ч": "interval_hours", "срок": "lead_days", "коммент": "comment"}
    for row in ws.iter_rows(values_only=True):
        vals = ["" if v is None else str(v).strip() for v in row]
        if not col:
            low = [v.lower() for v in vals]
            if any("артикул" in v for v in low) and any("наимен" in v for v in low):
                for i, v in enumerate(low):
                    for word, key in heads_by_word.items():
                        if word in v and key not in col:
                            col[key] = i
                            break
            continue
        if not any(vals):
            if items:
                break
            continue
        g = lambda k: (row[col[k]] if k in col and col[k] < len(row) else None)   # noqa: E731
        try:
            kind = _zip_kind(g("kind"))
            if kind is None:
                raise ValueError(f"вид ЗИП не распознан: «{g('kind')}» (нужно «поставка» или «ТО»)")
            it = {"kind": kind, "assembly": str(g("assembly") or "").strip(), "article": str(g("article") or "").strip(), "name": str(g("name") or "").strip(),
                  "unit": str(g("unit") or "шт").strip() or "шт", "qty": _num(g("qty")) or 1, "price": _num(g("price")), "currency": (str(g("currency") or "CNY").strip().upper()[:3]) or "CNY",
                  "interval_hours": _num(g("interval_hours")), "lead_days": int(_num(g("lead_days")) or 0) or None, "comment": str(g("comment") or "").strip()}
            if not (it["article"] or it["name"]):
                continue
            if it["kind"] == "to" and not it["interval_hours"]:
                errors.append(f"строка «{it['article'] or it['name']}»: для ЗИП на ТО нужна периодичность замены, м·ч")
            items.append(it)
        except ValueError as e:
            errors.append(f"строка «{vals[col.get('article', 0)] if vals else ''}»: {e}")
    if not col:
        errors.insert(0, "не найдена строка заголовков (нужны колонки «Артикул» и «Наименование») — скачайте шаблон")
    return items, errors


@router.post("/deals/{deal_id}/positions/{pos_id}/zip/import")
def zip_import(deal_id: str, pos_id: str, file: UploadFile = File(...), mode: str = Form("preview"), db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Загрузка ЗИП из Excel. mode=preview — только разбор и ошибки; replace — заменить ЗИП позиции; append — добавить к текущему."""
    if not user.has(Role.MANAGER, Role.SERVICE_HEAD, Role.CD, Role.GD):
        raise HTTPException(403, "ЗИП ведут менеджер, сервис, КД и ЗГД")
    if mode not in ("preview", "replace", "append"):
        raise HTTPException(422, "mode: preview | replace | append")
    pos = _pos(db, deal_id, pos_id, user)
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        with open(path, "wb") as f:
            f.write(file.file.read())
        try:
            items, errors = parse_zip_xlsx(path)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"файл не читается как Excel: {e}")
    finally:
        os.unlink(path)
    if mode == "preview":
        return {"items": items, "errors": errors, "current": len(pos.zip_items or [])}
    if errors:
        raise HTTPException(422, "в файле есть ошибки — исправьте и загрузите снова: " + "; ".join(errors[:5]))
    before = len(pos.zip_items or [])
    pos.zip_items = (list(pos.zip_items or []) if mode == "append" else []) + items
    audit(db, user.id, deal_id, "position", pos.id, "zip_import", {"items": before}, {"items": len(pos.zip_items), "mode": mode, "file": file.filename})
    db.commit()
    return {"items": pos.zip_items, "kinds": ZIP_KINDS, "errors": []}


def zip_for_hours(items: list[dict], hours: float) -> tuple[list[dict], dict]:
    """ЗИП на заданную наработку: для «ЗИП на ТО» число замен = floor(hours / interval); «в комплекте поставки» — как есть (1 раз)."""
    import math
    rows, total = [], {}
    for z in items:
        z = dict(z)
        if z.get("kind") == "to":
            iv = float(z.get("interval_hours") or 0)
            n = int(math.floor(hours / iv)) if iv > 0 else 0
        else:
            n = 1
        qty = float(z.get("qty") or 0) * n
        amount = round(qty * float(z["price"]), 2) if z.get("price") not in (None, "") else None
        z.update(replacements=n, qty_period=qty, amount=amount)
        if n <= 0:
            continue
        if amount is not None:
            total[z.get("currency", "CNY")] = round(total.get(z.get("currency", "CNY"), 0) + amount, 2)
        rows.append(z)
    return rows, total


@router.get("/deals/{deal_id}/positions/{pos_id}/zip/for-hours")
def zip_hours_json(deal_id: str, pos_id: str, hours: float = 2000, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    pos = _pos(db, deal_id, pos_id, user)
    rows, total = zip_for_hours(pos.zip_items or [], hours)
    return {"hours": hours, "items": rows, "totals": total}


@router.get("/deals/{deal_id}/positions/{pos_id}/zip/for-hours.xlsx")
def zip_hours_xlsx(deal_id: str, pos_id: str, hours: float = 2000, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Перечень ЗИП на заданное число моточасов — Excel для клиента/завода."""
    pos = _pos(db, deal_id, pos_id, user)
    rows, total = zip_for_hours(pos.zip_items or [], hours)
    wb = _zip_workbook(rows, f"ЗИП на {hours:g} м·ч — {pos.model}", hours=hours)
    ws = wb.active
    for cur, amt in total.items():
        ws.append([])
        ws.append(["Итого", "", "", "", "", "", "", cur, "", "", "", "", "", amt])
    return _send_wb(wb, f"zip_{hours:g}h_{pos.model}.xlsx".replace("/", "-"))
