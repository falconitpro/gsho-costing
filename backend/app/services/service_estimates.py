"""Смета наряда и документы-снимки (I-3, D-198).

Смета:
- версии: правится только черновик; «отправить клиенту» фиксирует суммы и формирует документ-снимок сметы;
- решение клиента (согласовал / отказал) записывает руководитель сервиса — с ФИО и основанием;
- новая версия копирует строки последней, прежняя становится «заменённой» (в том числе согласованная — с причиной);
- пустая цена — «не задана» (не 0): такую смету не отправить;
- суммы без НДС, НДС — по ставке сметы (по умолчанию «НДС с продажи» из настроек). Валюта — одна на смету, без пересчёта:
  цена детали из каталога подставляется, только если её валюта совпадает с валютой сметы.

Документы — снимки: данные на момент формирования (snapshot) и файл DOCX. Не меняются, новый документ — новая запись.
Шаблоны юристом не проверены (legal_checked = false) — в файле пометка «Черновик. Шаблон не проверен юристом».
"""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.clock import today as msk_today
from ..models import (Client, ClientMachine, ClientSite, Part, ServiceDocument, ServiceEstimate, ServiceEstimateLine, ServiceRequest,
                      User, WorkOrder, WorkOrderChecklistItem, WorkOrderWork)

STATUSES = {"draft": "Черновик", "sent": "Отправлена клиенту", "approved": "Согласована", "rejected": "Отклонена клиентом",
            "superseded": "Заменена новой версией"}
CHANNELS = {"email": "Почта", "messenger": "Мессенджер", "paper": "На бумаге", "other": "Другое"}
KINDS = {"work": "Работа", "part": "Запчасть", "other": "Прочее"}
DOC_KINDS = {"defect_list": "Дефектная ведомость", "estimate": "Смета", "act": "Акт выполненных работ"}
DOC_PREFIX = {"defect_list": "ДВ", "estimate": "СМ", "act": "АВР"}
DRAFT_MARK = "Черновик. Шаблон не проверен юристом."


class EstimateError(Exception):
    pass


def line_net(ln: ServiceEstimateLine) -> Optional[float]:
    if ln.price is None:
        return None
    return round((ln.qty or 0) * ln.price * (1 - (ln.discount_pct or 0) / 100), 2)


def totals(est: ServiceEstimate, lines: list[ServiceEstimateLine]) -> dict:
    nets = [line_net(x) for x in lines]
    missing = sum(1 for n in nets if n is None)
    net = round(sum(n for n in nets if n is not None), 2)
    vat = round(net * (est.vat_pct or 0) / 100, 2)
    return {"net": net, "vat": vat, "total": round(net + vat, 2), "missing_prices": missing,
            "by_kind": {k: round(sum(n for x, n in zip(lines, nets) if x.kind == k and n is not None), 2) for k in KINDS}}


def lines_of(db: Session, est: ServiceEstimate) -> list[ServiceEstimateLine]:
    return db.scalars(select(ServiceEstimateLine).where(ServiceEstimateLine.estimate_id == est.id).order_by(ServiceEstimateLine.position)).all()


def latest(db: Session, wo: WorkOrder) -> Optional[ServiceEstimate]:
    return db.scalar(select(ServiceEstimate).where(ServiceEstimate.work_order_id == wo.id).order_by(ServiceEstimate.version.desc()).limit(1))


def _default_vat(db: Session) -> float:
    from . import settings_service
    try:
        return float(settings_service.get(db, "sales_vat_pct"))
    except Exception:
        return 22.0


def _last_rate(db: Session) -> Optional[float]:
    """Ставка нормо-часа — из последней сметы, где она задана (своей настройки нет: ставку задаёт руководитель сервиса)."""
    return db.scalar(select(ServiceEstimate.hour_rate).where(ServiceEstimate.hour_rate.is_not(None))
                     .order_by(ServiceEstimate.created_at.desc()).limit(1))


