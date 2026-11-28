"""D-208 (L2 упрощённо, решение владельца 28.11.2026): приказ по личному составу из кадрового события «Персонала».

Порядок — бумажный, пока юрист не утвердил матрицу ЭДО:
1. Кадровик в карточке сотрудника нажимает «Сформировать приказ» у события (приём, перевод, ставка, увольнение).
2. В Канцелярии появляется черновик приказа по личному составу (вид «order», категория «hr») с текстом и файлом .docx
   с пометкой «Черновик. Шаблон не проверен юристом». Делопроизводителям уходит уведомление.
3. Дальше — как сейчас в Канцелярии: регистрация (номер ПР-К), подписание на бумаге, скан подписанного приказа, ознакомление.
4. В карточке сотрудника у события видны номер, состояние и есть ли скан; ссылка ведёт в Канцелярию.

Личные данные и оплата труда в текст приказа не попадают (Канцелярия их не шифрует): оклад, паспорт — строкой для заполнения от руки.
Права: сформировать — «Персонал» EDIT (черновик видит его автор и делопроизводители по личному составу); регистрировать и подписывать —
как сейчас, право «Канцелярия: приказы по личному составу».
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import EDIT, READ
from ..db import get_db
from ..models import HrDepartment, HrEmployee, HrEmployeeEvent, HrEmployment, HrEventOrder, HrPosition, LegalEntity, OfficeDoc, User
from ..services import hr_core as hc
from ..services import office as office_svc
from ..services.deal_service import audit, notify
from .hr import _visible_employee

router = APIRouter(prefix="/api/hr", tags=["hr"])

DRAFT_MARK = "Черновик. Шаблон не проверен юристом."
TITLES = {"hire": "О приёме работника на работу", "transfer": "О переводе работника на другую работу",
          "rate": "Об изменении режима (ставки) работы", "terminate": "О прекращении (расторжении) трудового договора с работником (увольнении)"}
EMP_KIND = {"main": "основное место работы", "internal_part": "внутреннее совместительство", "external_part": "внешнее совместительство"}
BLANK = "____________________"


def _fio_short(e: HrEmployee) -> str:
    ini = "".join(f"{x[0]}." for x in (e.first_name, e.middle_name) if x)
    return f"{e.last_name} {ini}".strip()


def _d(x: Optional[date]) -> str:
    return x.strftime("%d.%m.%Y") if x else BLANK


def _rate(v) -> str:
    return f"{float(v):g}".replace(".", ",") if v is not None else "1"


def compose(db: Session, ev: HrEmployeeEvent, m: HrEmployment, e: HrEmployee) -> tuple[str, list[str]]:
    """Заголовок и пункты приказа. Только кадровые данные: ФИО, номер, подразделение, должность, ставка, даты, основание."""
    evs = hc.events_of(db, [m.id])[m.id]
    now_a = hc.assignment_on(evs, ev.effective_from)
    from datetime import timedelta
    before = hc.assignment_on([x for x in evs if x.id != ev.id], ev.effective_from - timedelta(days=1) if ev.kind == "transfer" else ev.effective_from)
    dn = lambda i: (db.get(HrDepartment, i).name if i and db.get(HrDepartment, i) else BLANK)  # noqa: E731
    pn = lambda i: (db.get(HrPosition, i).name if i and db.get(HrPosition, i) else BLANK)  # noqa: E731
    who = f"{hc.full_name(e)} (табельный номер в CRM {e.personnel_no})"
    if ev.kind == "hire":
        a = {**now_a, **(ev.data or {})}
        lines = [f"Принять на работу {who}",
                 f"в подразделение «{dn(a.get('department_id'))}» на должность «{pn(a.get('position_id'))}»,",
                 f"{EMP_KIND.get(m.kind, m.kind)}, {_rate(a.get('rate'))} ставки, с {_d(ev.effective_from)}.",
                 f"Условия приёма, характер работы: {BLANK}.",
                 f"Тарифная ставка (оклад): {BLANK} руб.; надбавка: {BLANK}.",
                 f"Испытание на срок: {BLANK}.",
                 f"Основание: трудовой договор от {BLANK} № {BLANK}."]
    elif ev.kind == "transfer":
        new = {**before, **(ev.data or {})}
        until = f" по {_d(ev.effective_to)} (временный перевод)" if ev.effective_to else " (постоянно)"
        lines = [f"Перевести {who}",
                 f"с должности «{pn(before.get('position_id'))}» подразделения «{dn(before.get('department_id'))}»",
                 f"на должность «{pn(new.get('position_id'))}» подразделения «{dn(new.get('department_id'))}»",
                 f"с {_d(ev.effective_from)}{until}.",
                 f"Тарифная ставка (оклад): {BLANK} руб.; надбавка: {BLANK}.",
                 f"Основание: дополнительное соглашение к трудовому договору от {BLANK} № {BLANK}."]
    elif ev.kind == "rate":
        until = f" по {_d(ev.effective_to)}" if ev.effective_to else ""
        lines = [f"Установить {who}, должность «{pn(now_a.get('position_id'))}» подразделения «{dn(now_a.get('department_id'))}»,",
                 f"работу на {_rate((ev.data or {}).get('rate'))} ставки с {_d(ev.effective_from)}{until}.",
                 f"Основание: дополнительное соглашение к трудовому договору от {BLANK} № {BLANK}."]
    elif ev.kind == "terminate":
        reason = (ev.data or {}).get("reason") or BLANK
        lines = [f"Прекратить действие трудового договора от {BLANK} № {BLANK},",
                 f"уволить {_d(ev.effective_from)} {who},",
                 f"должность «{pn(now_a.get('position_id'))}» подразделения «{dn(now_a.get('department_id'))}».",
                 f"Основание прекращения трудового договора (увольнения): {reason}, {BLANK} (пункт, часть, статья ТК РФ).",
                 f"Основание (документ, номер, дата): {BLANK}."]
    else:
        raise HTTPException(422, "Приказ формируется по приёму, переводу, изменению ставки или увольнению")
    return TITLES[ev.kind], lines


def _docx(path: str, employer: str, title: str, fio_short: str, lines: list[str], comment: str) -> None:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Times New Roman", Pt(12)
    r = doc.add_paragraph().add_run(DRAFT_MARK)
    r.bold, r.font.color.rgb = True, RGBColor(0xC0, 0x1E, 0x2E)
    for text, bold in ((employer, True), ("ПРИКАЗ (распоряжение)", True), (f"№ {BLANK} от {BLANK}", False), (title.lower().capitalize(), True)):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run(text).bold = bold
    for ln in lines:
        doc.add_paragraph(ln)
    if comment:
        doc.add_paragraph(f"Примечание кадровика: {comment}")
    doc.add_paragraph("")
    doc.add_paragraph(f"Руководитель организации  {BLANK} / {BLANK} /")
    doc.add_paragraph("                                          (подпись)        (расшифровка)")
    doc.add_paragraph("")
    doc.add_paragraph(f"С приказом (распоряжением) работник ознакомлен:  {BLANK} / {fio_short} /   «___» __________ 20__ г.")
    doc.save(path)


def _office_hr_editors(db: Session, exclude: str = "") -> list[str]:
    from ..services.access_service import perms_for
    return [u.id for u in db.scalars(select(User).where(User.is_active.is_(True)))
            if u.id != exclude and perms_for(db, u).get("office_hr", 0) >= 2]


def _order_out(db: Session, link: HrEventOrder) -> dict:
    d = db.get(OfficeDoc, link.office_doc_id)
    return {"event_id": link.event_id, "office_doc_id": link.office_doc_id, "number": d.number if d else "", "status": d.status if d else "",
            "status_title": office_svc.STATUSES.get(d.status, d.status) if d else "", "has_scan": bool(d and office_svc.has_scan(d)),
            "signed_on": d.signed_on if d else None, "url": f"/office?doc={link.office_doc_id}"}


def _live(db: Session, event_id: str) -> Optional[HrEventOrder]:
    for link in db.scalars(select(HrEventOrder).where(HrEventOrder.event_id == event_id).order_by(HrEventOrder.created_at.desc())):
        d = db.get(OfficeDoc, link.office_doc_id)
        if d is not None and d.status != "cancelled":
            return link
    return None


@router.get("/employees/{employee_id}/orders")
def orders(employee_id: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Приказы по событиям сотрудника (видимым пользователю трудовым отношениям)."""
    if not user.can("hr_core", READ):
        raise HTTPException(403, "Нет доступа к разделу «Персонал»")
    e, emps = _visible_employee(db, user, employee_id)
    ev_ids = [x.id for x in db.scalars(select(HrEmployeeEvent).where(HrEmployeeEvent.employment_id.in_([m.id for m in emps])))]
    out = {}
    for eid in ev_ids:
        link = _live(db, eid)
        if link:
            out[eid] = _order_out(db, link)
    return out


