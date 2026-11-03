"""Реквизиты организации по ИНН из внешнего источника (ЕГРЮЛ/ЕГРИП).

COMPANY_REGISTRY_PROVIDER = dadata | none
  dadata — DaData «Организация по ИНН» (findById/party): данные ФНС, бесплатно до 10 000 запросов в сутки, нужен API-ключ
           (DADATA_API_KEY, личный кабинет dadata.ru). Отвечает с серверов в РФ.
  none   — источник не подключён: реквизиты вводятся вручную.
Адаптер сменный: при переходе на Контур.Фокус, СПАРК или API ФНС достаточно добавить класс с тем же методом find_by_inn.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Optional

log = logging.getLogger("gsho.registry")


@dataclass
class CompanyInfo:
    inn: str
    name: str = ""                 # краткое с ОПФ: ООО «Рудник Северный»
    full_name: str = ""            # полное наименование
    kpp: str = ""
    ogrn: str = ""
    address: str = ""
    director: str = ""             # должность и ФИО руководителя
    status: str = ""               # ACTIVE | LIQUIDATING | LIQUIDATED | BANKRUPT | REORGANIZING
    okved: str = ""
    registered_on: Optional[str] = None
    kind: str = ""                 # LEGAL | INDIVIDUAL
    source: str = ""
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


class RegistryNotConfigured(RuntimeError):
    pass


class CompanyRegistry:
    name = "none"

    def find_by_inn(self, inn: str) -> list[CompanyInfo]:
        raise RegistryNotConfigured("Источник реквизитов не подключён: задайте COMPANY_REGISTRY_PROVIDER=dadata и DADATA_API_KEY")


class DaDataRegistry(CompanyRegistry):
    name = "dadata"
    URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"

    def __init__(self, api_key: str, timeout: float = 10.0):
        if not api_key:
            raise RegistryNotConfigured("Не задан DADATA_API_KEY")
        self.api_key, self.timeout = api_key, timeout

    def find_by_inn(self, inn: str) -> list[CompanyInfo]:
        import httpx
        r = httpx.post(self.URL, json={"query": inn, "count": 5}, timeout=self.timeout,
                       headers={"Authorization": f"Token {self.api_key}", "Content-Type": "application/json", "Accept": "application/json"})
        r.raise_for_status()
        return [parse_dadata(s) for s in (r.json().get("suggestions") or [])]


def parse_dadata(s: dict) -> CompanyInfo:
    d = s.get("data") or {}
    name = d.get("name") or {}
    mgmt = d.get("management") or {}
    state = d.get("state") or {}
    addr = d.get("address") or {}
    reg = state.get("registration_date")
    from datetime import datetime, timezone
    reg_on = datetime.fromtimestamp(reg / 1000, tz=timezone.utc).date().isoformat() if isinstance(reg, (int, float)) else None
    director = ", ".join(x for x in (mgmt.get("post") or "", mgmt.get("name") or "") if x) if mgmt else ""
    if not director and d.get("type") == "INDIVIDUAL":
        fio = d.get("fio") or {}
        director = " ".join(x for x in (fio.get("surname"), fio.get("name"), fio.get("patronymic")) if x)
    return CompanyInfo(
        inn=d.get("inn") or "", name=name.get("short_with_opf") or s.get("value") or "", full_name=name.get("full_with_opf") or "",
        kpp=d.get("kpp") or "", ogrn=d.get("ogrn") or "", address=(addr.get("unrestricted_value") or addr.get("value") or ""),
        director=director, status=state.get("status") or "", okved=d.get("okved") or "", registered_on=reg_on, kind=d.get("type") or "",
        source="dadata", raw={k: d.get(k) for k in ("inn", "kpp", "ogrn", "okved", "type", "branch_type", "opf")},
    )


def make_registry(provider: str, dadata_key: str = "") -> CompanyRegistry:
    if provider == "dadata":
        return DaDataRegistry(dadata_key)
    return CompanyRegistry()


def inn_valid(inn: str) -> bool:
    """Контрольные цифры ИНН (10 знаков — организация, 12 — ИП/физлицо)."""
    if not inn.isdigit() or len(inn) not in (10, 12):
        return False
    d = [int(c) for c in inn]
    def ctrl(coefs: list[int], digits: list[int]) -> int:
        return sum(c * x for c, x in zip(coefs, digits)) % 11 % 10
    if len(inn) == 10:
        return ctrl([2, 4, 10, 3, 5, 9, 4, 6, 8], d) == d[9]
    return (ctrl([7, 2, 4, 10, 3, 5, 9, 4, 6, 8], d) == d[10]
            and ctrl([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8], d) == d[11])