def lines_from_order(db: Session, wo: WorkOrder, rate: Optional[float]) -> list[dict]:
    """Строки работ из наряда: часы — факт, если указан, иначе норматив; цена — ставка нормо-часа."""
    out = []
    for w in db.scalars(select(WorkOrderWork).where(WorkOrderWork.work_order_id == wo.id).order_by(WorkOrderWork.position)).all():
        hours = w.actual_hours if w.actual_hours is not None else w.norm_hours
        out.append({"kind": "work", "work_id": w.id, "title": w.title, "qty": hours if hours is not None else 1.0, "unit": "н·ч",
                    "price": rate, "note": "" if hours is not None else "часы не указаны — уточните"})
    return out


def create_version(db: Session, wo: WorkOrder, user_id: str, reason: str = "") -> ServiceEstimate:
    prev = latest(db, wo)
    if prev is not None and prev.status == "draft":
        raise EstimateError("Черновик сметы уже есть — правьте его")
    if prev is not None and prev.status == "approved" and not reason.strip():
        raise EstimateError("Смета уже согласована. Новая версия — только с причиной")
    est = ServiceEstimate(id=str(uuid.uuid4()), work_order_id=wo.id, version=(prev.version + 1) if prev else 1, status="draft",
                          currency=prev.currency if prev else "RUB", vat_pct=prev.vat_pct if prev else _default_vat(db),
                          hour_rate=prev.hour_rate if prev else _last_rate(db), note=prev.note if prev else "",
                          reason=reason.strip()[:2000], created_by=user_id)
    db.add(est)
    db.flush()
    if prev is not None:
        for ln in lines_of(db, prev):
            db.add(ServiceEstimateLine(estimate_id=est.id, position=ln.position, kind=ln.kind, part_id=ln.part_id, work_id=ln.work_id,
                                       title=ln.title, article=ln.article, qty=ln.qty, unit=ln.unit, price=ln.price,
                                       discount_pct=ln.discount_pct, note=ln.note))
        if prev.status in ("sent", "approved", "rejected"):
            prev.status = "superseded"
    else:
        for i, d in enumerate(lines_from_order(db, wo, est.hour_rate), start=1):
            db.add(ServiceEstimateLine(estimate_id=est.id, position=i, **d))
    db.flush()
    return est


def set_lines(db: Session, est: ServiceEstimate, rows: list[dict]) -> None:
    if est.status != "draft":
        raise EstimateError("Править можно только черновик сметы")
    for ln in lines_of(db, est):
        db.delete(ln)
    db.flush()
    for i, r in enumerate(rows, start=1):
        kind = r.get("kind") or "other"
        if kind not in KINDS:
            raise EstimateError("Вид строки — работа, запчасть или прочее")
        part = db.get(Part, r["part_id"]) if r.get("part_id") else None
        if r.get("part_id") and part is None:
            raise EstimateError("Нет такой запчасти в каталоге")
        title = str(r.get("title") or (part.name_ru if part else "")).strip()
        if not title:
            raise EstimateError(f"Строка {i}: нужно наименование")
        qty, price, disc = r.get("qty", 1), r.get("price"), r.get("discount_pct") or 0
        if not isinstance(qty, (int, float)) or qty <= 0:
            raise EstimateError(f"Строка {i}: количество больше нуля")
        if price is not None and (not isinstance(price, (int, float)) or price < 0):
            raise EstimateError(f"Строка {i}: цена — число от 0")
        if not (0 <= disc <= 100):
            raise EstimateError(f"Строка {i}: скидка от 0 до 100 %")
        work = r.get("work_id")
        if work and (w := db.get(WorkOrderWork, work)) is not None and w.work_order_id != est.work_order_id:
            raise EstimateError(f"Строка {i}: работа из другого наряда")
        db.add(ServiceEstimateLine(estimate_id=est.id, position=i, kind=kind, part_id=part.id if part else None, work_id=work or None,
                                   title=title[:500], article=(part.article if part else str(r.get("article") or ""))[:128],
                                   qty=float(qty), unit=str(r.get("unit") or ("н·ч" if kind == "work" else part.unit if part else "шт"))[:16],
                                   price=float(price) if price is not None else None, discount_pct=float(disc), note=str(r.get("note") or "")[:2000]))
    db.flush()


