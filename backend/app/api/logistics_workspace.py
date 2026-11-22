"""Рабочее место логиста (D-073): адреса, выбранные характеристики, запросы логистическим компаниям и их ответы."""
from __future__ import annotations

import re
import uuid

import os
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.files import safe_filename
from .. import adapters
from ..core.auth import CurrentUser, get_current_user, require_roles
from ..core.files import file_response
from ..core.roles import Role
from ..db import get_db
from ..models import Attachment, Client, Deal, FeedEntry
from ..services import logistics_workspace as lw
from ..services import quote_links as ql
from ..services.deal_service import audit, ensure_folders, notify
from .common import get_deal_or_404

router = APIRouter(prefix="/api/deals/{deal_id}/logistics", tags=["logistics"])
refs = APIRouter(prefix="/api/refs", tags=["logistics"])
LOGIST = require_roles(Role.LOGISTICIAN, Role.GD)
GD = require_roles(Role.GD)
CURRENCIES = ("RUB", "CNY", "USD", "EUR")


def _editable(deal: Deal) -> None:
    from ..core.workflow import DealStatus
    if deal.status in (DealStatus.CLOSED, DealStatus.LOST, DealStatus.CANCELLED) or deal.archived_at:
        raise HTTPException(409, "Сделка закрыта")


