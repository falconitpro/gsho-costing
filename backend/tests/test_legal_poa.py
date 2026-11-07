"""Доверенности компаний группы: конструктор → оформление (номер, DOCX-копия) → сканы → отзыв; напоминания об истечении; роли и скрытие паспортных данных."""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, GD, FD, LAW = "manager@example.com", "cd@example.com", "gd@example.com", "fd@example.com", "lawyer@example.com"

ATTORNEY = {"full_name": "Петров Пётр Петрович", "position": "Менеджер по продажам", "birth_date": "1990-01-01", "birth_place": "г. Москва",
            "passport": {"series": "4510", "number": "123456", "issued_by": "ОВД района Примерный г. Москвы", "issued_on": "2010-02-01", "code": "770-001"},
            "address": "г. Москва, ул. Лесная, д. 5, кв. 7", "phone": "+7 900 000-00-00"}


def _own(c) -> dict:
    items = c.get("/api/legal/own", headers=H(LAW)).json()["items"]
    ent = next((e for e in items if e["sells_to_clients"]), items[0])
    prof = c.get(f"/api/legal/profiles?owner_kind=own&owner_id={ent['id']}", headers=H(LAW)).json()["items"][0]
    # доверитель должен быть заполнен — как в карточке БЕРГ-ХАУС
    c.put(f"/api/legal/profiles/{prof['id']}", headers=H(LAW), json={"names": {"ru": {"full": "Общество с ограниченной ответственностью «БЕРГ-ХАУС»", "short": "ООО «БЕРГ-ХАУС»"}},
                                                                     "reg_number": "1177700000000", "tax_number": "7700000000", "kpp": "770001001", "legal_address": "123456, г. Москва, ул. Примерная, д. 1",
                                                                     "signatory_name": "Иванов Иван Иванович", "signatory_position": "Генеральный директор", "signatory_basis": "Устава"})
    return ent