def send(db: Session, est: ServiceEstimate, user_id: str, channel: str) -> ServiceDocument:
    if est.status != "draft":
        raise EstimateError("Отправить можно только черновик")
    if channel not in CHANNELS:
        raise EstimateError("Способ отправки — из списка")
    lines = lines_of(db, est)
    if not lines:
        raise EstimateError("В смете нет строк")
    t = totals(est, lines)
    if t["missing_prices"]:
        raise EstimateError(f"Не у всех строк есть цена: {t['missing_prices']}")
    est.total_net, est.total_vat, est.total = t["net"], t["vat"], t["total"]
    est.status, est.sent_at, est.sent_channel = "sent", datetime.now(timezone.utc), channel
    db.flush()
    wo = db.get(WorkOrder, est.work_order_id)
    return make_document(db, wo, "estimate", user_id, est)


def decide(db: Session, est: ServiceEstimate, user_id: str, decision: str, client_name: str, basis: str) -> None:
    if est.status != "sent":
        raise EstimateError("Решение клиента записывается по отправленной смете")
    if decision not in ("approved", "rejected"):
        raise EstimateError("Решение — согласовал или отказал")
    if not client_name.strip() or not basis.strip():
        raise EstimateError("Укажите, кто у клиента принял решение, и основание (письмо, подпись)")
    est.status, est.decided_at, est.decided_by = decision, datetime.now(timezone.utc), user_id
    est.client_name, est.decision_basis = client_name.strip()[:255], basis.strip()[:2000]


# ─────────────── документы-снимки ───────────────

def _number(db: Session, kind: str) -> str:
    p = f"{DOC_PREFIX[kind]}-{msk_today().year}-"
    last = db.scalars(select(ServiceDocument.number).where(ServiceDocument.number.like(f"{p}%"))).all()
    n = max([int(x.rsplit("-", 1)[1]) for x in last if x.rsplit("-", 1)[1].isdigit()] or [0]) + 1
    return f"{p}{n:04d}"


def snapshot(db: Session, wo: WorkOrder, kind: str, est: Optional[ServiceEstimate]) -> dict:
    c = db.get(Client, wo.client_id)
    s = db.get(ClientSite, wo.site_id) if wo.site_id else None
    m = db.get(ClientMachine, wo.machine_id) if wo.machine_id else None
    r = db.get(ServiceRequest, wo.request_id) if wo.request_id else None
    eng = db.get(User, wo.engineer_id) if wo.engineer_id else None
    works = db.scalars(select(WorkOrderWork).where(WorkOrderWork.work_order_id == wo.id).order_by(WorkOrderWork.position)).all()
    items = db.scalars(select(WorkOrderChecklistItem).where(WorkOrderChecklistItem.work_order_id == wo.id).order_by(WorkOrderChecklistItem.position)).all()
    from .work_orders import BILLING
    d = {"kind": kind, "title": DOC_KINDS[kind], "made_on": msk_today().isoformat(), "order": wo.number, "status": wo.status,
         "billing": wo.billing, "billing_title": BILLING.get(wo.billing, wo.billing),
         "client": c.name if c else "", "site": (s.name + (f", {s.address}" if s.address else "")) if s else "",
         "machine": " ".join(x for x in ((m.manufacturer, m.model, f"зав. № {m.serial}" if m.serial else "") if m else ()) if x),
         "request": r.number if r else "", "complaint": (r.description if r else wo.description) or "",
         "diagnosis": wo.diagnosis or "", "engineer": eng.display_name if eng else "",
         "works": [{"title": w.title, "norm_hours": w.norm_hours, "actual_hours": w.actual_hours, "done": w.done, "note": w.note} for w in works],
         "checklist": [{"text": i.text, "done": i.done} for i in items],
         "signed_by_name": wo.signed_by_name, "closed_at": wo.closed_at.isoformat() if wo.closed_at else None,
         "legal_checked": False}
    if est is not None:
        lines = lines_of(db, est)
        d["estimate"] = {"version": est.version, "status": est.status, "currency": est.currency, "vat_pct": est.vat_pct,
                         "lines": [{"kind": x.kind, "title": x.title, "article": x.article, "qty": x.qty, "unit": x.unit, "price": x.price,
                                    "discount_pct": x.discount_pct, "net": line_net(x)} for x in lines],
                         "totals": totals(est, lines), "client_name": est.client_name, "decided_at": est.decided_at.isoformat() if est.decided_at else None}
    return d