@router.post("/events/{event_id}/order", status_code=201)
def make_order(event_id: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.can("hr_core", EDIT):
        raise HTTPException(403, "Приказ формирует кадровик (право редактирования «Персонала»)")
    ev = db.get(HrEmployeeEvent, event_id)
    if ev is None or not hc.in_scope(hc.allowed_employers(db, user), ev.employer_id):
        raise HTTPException(404, "Событие не найдено")
    evs = hc.events_of(db, [ev.employment_id])[ev.employment_id]
    if ev.id in hc.cancelled_ids(evs):
        raise HTTPException(409, "Событие отменено — приказ по нему не формируется")
    if (link := _live(db, ev.id)) is not None:
        raise HTTPException(409, f"Приказ по событию уже есть: {db.get(OfficeDoc, link.office_doc_id).number or 'черновик'} — откройте его в Канцелярии")
    m, e = db.get(HrEmployment, ev.employment_id), db.get(HrEmployee, ev.employee_id)
    _visible_employee(db, user, e.id)
    ent = db.get(LegalEntity, ev.employer_id)
    title, lines = compose(db, ev, m, e)
    subject = f"{title}: {_fio_short(e)}"
    doc = OfficeDoc(entity_id=ent.id, kind="order", category="hr", subject=subject[:512], summary=f"Сформирован в «Персонале» по кадровому событию от {_d(ev.effective_from)}",
                    body="\n".join(lines), effective_on=ev.effective_from, status="draft", created_by=user.id, files=[], history=[], comments=[])
    db.add(doc)
    db.flush()
    office_svc.hist(doc, user.id, "created", "из «Персонала»")
    # файл черновика — в папку файлов документа Канцелярии (как загрузка вручную)
    from .office import _files_dir
    fid = uuid.uuid4().hex
    name = f"Приказ — {_fio_short(e)} — {TITLES[ev.kind][:40]}.docx".replace("/", "-")
    path = os.path.join(_files_dir(doc.id), f"{fid}__{name}")
    _docx(path, ent.name, title, _fio_short(e), lines, ev.comment or "")
    import hashlib
    data = open(path, "rb").read()
    doc.files = [{"id": fid, "name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "kind": "draft",
                  "at": datetime.now(timezone.utc).isoformat(), "by": user.id}]
    link = HrEventOrder(event_id=ev.id, office_doc_id=doc.id, created_by=user.id)
    db.add(link)
    db.flush()
    audit(db, user.id, None, "hr_employment", m.id, "order_draft", after={"event_kind": ev.kind, "office_doc_id": doc.id})
    # делопроизводителям по личному составу — зарегистрировать и подписать
    for uid in _office_hr_editors(db, exclude=user.id):
        notify(db, None, uid, "office_hr_order", None, f"Приказ по личному составу на регистрацию: {subject}", f"/office?doc={doc.id}")
    db.commit()
    return _order_out(db, link)
