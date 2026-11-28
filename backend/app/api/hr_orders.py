"""D-208 (L2 упрощённо, решение владельца 28.11.2026): приказ по личному составу из кадрового события «Персонала».

Порядок — бумажный, пока юрист не утвердил матрицу ЭДО:
1. Кадровик в карточке сотрудника нажимает «Сформировать приказ» у события (приём, перевод, ставка, увольнение).
2. В Канцелярии появляется черновик приказа по личному составу (вид «order», категория «hr») с текстом и файлом .docx
   с пометкой «Черновик. Шаблон не проверен юристом». Делопроизводителям уходит уведомление.
3. Дальше — как сейчас в Канцелярии: регистрация (номер ПР-К), подписание на бумаге, скан подписанного приказа, ознакомление.
4. В карточке сотрудника у события видны номер, состояние и есть ли скан; ссылка ведёт в Канцелярию.

D-209 (решение владельца 28.11.2026): приказ заполняется полностью, от руки — только подписи. Оклад и надбавки на дату события
расшифровываются и вносятся в текст (формирует пользователь с доступом к оплате труда); реквизиты договора, испытание, основание
увольнения и подписант — из формы. Номер можно присвоить до подписи («Присвоить номер»), печатная форма обновляется.
Паспортные и другие личные данные в кадровый приказ не входят.
Права: сформировать — «Персонал» EDIT (черновик видит его автор и делопроизводители по личному составу); регистрировать и подписывать —
как сейчас, право «Канцелярия: приказы по личному составу».
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.clock import today
from ..core.roles import EDIT, READ
from ..db import get_db
from ..models import HrDepartment, HrEmployee, HrEmployeeEvent, HrEmployment, HrEventOrder, HrPosition, LegalEntity, OfficeDoc, OfficeSigner, User
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


TK_BASES = {
    "77-1-3": "по инициативе работника (по собственному желанию), пункт 3 части первой статьи 77 Трудового кодекса Российской Федерации",
    "77-1-1": "по соглашению сторон, пункт 1 части первой статьи 77 Трудового кодекса Российской Федерации",
    "77-1-2": "в связи с истечением срока трудового договора, пункт 2 части первой статьи 77 Трудового кодекса Российской Федерации",
    "77-1-5": "в связи с переводом к другому работодателю, пункт 5 части первой статьи 77 Трудового кодекса Российской Федерации",
    "71": "в связи с неудовлетворительным результатом испытания, часть первая статьи 71 Трудового кодекса Российской Федерации",
    "81-1-2": "в связи с сокращением численности или штата работников, пункт 2 части первой статьи 81 Трудового кодекса Российской Федерации",
    "81-1-6a": "за прогул, подпункт «а» пункта 6 части первой статьи 81 Трудового кодекса Российской Федерации",
}
PAY_KINDS = ("hire", "transfer", "rate")   # в тексте — оклад и надбавки: видят только с доступом к оплате труда
PERIOD = {"month": "в месяц", "hour": "в час", "day": "в день", "shift": "за смену"}


class Details(BaseModel):
    """Реквизиты приказа, которых нет в кадровом событии. Подставляются в текст; от руки — только подписи."""
    model_config = ConfigDict(extra="forbid")
    contract_number: str = Field("", max_length=64)
    contract_date: Optional[date] = None
    addendum_number: str = Field("", max_length=64)
    addendum_date: Optional[date] = None
    probation_months: int = Field(0, ge=0, le=6)
    conditions: str = Field("", max_length=500)          # условия приёма, характер работы
    tk_basis: str = Field("", max_length=16)             # код из TK_BASES или пусто — тогда tk_basis_text
    tk_basis_text: str = Field("", max_length=500)
    basis_doc: str = Field("", max_length=500)           # «заявление работника от 20.11.2026» и т. п.
    signer_id: Optional[str] = None
    # D-209 (решение владельца 28.11): кто ещё видит, цепочка согласования, кого ознакомить после подписания
    viewer_ids: list[str] = Field(default_factory=list, max_length=50)
    approver_ids: list[str] = Field(default_factory=list, max_length=20)   # по очереди, в указанном порядке
    route_id: Optional[str] = None                                           # или типовая схема Канцелярии
    ack_ids: list[str] = Field(default_factory=list, max_length=200)


def _money(v: float, cur: str) -> str:
    s = f"{float(v):,.2f}".replace(",", " ").replace(".", ",")
    return f"{s} {'руб.' if cur in ('RUB', '') else cur}"


def _pay_on(db: Session, employment_id: str, on: date) -> tuple[Optional[dict], list[dict]]:
    """Оклад и надбавки на дату — расшифровываются только здесь, для текста приказа."""
    from ..models import HrPayRecord
    from ..services import hr_crypto
    rows = list(db.scalars(select(HrPayRecord).where(HrPayRecord.employment_id == employment_id)))
    dead = {r.cancels_id for r in rows if r.kind == "cancel" and r.cancels_id}
    salary, allow = None, []
    for r in sorted(rows, key=lambda r: (r.effective_from, r.recorded_at)):
        if r.id in dead or r.kind not in ("salary", "allowance") or r.effective_from > on or (r.effective_to and on > r.effective_to):
            continue
        data = hr_crypto.unseal(r.data_enc, r.kid, f"pay|{r.id}")
        if r.kind == "salary":
            salary = data
        else:
            allow.append(data)
    return salary, allow


def _pay_text(salary: Optional[dict], allow: list[dict]) -> str:
    if not salary:
        raise HTTPException(422, "Сначала внесите оклад или тариф на дату события во вкладке «Оплата труда» — приказ заполняется полностью")
    s = f"Тарифная ставка (оклад): {_money(salary['amount'], salary.get('currency', 'RUB'))} {PERIOD.get(salary.get('period', 'month'), '')}"
    if allow:
        parts = [f"{a['name']} — " + (f"{float(a['percent']):g} %".replace(".", ",") if a.get("percent") is not None else _money(a.get("amount") or 0, a.get("currency", "RUB")))
                 for a in allow]
        s += "; надбавки: " + "; ".join(parts)
    else:
        s += "; надбавки: нет"
    return s + "."


def compose(db: Session, ev: HrEmployeeEvent, m: HrEmployment, e: HrEmployee, det: Details) -> tuple[str, list[str]]:
    """Заголовок и пункты приказа — полностью, от руки только подписи (решение владельца 28.11.2026)."""
    from datetime import timedelta
    evs = hc.events_of(db, [m.id])[m.id]
    now_a = hc.assignment_on(evs, ev.effective_from)
    before = hc.assignment_on([x for x in evs if x.id != ev.id], ev.effective_from - timedelta(days=1) if ev.kind == "transfer" else ev.effective_from)
    dn = lambda i: (db.get(HrDepartment, i).name if i and db.get(HrDepartment, i) else "—")  # noqa: E731
    pn = lambda i: (db.get(HrPosition, i).name if i and db.get(HrPosition, i) else "—")  # noqa: E731
    who = f"{hc.full_name(e)} (табельный номер {e.personnel_no})"
    need = []
    if ev.kind == "hire":
        if not det.contract_number or not det.contract_date:
            need.append("номер и дату трудового договора")
        a = {**now_a, **(ev.data or {})}
        salary, allow = _pay_on(db, m.id, ev.effective_from)
        if need:
            raise HTTPException(422, "Укажите " + ", ".join(need))
        lines = [f"Принять на работу {who}",
                 f"в подразделение «{dn(a.get('department_id'))}» на должность «{pn(a.get('position_id'))}»,",
                 f"{EMP_KIND.get(m.kind, m.kind)}, {_rate(a.get('rate'))} ставки, с {_d(ev.effective_from)}.",
                 f"Условия приёма на работу, характер работы: {det.conditions or 'постоянно'}.",
                 _pay_text(salary, allow),
                 f"Испытание на срок {det.probation_months} мес." if det.probation_months else "Без испытания.",
                 f"Основание: трудовой договор от {_d(det.contract_date)} № {det.contract_number}."]
    elif ev.kind in ("transfer", "rate"):
        if not det.addendum_number or not det.addendum_date:
            raise HTTPException(422, "Укажите номер и дату дополнительного соглашения к трудовому договору")
        salary, allow = _pay_on(db, m.id, ev.effective_from)
        until = f" по {_d(ev.effective_to)}" if ev.effective_to else ""
        if ev.kind == "transfer":
            new = {**before, **(ev.data or {})}
            lines = [f"Перевести {who}",
                     f"с должности «{pn(before.get('position_id'))}» подразделения «{dn(before.get('department_id'))}»",
                     f"на должность «{pn(new.get('position_id'))}» подразделения «{dn(new.get('department_id'))}»",
                     f"с {_d(ev.effective_from)}{until + ' (временный перевод)' if until else ' (постоянно)'}."]
        else:
            lines = [f"Установить {who}, должность «{pn(now_a.get('position_id'))}» подразделения «{dn(now_a.get('department_id'))}»,",
                     f"работу на {_rate((ev.data or {}).get('rate'))} ставки с {_d(ev.effective_from)}{until}."]
        lines += [_pay_text(salary, allow),
                  f"Основание: дополнительное соглашение от {_d(det.addendum_date)} № {det.addendum_number} к трудовому договору"
                  + (f" от {_d(det.contract_date)} № {det.contract_number}." if det.contract_number else ".")]
    elif ev.kind == "terminate":
        basis = TK_BASES.get(det.tk_basis) or det.tk_basis_text.strip()
        if not det.contract_number or not det.contract_date:
            need.append("номер и дату трудового договора")
        if not basis:
            need.append("основание увольнения (статью ТК)")
        if not det.basis_doc.strip():
            need.append("документ-основание (например, заявление работника с датой)")
        if need:
            raise HTTPException(422, "Укажите " + ", ".join(need))
        lines = [f"Прекратить действие трудового договора от {_d(det.contract_date)} № {det.contract_number},",
                 f"уволить {_d(ev.effective_from)} {who},",
                 f"должность «{pn(now_a.get('position_id'))}» подразделения «{dn(now_a.get('department_id'))}»,",
                 f"{basis}.",
                 f"Основание (документ, номер, дата): {det.basis_doc.strip()}."]
    else:
        raise HTTPException(422, "Приказ формируется по приёму, переводу, изменению ставки или увольнению")
    return TITLES[ev.kind], lines


def render(doc: OfficeDoc, employer: str, fio_short: str, signer: Optional[OfficeSigner], title: str, path: str) -> None:
    """Печатная форма из текста приказа в Канцелярии: номер и дата — если присвоены; от руки — только подписи."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor
    d = Document()
    st = d.styles["Normal"]
    st.font.name, st.font.size = "Times New Roman", Pt(12)
    r = d.add_paragraph().add_run(DRAFT_MARK)
    r.bold, r.font.color.rgb, r.font.size = True, RGBColor(0xC0, 0x1E, 0x2E), Pt(9)
    num = f"№ {doc.number} от {_d(doc.reg_date)}" if doc.number else "№ — присваивается при регистрации в Канцелярии"
    for text, bold in ((employer, True), ("ПРИКАЗ (распоряжение)", True), (num, False), (title, True)):
        p = d.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run(text).bold = bold
    for ln in (doc.body or "").split("\n"):
        d.add_paragraph(ln)
    d.add_paragraph("")
    who = f"{signer.position} {signer.full_name}".strip() if signer else "Руководитель организации"
    d.add_paragraph(f"{who}  ____________________ (подпись)")
    d.add_paragraph("")
    d.add_paragraph(f"С приказом (распоряжением) работник ознакомлен: {fio_short}  ____________________ (подпись)   «___» __________ 20__ г.")
    d.save(path)