def _docx(snap: dict, number: str, path: str, with_prices: bool) -> None:
    from docx import Document
    from docx.shared import Pt, RGBColor
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Arial", Pt(10)
    mark = doc.add_paragraph()
    run = mark.add_run(DRAFT_MARK)
    run.bold, run.font.color.rgb = True, RGBColor(0xC0, 0x1E, 0x2E)
    doc.add_heading(f"{snap['title']} № {number} от {date.fromisoformat(snap['made_on']).strftime('%d.%m.%Y')}", level=1)
    info = [("Заказ-наряд", snap["order"]), ("Обращение", snap["request"]), ("Клиент", snap["client"]), ("Площадка", snap["site"]),
            ("Машина", snap["machine"]), ("Вид оплаты", snap["billing_title"]), ("Исполнитель", snap["engineer"])]
    t = doc.add_table(rows=0, cols=2)
    t.style = "Table Grid"
    for k, v in info:
        if v:
            row = t.add_row().cells
            row[0].text, row[1].text = k, str(v)
    if snap["kind"] in ("defect_list", "act") and snap["complaint"]:
        doc.add_heading("Заявленная неисправность", level=2)
        doc.add_paragraph(snap["complaint"])
    if snap["kind"] in ("defect_list", "act") and snap["diagnosis"]:
        doc.add_heading("Заключение диагностики", level=2)
        doc.add_paragraph(snap["diagnosis"])
    if snap["kind"] in ("defect_list", "act") and snap["works"]:
        doc.add_heading("Работы" if snap["kind"] == "act" else "Необходимые работы", level=2)
        wt = doc.add_table(rows=1, cols=4)
        wt.style = "Table Grid"
        for c, h in zip(wt.rows[0].cells, ("№", "Работа", "Норматив, ч", "Факт, ч" if snap["kind"] == "act" else "Примечание")):
            c.text = h
        for i, w in enumerate(snap["works"], 1):
            r = wt.add_row().cells
            r[0].text, r[1].text = str(i), w["title"]
            r[2].text = "" if w["norm_hours"] is None else f"{w['norm_hours']:g}"
            r[3].text = ("" if w["actual_hours"] is None else f"{w['actual_hours']:g}") if snap["kind"] == "act" else (w["note"] or "")
    est = snap.get("estimate")
    if est:
        parts = [x for x in est["lines"] if x["kind"] == "part"]
        if snap["kind"] == "defect_list" and parts:
            doc.add_heading("Необходимые запчасти", level=2)
            pt = doc.add_table(rows=1, cols=4)
            pt.style = "Table Grid"
            for c, h in zip(pt.rows[0].cells, ("№", "Наименование", "Артикул", "Кол-во")):
                c.text = h
            for i, x in enumerate(parts, 1):
                r = pt.add_row().cells
                r[0].text, r[1].text, r[2].text, r[3].text = str(i), x["title"], x["article"], f"{x['qty']:g} {x['unit']}"
        if with_prices:
            doc.add_heading(f"Смета, версия {est['version']}", level=2)
            et = doc.add_table(rows=1, cols=6)
            et.style = "Table Grid"
            for c, h in zip(et.rows[0].cells, ("№", "Наименование", "Кол-во", f"Цена без НДС, {est['currency']}", "Скидка, %", f"Сумма без НДС, {est['currency']}")):
                c.text = h
            for i, x in enumerate(est["lines"], 1):
                r = et.add_row().cells
                r[0].text, r[1].text = str(i), x["title"] + (f" ({x['article']})" if x["article"] else "")
                r[2].text = f"{x['qty']:g} {x['unit']}"
                r[3].text = "" if x["price"] is None else f"{x['price']:,.2f}".replace(",", " ")
                r[4].text = f"{x['discount_pct']:g}" if x["discount_pct"] else ""
                r[5].text = "" if x["net"] is None else f"{x['net']:,.2f}".replace(",", " ")
            tt = est["totals"]
            doc.add_paragraph(f"Итого без НДС: {tt['net']:,.2f} {est['currency']}".replace(",", " "))
            doc.add_paragraph(f"НДС {est['vat_pct']:g} %: {tt['vat']:,.2f} {est['currency']}".replace(",", " "))
            doc.add_paragraph(f"Итого с НДС: {tt['total']:,.2f} {est['currency']}".replace(",", " ")).runs[0].bold = True
            if snap["billing"] != "client":
                doc.add_paragraph(f"Оплата: {snap['billing_title'].lower()} — клиенту к оплате не выставляется.")
    doc.add_paragraph("")
    if snap["kind"] == "act":
        doc.add_paragraph("Работы выполнены в полном объёме. Претензий по объёму и качеству работ не имею.")
        doc.add_paragraph(f"Представитель клиента: {snap['signed_by_name'] or '____________________'}   подпись ____________")
        doc.add_paragraph(f"Исполнитель: {snap['engineer'] or '____________________'}   подпись ____________")
    elif snap["kind"] == "defect_list":
        doc.add_paragraph(f"Составил: {snap['engineer'] or '____________________'}   подпись ____________")
        doc.add_paragraph("Представитель клиента: ____________________   подпись ____________")
    doc.save(path)