@router.get("/workspace")
def workspace(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    return {**lw.workspace(db, deal, user), "can_choose_fields": user.has(Role.GD), "can_edit": user.has(Role.LOGISTICIAN, Role.GD)}


class AddressesIn(BaseModel):
    pickup: dict[str, str] = Field(default_factory=dict)     # factory_id → адрес отгрузки (пусто — из карточки завода)
    delivery_address: Optional[str] = None
    propose_to_client: bool = True                          # новый адрес — в карточку клиента на утверждение ЗГД


@router.put("/addresses")
def set_addresses(deal_id: str, body: AddressesIn, db: Session = Depends(get_db), user: CurrentUser = Depends(LOGIST)):
    deal = get_deal_or_404(db, deal_id, user)
    _editable(deal)
    res = lw.set_addresses(db, deal, user, body.pickup, body.delivery_address, body.propose_to_client)
    db.commit()
    return {**res, "addresses": lw.addresses(db, deal)}


class ServicesIn(BaseModel):
    services: list[str]


@router.put("/services")
def set_services(deal_id: str, body: ServicesIn, db: Session = Depends(get_db), user: CurrentUser = Depends(LOGIST)):
    """Какие услуги запрашиваем у логистических компаний (обычно без таможни в РФ и экспортного оформления)."""
    deal = get_deal_or_404(db, deal_id, user)
    _editable(deal)
    try:
        out = lw.set_services(deal, body.services)
    except ValueError as e:
        raise HTTPException(422, str(e))
    db.commit()
    return {"services": out}


class ChooseIn(BaseModel):
    carrier: str
    comment: str = ""
    need_gd: bool = False          # логист просит согласование ЗГД


@router.post("/quotes/choose")
def choose_quote(deal_id: str, body: ChooseIn, db: Session = Depends(get_db), user: CurrentUser = Depends(LOGIST)):
    """Выбор лучшего предложения логистической компании (по умолчанию — самое дешёвое полное); пусто — снять выбор."""
    deal = get_deal_or_404(db, deal_id, user)
    _editable(deal)
    known = {lw._key(q.get("carrier") or "") for q in (deal.logistics_info or {}).get("quotes") or []}
    if body.carrier.strip() and lw._key(body.carrier) not in known:
        raise HTTPException(422, "От этой компании нет ответов")
    cmp = lw.compare(db, deal, (deal.logistics_info or {}).get("quotes") or [], lw.carriers(db))
    if body.carrier.strip() and cmp["best"] and lw._key(body.carrier) != lw._key(cmp["best"]) and not body.comment.strip():
        raise HTTPException(422, f"Выбрано не самое дешёвое предложение (лучшее — {cmp['best']}) — укажите причину")
    need = body.need_gd and not user.has(Role.GD)
    if need and not body.comment.strip():
        raise HTTPException(422, "Для согласования ЗГД опишите, что нужно согласовать")
    status = lw.choose(deal, body.carrier, user.id, body.comment, need)
    audit(db, user.id, deal.id, "logistics", deal.id, "carrier_chosen", after={"carrier": body.carrier, "comment": body.comment, "status": status})
    if status == "pending_gd":
        from ..services.legal_contracts import role_users
        for u in role_users(db, "gd"):
            notify(db, deal, u.id, "logistics_choice", None,
                   f"Сделка {deal.number}: логист просит согласовать выбор логистической компании «{body.carrier}». {body.comment.strip()}")
    db.commit()
    return lw.compare(db, deal, (deal.logistics_info or {}).get("quotes") or [], lw.carriers(db))


class DecisionIn(BaseModel):
    approve: bool
    comment: str = ""


@router.post("/quotes/choose/decision")
def choice_decision(deal_id: str, body: DecisionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(GD)):
    """ЗГД согласует или отклоняет выбор логистической компании."""
    deal = get_deal_or_404(db, deal_id, user)
    if (deal.logistics_info or {}).get("chosen_status") != "pending_gd":
        raise HTTPException(409, "Выбор не ждёт согласования")
    if not body.approve and not body.comment.strip():
        raise HTTPException(422, "Укажите причину отказа")
    lw.decide_choice(deal, user.id, body.approve, body.comment)
    audit(db, user.id, deal.id, "logistics", deal.id, "carrier_choice_" + ("approved" if body.approve else "declined"), comment=body.comment or None)
    db.add(FeedEntry(deal_id=deal.id, kind="system", subject=f"ЗГД {'согласовал' if body.approve else 'отклонил'} выбор логистической компании «{deal.logistics_info.get('chosen_carrier')}»",
                     body=body.comment))
    db.commit()
    return lw.compare(db, deal, (deal.logistics_info or {}).get("quotes") or [], lw.carriers(db))


@router.get("/request/file")
def request_file(deal_id: str, fmt: str = "xlsx", answer_days: int = 3, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Типовой запрос логистическим компаниям на расчёт доставки — Excel или PDF."""
    deal = get_deal_or_404(db, deal_id, user)
    data = lw.request_data(db, deal, max(1, min(answer_days, 30)))
    path = os.path.join(tempfile.mkdtemp(), f"request.{'pdf' if fmt == 'pdf' else 'xlsx'}")
    (lw.to_pdf if fmt == "pdf" else lw.to_xlsx)(data, path)
    return file_response(path, f"Запрос на расчёт доставки {data['no']}.{'pdf' if fmt == 'pdf' else 'xlsx'}", inline=False)


class SendIn(BaseModel):
    emails: list[str] = Field(default_factory=list)
    message: str = ""
    formats: list[str] = Field(default_factory=lambda: ["xlsx", "pdf"])
    answer_days: int = 3
    manual: bool = False        # отправлено вручную (своей почтой) — только отметка в сделке


@router.post("/request/send")
def send_request(deal_id: str, body: SendIn, db: Session = Depends(get_db), user: CurrentUser = Depends(LOGIST)):
    """Отправить запрос логистическим компаниям из системы (если почта подключена) или отметить отправку вручную."""
    from ..core.config import get_settings
    deal = get_deal_or_404(db, deal_id, user)
    _editable(deal)
    emails = [e.strip() for e in body.emails if e.strip()]
    if not emails:
        raise HTTPException(422, "Укажите, кому отправлен запрос")
    bad = [e for e in emails if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e)]
    if bad:
        raise HTTPException(422, f"Неверный адрес почты: {', '.join(bad)}")
    data = lw.request_data(db, deal, max(1, min(body.answer_days, 30)))
    if not body.manual:
        if get_settings().mail_provider == "console":
            raise HTTPException(409, "Почта не подключена — скачайте запрос (Excel / PDF), отправьте своей почтой и отметьте «Отправлено вручную»")
        tmp = tempfile.mkdtemp()
        try:
            files = []
            for fmt in [f for f in body.formats if f in ("xlsx", "pdf")] or ["xlsx"]:
                p = os.path.join(tmp, f"Запрос на расчёт доставки {data['no']}.{fmt}")
                (lw.to_pdf if fmt == "pdf" else lw.to_xlsx)(data, p)
                files.append(p)
            text = (body.message.strip() or "Добрый день! Просим рассчитать стоимость и сроки доставки по запросу во вложении.").replace("\n", "<br/>")
            req_id = uuid.uuid4().hex[:12]
            links = ql.create(db, deal, {"id": req_id, "no": data["no"], "answer_by": data["answer_by"]}, emails, user.id)
            for ln in links:        # D-107: каждому получателю — своё письмо со своей ссылкой для ответа
                adapters.mail().send(_mail([ln["email"]], data, text, files, ln["url"]))
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"Почта не отправила письмо: {e}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if body.manual:
        req_id = uuid.uuid4().hex[:12]
        links = ql.create(db, deal, {"id": req_id, "no": data["no"], "answer_by": data["answer_by"]}, emails, user.id)
    req = lw.record_request(deal, data, user.id, emails, {"manual": body.manual}, req_id=req_id)
    db.add(FeedEntry(deal_id=deal.id, kind="system", subject=f"Запрос на расчёт доставки {data['no']}" + (" (отправлен вручную)" if body.manual else ""),
                     body=", ".join(emails), user_id=user.id))
    audit(db, user.id, deal.id, "deal", deal.id, "logistics_request", after=req)    # без ссылок: в базе — только их хэши
    db.commit()
    return {**req, "links": links}       # D-107: ссылки для ответа видит только отправивший — в своём письме, если отправлял вручную


def _mail(emails: list[str], data: dict, text: str, files: list[str], link: str = ""):
    import html
    from ..adapters.mail import OutgoingMail
    return OutgoingMail(to=emails, subject=f"Запрос на расчёт доставки № {data['no']} — ООО «БЕРГ-ХАУС»",
                        body_html=f"<p>{html.escape(text)}</p><p>Просим рассчитать: {html.escape('; '.join(data['services']))}.</p>"
                                  f"<p>Ответ просим до {data['answer_by']}.</p>"
                                  + (f'<p>Ответить можно по ссылке, без регистрации: <a href="{html.escape(link)}">{html.escape(link)}</a></p>' if link else ""),
                        attachments=files)


@router.post("/quotes", status_code=201)
async def add_quote(deal_id: str, carrier: str = Form(...), amount: float = Form(0), currency: str = Form("RUB"), transit_days: Optional[int] = Form(None),
                    valid_until: str = Form(""), scope: str = Form("door_to_door"), comment: str = Form(""), file: Optional[UploadFile] = File(None),
                    db: Session = Depends(get_db), user: CurrentUser = Depends(LOGIST)):
    """Ответ логистической компании: файл (если есть) + сумма, валюта, срок, что входит."""
    deal = get_deal_or_404(db, deal_id, user)
    _editable(deal)
    if not carrier.strip():
        raise HTTPException(422, "Укажите логистическую компанию")
    if currency.upper() not in CURRENCIES:
        raise HTTPException(422, f"Валюта: {', '.join(CURRENCIES)}")
    att = None
    if file is not None and file.filename:
        d = tempfile.mkdtemp()
        name = safe_filename(file.filename)
        path = os.path.join(d, name)
        with open(path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        try:
            ensure_folders(db, deal)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            stem, ext = os.path.splitext(name)
            stored = adapters.storage().upload(path, deal.storage_folder, "02_logistics", f"Ответ {carrier.strip()[:40]} — {stem} [{stamp}]{ext}".replace("/", "-"))
        except Exception as e:
            raise HTTPException(502, f"Хранилище недоступно: {e}")
        finally:
            shutil.rmtree(d, ignore_errors=True)
        att = Attachment(deal_id=deal.id, folder="02_logistics", entity="logistics_quote", entity_id=deal.id, category="", comment=carrier.strip()[:255],
                         file_name=name, storage_path=stored.path, storage_url=stored.url, sha256=stored.sha256, size=stored.size, uploaded_by=user.id)
        db.add(att)
        db.flush()
    q = lw.add_quote(deal, user.id, {"carrier": carrier.strip()[:255], "amount": amount, "currency": currency.upper(), "transit_days": transit_days,
                                     "valid_until": valid_until[:10], "scope": scope if scope in dict(lw.SERVICES) else "door_to_door", "comment": comment.strip()[:1000],
                                     "storage_path": att.storage_path if att else ""}, att.id if att else None, att.file_name if att else "")
    db.add(FeedEntry(deal_id=deal.id, kind="file" if att else "system", subject=f"Ответ логистической компании {q['carrier']}: {amount:,.0f} {q['currency']}".replace(",", " "),
                     body=comment, user_id=user.id))
    audit(db, user.id, deal.id, "deal", deal.id, "logistics_quote", after=q)
    db.commit()
    return q


@router.delete("/quotes/{qid}", status_code=204)
def delete_quote(deal_id: str, qid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(LOGIST)):
    deal = get_deal_or_404(db, deal_id, user)
    if not lw.remove_quote(deal, qid):
        raise HTTPException(404)
    audit(db, user.id, deal.id, "deal", deal.id, "logistics_quote_delete", after={"id": qid})
    db.commit()


# ------------------------------------------------------------------ справочные: поля для логиста, логистические компании, адреса клиента

@refs.get("/logistics-fields")
def logistics_fields(deal_id: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = db.get(Deal, deal_id) if deal_id else None
    return {"fields": lw.fields(db), "options": lw.field_options(db, deal), "can_edit": user.has(Role.GD)}


@refs.put("/logistics-fields")
def set_logistics_fields(body: list[dict], db: Session = Depends(get_db), user: CurrentUser = Depends(GD)):
    """Какие технические характеристики видит логист в своём блоке — выбирает ЗГД (D-073)."""
    out = lw.set_fields(db, body, user.id)
    db.commit()
    return {"fields": out}


@refs.get("/logistics-carriers")
def logistics_carriers(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return lw.carriers(db)


@refs.put("/logistics-carriers")
def set_logistics_carriers(body: list[dict], db: Session = Depends(get_db), user: CurrentUser = Depends(LOGIST)):
    try:
        out = lw.set_carriers(db, body, user.id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    db.commit()
    return out


@refs.post("/clients/{client_id}/delivery-addresses/{addr_id}/{action}")
def decide_delivery_address(client_id: str, addr_id: str, action: str, db: Session = Depends(get_db), user: CurrentUser = Depends(GD)):
    """Новый адрес доставки, предложенный логистом, — утвердить (станет адресом доставки клиента) или отклонить. Решает ЗГД."""
    if action not in ("approve", "reject"):
        raise HTTPException(404)
    c = db.get(Client, client_id)
    if c is None:
        raise HTTPException(404)
    try:
        lw.decide_address(db, c, addr_id, user, action == "approve")
    except KeyError:
        raise HTTPException(404, "Адрес не найден")
    db.commit()
    return {"delivery_addresses": lw.client_addresses(c)}