def _store_print(db: Session, doc: OfficeDoc, link: HrEventOrder, user_id: str) -> None:
    """Файл печатной формы в Канцелярии: прежняя печатная форма из «Персонала» заменяется (подписанный скан — отдельно, не трогается)."""
    import hashlib
    from .office import _files_dir
    ev = db.get(HrEmployeeEvent, link.event_id)
    e = db.get(HrEmployee, ev.employee_id)
    ent = db.get(LegalEntity, doc.entity_id)
    sid = (link.details or {}).get("signer_id")
    signer = db.get(OfficeSigner, sid) if sid else (db.get(OfficeSigner, doc.signer_id) if doc.signer_id else None)
    folder = _files_dir(doc.id)
    keep = []
    for f in doc.files or []:
        if f.get("hr_print"):
            for x in os.listdir(folder):
                if x.startswith(f["id"]):
                    os.unlink(os.path.join(folder, x))
        else:
            keep.append(f)
    fid = uuid.uuid4().hex
    name = f"Приказ{(' ' + doc.number) if doc.number else ''} — {_fio_short(e)} — {TITLES[ev.kind][:40]}.docx".replace("/", "-")
    path = os.path.join(folder, f"{fid}__{name}")
    render(doc, ent.name, _fio_short(e), signer, TITLES[ev.kind], path)
    data = open(path, "rb").read()
    doc.files = [*keep, {"id": fid, "name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "kind": "draft", "hr_print": True,
                         "at": datetime.now(timezone.utc).isoformat(), "by": user_id}]


