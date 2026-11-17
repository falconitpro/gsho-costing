"""Шапка заявки, утверждение новых типов техники, заполнение по шаблону типа (D-071)."""
import os

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан", allow_module_level=True)

from tests.test_api_flow import client, _ok, H, MGR, CD  # noqa: F401,E402

GD = "evgeniy@dobrov.pro"


def test_header_default_and_edit(client):
    rows = _ok(client.get("/api/refs/request-header", headers=H(MGR)))["rows"]
    codes = {r["code"] for r in rows}
    assert "0.1" in codes and "14.1" not in codes and "13.5" not in codes and "1.1" not in codes   # поля карточки — не в шапке
    assert {r["section"].split(".")[0] for r in rows} == {"0", "12", "13", "14"}
    assert client.put("/api/refs/request-header", headers=H(MGR), json=rows).status_code == 403
    new = [{"code": "0.1", "name_ru": "Дата запроса", "section": "Запрос"}, {"name_ru": "Номер тендера", "section": "Запрос", "required": True},
           {"name_ru": "  ", "section": "Запрос"}]
    assert _ok(client.put("/api/refs/request-header", headers=H(CD), json=new))["rows"] == 2
    got = _ok(client.get("/api/refs/request-header", headers=H(MGR)))["rows"]
    assert [(r["no"], r["name_ru"], r["required"]) for r in got] == [(1, "Дата запроса", False), (2, "Номер тендера", True)]
    x = client.get("/api/refs/tech-param-templates/file?kind=header", headers=H(MGR))
    assert x.status_code == 200 and x.content[:2] == b"PK"
    # через общий экран настроек шапку не поменять
    assert client.put("/api/admin/settings/request_header_rows", headers=H(GD), json={"value": []}).status_code == 403


def test_new_machine_type_approval_flow(client):
    t = _ok(client.post("/api/refs/machine-types", headers=H(MGR), json={"name": "  Буровой   станок "}), 201)
    assert t["status"] == "pending" and t["name"] == "Буровой станок"
    assert client.post("/api/refs/machine-types", headers=H(MGR), json={"name": "буровой станок"}).status_code == 409
    from app.db import SessionLocal
    from app.models import NotificationLog
    from sqlalchemy import select
    with SessionLocal() as db:
        assert db.scalar(select(NotificationLog).where(NotificationLog.event == "machine_type", NotificationLog.text.contains("Буровой станок"))) is not None
    # позиция с новым типом заполняется по типовому шаблону
    clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
    did = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "Типы"}), 201)["id"]
    pos = _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"machine_type": "Буровой станок", "qty": 1}), 201)
    tpl = _ok(client.get(f"/api/refs/tech-param-templates?machine_type_id={t['id']}", headers=H(MGR)))
    assert tpl["inherited"] and len(tpl["rows"]) > 50
    assert client.post(f"/api/refs/machine-types/{t['id']}/approve", headers=H(MGR), json={"name": "x"}).status_code == 403
    # утверждение с исправленным названием — позиции получают новое
    ok = _ok(client.post(f"/api/refs/machine-types/{t['id']}/approve", headers=H(CD), json={"name": "Буровая установка"}))
    assert ok["status"] == "approved"
    d = _ok(client.get(f"/api/deals/{did}", headers=H(MGR)))
    assert next(p for p in d["positions"] if p["id"] == pos["id"])["machine_type"] == "Буровая установка"
    # отклонение: позиции переводятся на выбранный тип
    t2 = _ok(client.post("/api/refs/machine-types", headers=H(MGR), json={"name": "Самосвал-дубль"}), 201)
    p2 = _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"machine_type": "Самосвал-дубль", "qty": 1}), 201)
    assert client.post(f"/api/refs/machine-types/{t2['id']}/reject", headers=H(GD), json={}).status_code == 422
    _ok(client.post(f"/api/refs/machine-types/{t2['id']}/reject", headers=H(GD), json={"replace_with": t["id"]}))
    d = _ok(client.get(f"/api/deals/{did}", headers=H(MGR)))
    assert next(p for p in d["positions"] if p["id"] == p2["id"])["machine_type"] == "Буровая установка"
    assert not any(x["name"] == "Самосвал-дубль" for x in _ok(client.get("/api/refs/machine-types", headers=H(MGR))))


def test_template_rows_not_duplicated(client):
    mt = _ok(client.post("/api/refs/machine-types", headers=H(CD), json={"name": "Погрузчик шахтный (тест)"}), 201)
    _ok(client.put(f"/api/refs/tech-param-templates?machine_type_id={mt['id']}", headers=H(CD),
                   json=[{"no": 1, "name_ru": "Грузоподъёмность ковша", "unit": "т", "section": "Рабочее оборудование"}]))
    clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
    did = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "Шаблон типа"}), 201)["id"]
    row = {"name": "Грузоподъёмность ковша", "unit": "т", "requirement": "≥ 14", "section": "Рабочее оборудование"}
    hdr = {"name": "Номер тендера", "section": "Запрос", "skip_empty": True}
    pos = _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"machine_type": mt["name"], "qty": 1,
              "custom_rows": [hdr, row], "row_order": ["x:0", "x:1"]}), 201)
    url = f"/api/deals/{did}/positions/{pos['id']}"
    tr = _ok(client.get(f"{url}/tech-params", headers=H(MGR)))["rows"]
    assert [r["name_ru"] for r in tr] == ["Грузоподъёмность ковша"] and tr[0]["customer_requirement_ru"] == "≥ 14"   # пустое поле шапки не создаётся
    _ok(client.patch(url, headers=H(MGR), json={"custom_rows": [{**hdr, "value": "Т-77"}, {**row, "requirement": "≥ 15"}], "row_order": ["x:0", "x:1"]}))
    tr = _ok(client.get(f"{url}/tech-params", headers=H(MGR)))["rows"]
    assert [(r["name_ru"], r["section"]) for r in tr] == [("Номер тендера", "Запрос"), ("Грузоподъёмность ковша", "Рабочее оборудование")]
    assert tr[1]["customer_requirement_ru"] == "≥ 15" and tr[0]["value_ru"] == "Т-77"