def test_poa_lifecycle_and_roles():
    seed()
    with TestClient(app) as c:
        ent = _own(c)
        cat = c.get("/api/legal/poa/catalog", headers=H(MGR)).json()
        codes = [i["code"] for g in cat["powers"] for i in g["items"]]
        assert "general" in cat["kinds"] and len(codes) >= 15
        # оформляет юрист или ЗГД; КД, ФД, менеджер — нет
        body = {"entity_id": ent["id"], "kind": "special", "place": "г. Москва", "valid_to": (date.today() + timedelta(days=365)).isoformat(), "attorney": ATTORNEY,
                "powers": [{"code": codes[0]}, {"code": codes[1]}, {"text": "получать корреспонденцию в АО «Почта России»"}], "limits": {"amount_limit": 5000000, "currency": "RUB", "substitution": False, "territory": "Российская Федерация"}}
        for who in (CD, FD, MGR):
            assert c.post("/api/legal/poa", headers=H(who), json=body).status_code == 403, who
        p = c.post("/api/legal/poa", headers=H(LAW), json=body)
        assert p.status_code == 201, p.text
        p = p.json()
        assert p["status"] == "draft" and p["number"] == "" and p["principal"]["inn"] == "7700000000" and p["principal"]["signatory"] == "Иванов Иван Иванович"
        assert len(p["powers_resolved"]) == 3 and p["powers_resolved"][2]["text"].startswith("получать корреспонденцию")
        # черновик правится; проверка полноты
        p = c.put(f"/api/legal/poa/{p['id']}", headers=H(GD), json={"notes": "для тендера", "limits": {**body["limits"], "substitution": True}}).json()
        assert p["notes"] == "для тендера" and p["limits"]["substitution"] is True
        assert c.post(f"/api/legal/poa/{p['id']}/check", headers=H(LAW)).json()["ok"] is True
        # неполный черновик не оформляется
        bad = c.post("/api/legal/poa", headers=H(GD), json={"entity_id": ent["id"], "attorney": {"full_name": "Без Паспорта"}, "powers": []}).json()
        r = c.post(f"/api/legal/poa/{bad['id']}/issue", headers=H(GD), json={})
        assert r.status_code == 422 and any("паспорт" in e.lower() for e in r.json()["detail"]["errors"]) and any("полномочи" in e.lower() for e in r.json()["detail"]["errors"])
        assert c.post(f"/api/legal/poa/{bad['id']}/cancel", headers=H(GD)).json()["status"] == "cancelled"
        # предпросмотр проекта — DOCX
        r = c.get(f"/api/legal/poa/{p['id']}/preview", headers=H(LAW))
        assert r.status_code == 200 and r.content[:2] == b"PK"
        assert c.get(f"/api/legal/poa/{p['id']}/preview", headers=H(MGR)).status_code == 403
        # оформление: номер по компании и году, статус «действует», копия DOCX сохранена, представитель-не-сотрудник — без уведомления
        assert c.post(f"/api/legal/poa/{p['id']}/issue", headers=H(MGR), json={}).status_code == 403
        p = c.post(f"/api/legal/poa/{p['id']}/issue", headers=H(LAW), json={}).json()
        assert p["status"] == "active" and p["number"].startswith(f"Д-{ent['code'].upper()}-{date.today().year}-") and p["issued_on"] == date.today().isoformat()
        assert any(f["kind"] == "issued" and f["name"].endswith(".docx") for f in p["files"]) and p["days_left"] >= 360
        assert c.put(f"/api/legal/poa/{p['id']}", headers=H(LAW), json={"notes": "x"}).status_code == 409        # оформленная не редактируется
        # вторая доверенность той же компании — следующий номер
        p2 = c.post("/api/legal/poa", headers=H(GD), json=body).json()
        p2 = c.post(f"/api/legal/poa/{p2['id']}/issue", headers=H(GD), json={}).json()
        assert int(p2["number"].rsplit("-", 1)[1]) == int(p["number"].rsplit("-", 1)[1]) + 1
        # скан: загрузка, скачивание, удаление; оформленный экземпляр не удаляется
        r = c.post(f"/api/legal/poa/{p['id']}/files", headers=H(LAW), files={"file": ("scan.pdf", b"%PDF-1.4 test", "application/pdf")}, data={"kind": "signed_scan"})
        assert r.status_code == 201
        scan = next(f for f in r.json()["files"] if f["kind"] == "signed_scan")
        assert c.get(f"/api/legal/poa/{p['id']}/files/{scan['id']}", headers=H(MGR)).status_code == 200      # скан виден всем, кто видит реестр
        issued = next(f for f in r.json()["files"] if f["kind"] == "issued")
        assert c.get(f"/api/legal/poa/{p['id']}/files/{issued['id']}", headers=H(MGR)).status_code == 403    # текст с паспортом — нет
        assert c.delete(f"/api/legal/poa/{p['id']}/files/{issued['id']}", headers=H(LAW)).status_code == 409
        assert c.post(f"/api/legal/poa/{p['id']}/files", headers=H(MGR), files={"file": ("x.pdf", b"%PDF", "application/pdf")}).status_code == 403
        # реестр: менеджер видит без паспортных данных, юрист — с ними; сводка и фильтры
        reg_m = c.get(f"/api/legal/poa?entity_id={ent['id']}", headers=H(MGR)).json()
        assert reg_m["can_issue"] is False and reg_m["sensitive_visible"] is False
        mine = next(x for x in reg_m["items"] if x["id"] == p["id"])
        assert "passport" not in mine["attorney"] and "address" not in mine["attorney"] and mine["attorney"]["full_name"] == ATTORNEY["full_name"]
        reg_l = c.get(f"/api/legal/poa?entity_id={ent['id']}&status=active", headers=H(LAW)).json()
        assert reg_l["can_issue"] and next(x for x in reg_l["items"] if x["id"] == p["id"])["attorney"]["passport"]["number"] == "123456"
        assert reg_l["summary"]["active"] >= 2 and all(x["status"] == "active" for x in reg_l["items"])
        assert c.get(f"/api/legal/poa?q={p['number']}", headers=H(LAW)).json()["items"][0]["id"] == p["id"]
        # основание для подписанта в профиле
        prof = c.get(f"/api/legal/profiles?owner_kind=own&owner_id={ent['id']}", headers=H(LAW)).json()["items"][0]
        r = c.post(f"/api/legal/poa/{p['id']}/use-as-basis?profile_id={prof['id']}", headers=H(LAW)).json()
        assert r["signatory_name"] == ATTORNEY["full_name"] and p["number"] in r["signatory_basis"]
        # отзыв: только юрист/ЗГД, причина обязательна, уведомление об отмене сохранено, статус «отозвана»; черновик отозвать нельзя
        assert c.post(f"/api/legal/poa/{p['id']}/revoke", headers=H(CD), json={"reason": "увольнение"}).status_code == 403
        assert c.post(f"/api/legal/poa/{p['id']}/revoke", headers=H(LAW), json={"reason": ""}).status_code == 422
        p = c.post(f"/api/legal/poa/{p['id']}/revoke", headers=H(GD), json={"reason": "прекращение трудовых отношений"}).json()
        assert p["status"] == "revoked" and p["revoke_reason"] == "прекращение трудовых отношений" and any(f["kind"] == "revocation" for f in p["files"])
        assert c.post(f"/api/legal/poa/{p['id']}/revoke", headers=H(GD), json={"reason": "ещё раз"}).status_code == 409
        assert [h["action"] for h in p["history"]][:1] == ["revoked"]
        # продление — новый черновик с теми же полномочиями
        n = c.post(f"/api/legal/poa/{p2['id']}/renew", headers=H(LAW)).json()
        assert n["status"] == "draft" and n["number"] == "" and len(n["powers"]) == 3 and n["attorney"]["full_name"] == ATTORNEY["full_name"]
        # сотрудник-представитель получает уведомление при оформлении
        emp = reg_l["employees"][0]
        p3 = c.post("/api/legal/poa", headers=H(LAW), json={**body, "attorney_user_id": emp["id"]}).json()
        assert p3["attorney_user_id"] == emp["id"]