def make_document(db: Session, wo: WorkOrder, kind: str, user_id: str, est: Optional[ServiceEstimate] = None) -> ServiceDocument:
    if kind not in DOC_KINDS:
        raise EstimateError("Вид документа — из списка")
    if kind == "act" and wo.status not in ("ready_to_close", "closed"):
        raise EstimateError("Акт формируется, когда наряд готов к закрытию или закрыт")
    if kind == "defect_list" and not (wo.diagnosis or db.scalar(select(func.count()).select_from(WorkOrderWork).where(WorkOrderWork.work_order_id == wo.id))):
        raise EstimateError("Для дефектной ведомости нужны заключение диагностики или работы")
    if est is None and kind in ("defect_list", "act"):
        # запчасти и итог — из согласованной сметы, иначе из последней
        est = db.scalar(select(ServiceEstimate).where(ServiceEstimate.work_order_id == wo.id, ServiceEstimate.status == "approved")
                        .order_by(ServiceEstimate.version.desc()).limit(1)) or latest(db, wo)
    snap = snapshot(db, wo, kind, est)
    number = _number(db, kind)
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    try:
        _docx(snap, number, path, with_prices=(kind == "estimate"))
        from .. import adapters
        name = f"{number} {DOC_KINDS[kind]}.docx"
        stored = adapters.storage().upload(path, f"Сервис/Наряды/{wo.number}", "07_fact", name)
    finally:
        os.unlink(path)
    doc = ServiceDocument(work_order_id=wo.id, estimate_id=est.id if est and kind == "estimate" else None, kind=kind, number=number,
                          snapshot=snap, storage_path=stored.path, filename=name, legal_checked=False, created_by=user_id)
    db.add(doc)
    db.flush()
    return doc

