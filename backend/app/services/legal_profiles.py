"""Юридический блок, этап 1: юридические профили контрагентов и компаний группы (БЕРГ-ХАУС), банковские профили.

Профиль привязан к существующему клиенту, поставщику или компании группы — отдельных дубликатов контрагентов нет.
Первый профиль создаётся автоматически из данных карточки (DaData для клиента, карточка завода, карточка компании группы),
дальше его дополняет менеджер / юрист. Все правки пишутся в историю профиля; банковские реквизиты — со своей историей и статусом.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BankProfile, Client, Factory, LegalEntity, LegalProfile, User

OWNER_KINDS = {"client": "Клиент", "factory": "Поставщик", "own": "Компания группы (БЕРГ-ХАУС)"}
PROFILE_KINDS = {"ru_customer": "Российский заказчик", "cn_factory": "Китайский завод", "foreign_supplier": "Иностранный поставщик",
                 "kg_company": "Киргизская компания", "intermediary": "Посредник", "own": "Компания группы", "other": "Другой контрагент"}
CHECK_STATUS = {"not_checked": "не проверен", "checked": "проверен", "issues": "есть замечания"}
BANK_STATUS = {"active": "действующий", "archived": "архивный"}

# поля профиля, которые правятся из формы и попадают в историю
PROFILE_FIELDS = ("kind", "title", "is_default", "country", "names", "opf", "reg_number", "tax_number", "kpp", "legal_address", "actual_address", "postal_address",
                  "director", "signatory_name", "signatory_position", "signatory_basis", "contacts", "documents", "powers_of_attorney", "extra", "lawyer_comment", "is_active")
BANK_FIELDS = ("title", "currency", "country", "beneficiary", "bank_name", "bank_address", "account", "bik", "swift", "corr_account", "corr_bank", "purpose",
               "status", "valid_from", "valid_to")

# страновые поля (extra) — подсказка интерфейсу, что показывать для типа профиля
COUNTRY_FIELDS: dict[str, list[dict]] = {
    "ru_customer": [],
    "cn_factory": [{"key": "business_license_no", "title": "Номер бизнес-лицензии"}, {"key": "business_license_valid_until", "title": "Лицензия действует до"},
                   {"key": "business_scope", "title": "Разрешённая деятельность (по лицензии)"}, {"key": "legal_representative", "title": "Законный представитель (法定代表人)"},
                   {"key": "registered_capital", "title": "Уставный капитал"}],
    "kg_company": [{"key": "okpo", "title": "ОКПО"}, {"key": "vat_number", "title": "Регистрационный номер НДС"}, {"key": "registration_authority", "title": "Орган регистрации"}],
    "foreign_supplier": [{"key": "vat_number", "title": "VAT / налоговый номер"}, {"key": "registration_authority", "title": "Орган регистрации"}, {"key": "legal_form", "title": "Правовая форма"}],
    "intermediary": [{"key": "agency_agreement", "title": "Агентский / посреднический договор"}],
    "own": [{"key": "okpo", "title": "ОКПО"}, {"key": "okved", "title": "ОКВЭД"}],
    "other": [{"key": "registration_authority", "title": "Орган регистрации"}],
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _diff(obj: Any, data: dict, fields: tuple[str, ...]) -> dict[str, list]:
    out = {}
    for k in fields:
        if k not in data:
            continue
        old, new = getattr(obj, k), data[k]
        if isinstance(old, (datetime,)):
            old = old.isoformat()
        if str(old if old is not None else "") != str(new if new is not None else ""):
            out[k] = [old if not hasattr(old, "isoformat") else old.isoformat(), new if not hasattr(new, "isoformat") else new.isoformat()]
    return out


def apply_changes(obj: Any, data: dict, fields: tuple[str, ...], user_id: Optional[str], action: str = "update") -> dict[str, list]:
    """Применяет изменения из data к объекту, пишет запись в history (только если что-то изменилось)."""
    changed = _diff(obj, data, fields)
    for k in changed:
        setattr(obj, k, data[k])
    if changed:
        obj.history = list(obj.history or []) + [{"at": _now().isoformat(), "by": user_id, "action": action, "fields": changed}]
    return changed


# ------------------------------------------------------------------ автосоздание из карточек

def _names(ru_full: str = "", ru_short: str = "", en: str = "", zh: str = "") -> dict:
    return {"ru": {"full": ru_full or "", "short": ru_short or ""}, "en": {"full": en or "", "short": ""}, "zh": {"full": zh or "", "short": ""}}


def profile_from_client(c: Client, req: Optional[dict] = None) -> LegalProfile:
    """Профиль российского заказчика: из набора реквизитов (#77) или из данных ЕГРЮЛ в карточке."""
    r = req or {}
    p = LegalProfile(owner_kind="client", owner_id=c.id, kind="ru_customer", country="RU",
                     title=r.get("title") or "Основной", is_default=bool(r.get("is_default", True)),
                     names=_names(r.get("legal_name") or c.full_name or c.name, r.get("short_name") or c.name),
                     reg_number=r.get("ogrn") or c.ogrn or "", tax_number=r.get("inn") or c.inn, kpp=r.get("kpp") or c.kpp or "",
                     legal_address=r.get("legal_address") or c.address or "", postal_address=r.get("postal_address") or "",
                     director=c.director or "", signatory_name=r.get("signatory_name") or c.director or "",
                     signatory_position=r.get("signatory_position") or ("Генеральный директор" if c.director else ""),
                     signatory_basis=r.get("signatory_basis") or ("Устава" if c.director else ""),
                     contacts=[{"name": x.get("name", ""), "position": x.get("position", ""), "phone": x.get("phone", ""), "email": x.get("email", "")} for x in (c.contacts or []) if isinstance(x, dict)],
                     extra={"okved": c.okved or ""} if c.okved else {})
    if r.get("bank_name") or r.get("account"):
        p.banks.append(BankProfile(title="Основной RUB", currency="RUB", country="RU", bank_name=r.get("bank_name", ""), account=r.get("account", ""), bik=r.get("bik", ""),
                                   corr_account=r.get("corr_account", ""), purpose="расчёты по договорам поставки"))
    if r.get("id"):
        p.extra = {**p.extra, "requisites_set_id": r["id"]}
    return p


def profile_from_factory(f: Factory) -> LegalProfile:
    kind = "cn_factory" if (f.country or "CN").upper() in ("CN", "CHINA", "КИТАЙ") else "kg_company" if (f.country or "").upper() in ("KG", "КИРГИЗИЯ", "KYRGYZSTAN") else "foreign_supplier"
    bd = f.bank_details or {}
    p = LegalProfile(owner_kind="factory", owner_id=f.id, kind=kind, country=f.country or "CN", title="Основной", is_default=True,
                     names=_names(f.name, f.short_name or "", f.name, f.name_zh or ""), reg_number=f.uscc or "", tax_number=f.uscc or "",
                     legal_address=f.address or "", actual_address=f.address_zh or "",
                     contacts=[{"name": x.get("name", ""), "position": x.get("position", ""), "phone": x.get("phone") or x.get("mobile", ""), "email": x.get("email", "")}
                               for x in (f.contacts or []) if isinstance(x, dict) and x.get("primary")],
                     extra={"legal_representative": ""} if kind == "cn_factory" else {})
    if bd.get("account") or bd.get("bank"):
        p.banks.append(BankProfile(title=f"Основной {f.default_currency or 'CNY'}", currency=f.default_currency or "CNY", country=f.country or "CN", beneficiary=bd.get("beneficiary", ""),
                                   bank_name=bd.get("bank", ""), bank_address=bd.get("bank_address", ""), account=bd.get("account", ""), swift=bd.get("swift", ""),
                                   purpose="оплата оборудования"))
    return p


def profile_from_entity(e: LegalEntity) -> LegalProfile:
    return LegalProfile(owner_kind="own", owner_id=e.id, kind="own", country=(e.country or "RU")[:64], title=e.name, is_default=True,
                        names=_names(e.name, e.name), extra={})


def ensure_profiles(db: Session, owner_kind: str, owner_id: str) -> list[LegalProfile]:
    """Профили владельца; если их нет — первый создаётся из карточки."""
    rows = list(db.scalars(select(LegalProfile).where(LegalProfile.owner_kind == owner_kind, LegalProfile.owner_id == owner_id).order_by(LegalProfile.is_default.desc(), LegalProfile.created_at)).all())
    if rows:
        return rows
    created: list[LegalProfile] = []
    if owner_kind == "client":
        c = db.get(Client, owner_id)
        if c is None:
            return []
        sets = list(c.requisites or [])
        created = [profile_from_client(c, r) for r in sets] if sets else [profile_from_client(c)]
        if created and not any(p.is_default for p in created):
            created[0].is_default = True
    elif owner_kind == "factory":
        f = db.get(Factory, owner_id)
        if f is None:
            return []
        created = [profile_from_factory(f)]
    elif owner_kind == "own":
        e = db.get(LegalEntity, owner_id)
        if e is None:
            return []
        created = [profile_from_entity(e)]
    for p in created:
        p.history = [{"at": _now().isoformat(), "by": None, "action": "auto_created", "fields": {}}]
        db.add(p)
    db.flush()
    return created


# ------------------------------------------------------------------ представление

def bank_payload(b: BankProfile, names: dict[str, str]) -> dict:
    return {"id": b.id, "profile_id": b.profile_id, "title": b.title, "currency": b.currency, "country": b.country, "beneficiary": b.beneficiary, "bank_name": b.bank_name,
            "bank_address": b.bank_address, "account": b.account, "bik": b.bik, "swift": b.swift, "corr_account": b.corr_account, "corr_bank": b.corr_bank, "purpose": b.purpose,
            "status": b.status, "status_title": BANK_STATUS.get(b.status, b.status), "valid_from": b.valid_from, "valid_to": b.valid_to,
            "verified": b.verified, "verified_at": b.verified_at, "verified_by_name": names.get(b.verified_by or "", ""),
            "history": [{**h, "by_name": names.get(h.get("by") or "", "")} for h in reversed(b.history or [])]}


def profile_payload(p: LegalProfile, names: dict[str, str], with_history: bool = True) -> dict:
    n = p.names or {}
    ru = n.get("ru") or {}
    return {"id": p.id, "owner_kind": p.owner_kind, "owner_id": p.owner_id, "kind": p.kind, "kind_title": PROFILE_KINDS.get(p.kind, p.kind), "title": p.title, "is_default": p.is_default,
            "country": p.country, "names": {"ru": {"full": ru.get("full", ""), "short": ru.get("short", "")}, "en": {"full": (n.get("en") or {}).get("full", ""), "short": (n.get("en") or {}).get("short", "")},
                                            "zh": {"full": (n.get("zh") or {}).get("full", ""), "short": (n.get("zh") or {}).get("short", "")}},
            "opf": p.opf, "reg_number": p.reg_number, "tax_number": p.tax_number, "kpp": p.kpp, "legal_address": p.legal_address, "actual_address": p.actual_address, "postal_address": p.postal_address,
            "director": p.director, "signatory_name": p.signatory_name, "signatory_position": p.signatory_position, "signatory_basis": p.signatory_basis,
            "contacts": p.contacts or [], "documents": p.documents or [], "powers_of_attorney": p.powers_of_attorney or [], "extra": p.extra or {},
            "check_status": p.check_status, "check_status_title": CHECK_STATUS.get(p.check_status, p.check_status), "checked_at": p.checked_at, "checked_by_name": names.get(p.checked_by or "", ""),
            "lawyer_comment": p.lawyer_comment, "is_active": p.is_active, "country_fields": COUNTRY_FIELDS.get(p.kind, []),
            "banks": [bank_payload(b, names) for b in p.banks], "summary": profile_summary(p),
            "history": [{**h, "by_name": names.get(h.get("by") or "", "")} for h in reversed(p.history or [])] if with_history else []}


def profile_summary(p: LegalProfile) -> str:
    ru = (p.names or {}).get("ru") or {}
    name = ru.get("full") or ru.get("short") or ((p.names or {}).get("en") or {}).get("full") or ""
    parts = [name]
    if p.tax_number:
        parts.append(("ИНН " if p.kind == "ru_customer" or p.country == "RU" else "рег. № ") + p.tax_number + (f" / КПП {p.kpp}" if p.kpp else ""))
    if p.signatory_name:
        parts.append(f"подписывает {p.signatory_name}" + (f" ({p.signatory_position})" if p.signatory_position else ""))
    return " · ".join(x for x in parts if x)


def user_names(db: Session) -> dict[str, str]:
    return {u.id: u.display_name for u in db.scalars(select(User)).all()}


# ------------------------------------------------------------------ мост для реквизитов контракта (#77)

def requisites_view(p: LegalProfile) -> dict:
    """Профиль в форме набора реквизитов, как ждёт блок «Реквизиты контракта» сделки: id = id профиля."""
    ru = (p.names or {}).get("ru") or {}
    bank = next((b for b in p.banks if b.status == "active"), None)
    return {"id": p.id, "title": p.title or ru.get("short") or ru.get("full") or "Профиль", "is_default": p.is_default, "legal_name": ru.get("full", ""), "short_name": ru.get("short", ""),
            "inn": p.tax_number, "kpp": p.kpp, "ogrn": p.reg_number, "legal_address": p.legal_address, "postal_address": p.postal_address,
            "bank_name": bank.bank_name if bank else "", "bik": bank.bik if bank else "", "account": bank.account if bank else "", "corr_account": bank.corr_account if bank else "",
            "signatory_name": p.signatory_name, "signatory_position": p.signatory_position, "signatory_basis": p.signatory_basis,
            "phone": (p.contacts[0].get("phone", "") if p.contacts else ""), "email": (p.contacts[0].get("email", "") if p.contacts else ""), "comment": p.lawyer_comment,
            "bank_profile_id": bank.id if bank else None, "profile_id": p.id}


def client_requisites_options(db: Session, c: Client) -> list[dict]:
    """Все варианты реквизитов клиента для договора: активные юридические профили (по умолчанию — первым)."""
    rows = [p for p in ensure_profiles(db, "client", c.id) if p.is_active]
    rows.sort(key=lambda p: (not p.is_default, p.created_at or _now()))
    return [requisites_view(p) for p in rows]


def upsert_requisites(db: Session, c: Client, items: list[dict], user_id: Optional[str]) -> list[LegalProfile]:
    """Совместимость со старым API наборов реквизитов: набор → профиль (создать/обновить), отсутствующие — архив."""
    existing = {p.id: p for p in ensure_profiles(db, "client", c.id)}
    keep: set[str] = set()
    for r in items:
        p = existing.get(r.get("id") or "")
        if p is None:
            p = profile_from_client(c, {**r, "is_default": False})
            p.history = [{"at": _now().isoformat(), "by": user_id, "action": "created", "fields": {}}]
            db.add(p)
            db.flush()
        else:
            data = {"title": r.get("title") or p.title, "names": _names(r.get("legal_name") or "", r.get("short_name") or ""), "tax_number": r.get("inn") or "", "kpp": r.get("kpp") or "",
                    "reg_number": r.get("ogrn") or "", "legal_address": r.get("legal_address") or "", "postal_address": r.get("postal_address") or "",
                    "signatory_name": r.get("signatory_name") or "", "signatory_position": r.get("signatory_position") or "", "signatory_basis": r.get("signatory_basis") or "", "is_active": True}
            apply_changes(p, data, PROFILE_FIELDS, user_id)
            bank = next((b for b in p.banks if b.status == "active"), None)
            if r.get("bank_name") or r.get("account"):
                bd = {"bank_name": r.get("bank_name", ""), "account": r.get("account", ""), "bik": r.get("bik", ""), "corr_account": r.get("corr_account", "")}
                if bank is None:
                    p.banks.append(BankProfile(title="Основной RUB", currency="RUB", country="RU", **bd))
                else:
                    apply_changes(bank, bd, BANK_FIELDS, user_id)
        p.is_default = bool(r.get("is_default"))
        keep.add(p.id)
    for pid, p in existing.items():
        if pid not in keep:
            apply_changes(p, {"is_active": False, "is_default": False}, PROFILE_FIELDS, user_id, action="archived")
    active = [p for p in ensure_profiles(db, "client", c.id) if p.is_active]
    if active and not any(p.is_default for p in active):
        active[0].is_default = True
    db.flush()
    return active