def _pay_editors(db: Session, exclude: str = "") -> list[str]:
    from ..services.access_service import perms_for
    return [u.id for u in db.scalars(select(User).where(User.is_active.is_(True)))
            if u.id != exclude and perms_for(db, u).get("hr_payroll", 0) >= 2]


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


@router.get("/events/{event_id}/order-template")
def order_template(event_id: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Подсказки для формы: договор из приказа о приёме, подписанты юрлица, основания увольнения."""
    if not user.can("hr_core", EDIT):
        raise HTTPException(403, "Приказ формирует кадровик (право редактирования «Персонала»)")
    ev = db.get(HrEmployeeEvent, event_id)
    if ev is None or not hc.in_scope(hc.allowed_employers(db, user), ev.employer_id):
        raise HTTPException(404, "Событие не найдено")
    pre: dict = {}
    for other in db.scalars(select(HrEmployeeEvent).where(HrEmployeeEvent.employment_id == ev.employment_id, HrEmployeeEvent.kind == "hire")):
        link = _live(db, other.id)
        if link:
            pre = {k: (link.details or {}).get(k) for k in ("contract_number", "contract_date", "signer_id") if (link.details or {}).get(k)}
    if ev.kind == "terminate" and (ev.data or {}).get("reason"):
        pre["basis_doc"] = ""
    on = ev.effective_from
    signers = [{"id": s.id, "title": f"{s.full_name}, {s.position}".strip(", ")} for s in db.scalars(select(OfficeSigner).where(OfficeSigner.entity_id == ev.employer_id))
               if office_svc.signer_ok(s, on) or office_svc.signer_ok(s, today())]
    from ..models import OfficeRoute
    from ..services.hr_orders_access import has_pay_access
    e = db.get(HrEmployee, ev.employee_id)
    people = [{"id": u.id, "name": u.display_name, "pay": has_pay_access(db, u)}
              for u in db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.display_name)) if u.id != "system"]
    routes = [{"id": r.id, "name": r.name} for r in db.scalars(select(OfficeRoute).where(OfficeRoute.is_active.is_(True)))
              if (not r.kinds or "order" in r.kinds) and (not r.categories or "hr" in r.categories) and (not r.entity_id or r.entity_id == ev.employer_id)]
    return {"kind": ev.kind, "prefill": pre, "signers": signers, "tk_bases": TK_BASES, "pay_restricted": ev.kind in PAY_KINDS,
            "people": people, "routes": routes, "employee_user_id": e.user_id}


@router.post("/events/{event_id}/order", status_code=201)
def make_order(event_id: str, body: Details, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.can("hr_core", EDIT):
        raise HTTPException(403, "Приказ формирует кадровик (право редактирования «Персонала»)")
    if not user.can("hr_payroll", READ):
        raise HTTPException(403, "В приказ входят оклад и надбавки — нужен доступ к оплате труда «Персонала»")
    ev = db.get(HrEmployeeEvent, event_id)
    if ev is None or not hc.in_scope(hc.allowed_employers(db, user), ev.employer_id):
        raise HTTPException(404, "Событие не найдено")
    evs = hc.events_of(db, [ev.employment_id])[ev.employment_id]
    if ev.id in hc.cancelled_ids(evs):
        raise HTTPException(409, "Событие отменено — приказ по нему не формируется")
    if (link := _live(db, ev.id)) is not None:
        raise HTTPException(409, f"Приказ по событию уже есть: {db.get(OfficeDoc, link.office_doc_id).number or 'черновик'} — откройте его в Канцелярии")
    if body.signer_id:
        sg = db.get(OfficeSigner, body.signer_id)
        if sg is None or sg.entity_id != ev.employer_id:
            raise HTTPException(422, "Подписант не найден у этого юрлица")
    if body.tk_basis and body.tk_basis not in TK_BASES:
        raise HTTPException(422, "Основание увольнения — из списка или текстом")
    m, e = db.get(HrEmployment, ev.employment_id), db.get(HrEmployee, ev.employee_id)
    _visible_employee(db, user, e.id)
    ent = db.get(LegalEntity, ev.employer_id)
    restricted = ev.kind in PAY_KINDS
    from ..services.hr_orders_access import pay_viewers_ok
    for ids in (body.viewer_ids, body.approver_ids, body.ack_ids):
        if ids and len(db.scalars(select(User.id).where(User.id.in_(ids), User.is_active.is_(True))).all()) != len(set(ids)):
            raise HTTPException(422, "Сотрудник в списке не найден или отключён")
    if restricted:
        pay_viewers_ok(db, body.viewer_ids)
        pay_viewers_ok(db, body.approver_ids, what="согласовывать приказ с оплатой труда")
        pay_viewers_ok(db, body.ack_ids, allow={e.user_id} if e.user_id else set(), what="знакомиться с приказом с оплатой труда (кроме самого работника)")
    title, lines = compose(db, ev, m, e, body)
    subject = f"{title}: {_fio_short(e)}"
    doc = OfficeDoc(entity_id=ent.id, kind="order", category="hr", subject=subject[:512], summary=f"Сформирован в «Персонале» по кадровому событию от {_d(ev.effective_from)}",
                    body="\n".join(lines), effective_on=ev.effective_from, status="draft", created_by=user.id, files=[], history=[], comments=[],
                    pay_restricted=restricted, viewer_ids=list(dict.fromkeys(body.viewer_ids)), planned_acks=list(dict.fromkeys(body.ack_ids)))
    db.add(doc)
    db.flush()
    office_svc.hist(doc, user.id, "created", "из «Персонала»")
    link = HrEventOrder(event_id=ev.id, office_doc_id=doc.id, created_by=user.id, details=body.model_dump(mode="json"))
    db.add(link)
    db.flush()
    _store_print(db, doc, link, user.id)
    audit(db, user.id, None, "hr_employment", m.id, "order_draft", after={"event_kind": ev.kind, "office_doc_id": doc.id, "pay_restricted": restricted})
    # кто ведёт: приказ с оплатой труда — ЗГД, ФД, бухгалтер по зарплате; без оплаты — делопроизводители по личному составу
    for uid in (_pay_editors(db, exclude=user.id) if restricted else _office_hr_editors(db, exclude=user.id)):
        notify(db, None, uid, "office_hr_order", None, f"Приказ по личному составу на регистрацию: {subject}", f"/office?doc={doc.id}")
    if body.approver_ids or body.route_id:
        from .office import StartIn, StepIn, start_approval
        db.commit()
        start_approval(doc.id, StartIn(route_id=body.route_id, mode="seq", steps=[StepIn(user_id=i) for i in body.approver_ids],
                                       note="сформирован в «Персонале»"), db, user)
        if restricted and doc.approval:   # типовая схема могла добавить согласующих по роли — проверить, что они видят оплату
            people = [st.get("user_id") for st in doc.approval.get("steps", []) if st.get("user_id")]
            pay_viewers_ok(db, people, what="согласовывать приказ с оплатой труда")
    db.commit()
    return _order_out(db, link)


@router.post("/orders/{office_doc_id}/number")
def order_number(office_doc_id: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Присвоить номер кадровому приказу до подписи — чтобы печатать полностью заполненным (от руки только подписи).
    Номер из того же журнала ПР-К и тем же счётчиком, что при подписании; при подписании номер не меняется."""
    doc = db.get(OfficeDoc, office_doc_id)
    link = db.scalar(select(HrEventOrder).where(HrEventOrder.office_doc_id == office_doc_id))
    if doc is None or doc.kind != "order" or doc.category != "hr" or not office_svc.can_see(user, doc):
        raise HTTPException(404, "Кадровый приказ не найден")
    if not office_svc.is_editor(user, doc):
        raise HTTPException(403, "Номер присваивает делопроизводитель по личному составу или ЗГД (приказ с оплатой труда — ЗГД, ФД, бухгалтер по зарплате)")
    if doc.status not in ("draft", "approved"):
        raise HTTPException(409, "Номер присваивается до подписания")
    office_svc.allocate_number(db, doc)
    office_svc.hist(doc, user.id, "numbered", doc.number)
    if link is not None:   # приказ из «Персонала» — печатная форма с номером
        _store_print(db, doc, link, user.id)
    db.commit()
    return office_svc.serialize(db, doc, user, full=True)