def test_poa_expiring_reminders_and_status():
    seed()
    from app.db import SessionLocal
    from app.models import PowerOfAttorney
    from app.services.legal_poa import check_expiring
    with TestClient(app) as c:
        ent = _own(c)
        body = {"entity_id": ent["id"], "kind": "single", "valid_to": (date.today() + timedelta(days=20)).isoformat(), "attorney": ATTORNEY, "powers": [{"text": "получить груз на складе"}]}
        p = c.post("/api/legal/poa", headers=H(LAW), json=body).json()
        p = c.post(f"/api/legal/poa/{p['id']}/issue", headers=H(LAW), json={}).json()
        assert p["expiring"] is True and p["days_left"] == 20
        with SessionLocal() as db:
            assert check_expiring(db) >= 1
            db.commit()
            poa = db.get(PowerOfAttorney, p["id"])
            assert "expiring_30" in poa.notified and poa.status == "active"
            assert check_expiring(db) == 0                                   # повторно за тот же порог не шлём
            assert check_expiring(db, today=date.today() + timedelta(days=15)) >= 1   # за 7 дней — ещё одно
            db.commit()
            assert check_expiring(db, today=date.today() + timedelta(days=30)) >= 1   # истекла
            db.commit()
            db.refresh(poa)
            assert poa.status == "expired" and "expired" in poa.notified
        assert c.get(f"/api/legal/poa/{p['id']}", headers=H(LAW)).json()["status"] == "expired"
        # истёкшую можно отозвать (например, чтобы зафиксировать возврат оригинала) и продлить
        assert c.post(f"/api/legal/poa/{p['id']}/revoke", headers=H(LAW), json={"reason": "срок истёк, оригинал возвращён"}).json()["status"] == "revoked"
