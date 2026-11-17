"""Обязательные поля заявки (D-069): позиция по требованиям клиента без завода, модели и цены; настройка ЗГД."""
import os

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан", allow_module_level=True)

from tests.test_api_flow import client, _ok, H, MGR, CD  # noqa: F401,E402

GD = "evgeniy@dobrov.pro"


def _deal(client):
    clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
    return _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "Требования"}), 201)["id"]


def test_position_from_client_requirements_only(client):
    did = _deal(client)
    url = f"/api/deals/{did}/positions"
    r = client.post(url, headers=H(MGR), json={"qty": 2})
    assert r.status_code == 422 and "Тип техники" in r.json()["detail"]["missing"]
    pos = _ok(client.post(url, headers=H(MGR), json={"machine_type": "Самосвал шахтный", "qty": 2,
              "form_rows": [{"code": "1.8", "value": "", "requirement": "не менее 50 т", "comment": ""}]}), 201)
    assert pos["factory_id"] is None and pos["model"] == "" and pos["unit_price"] == 0 and not pos["production_term"]
    # перед расчётом — завод, модель, цена (по умолчанию)
    d = _ok(client.get(f"/api/deals/{did}", headers=H(MGR)))
    assert any("завод" in c for c in d["checks"]) and any("цена" in c for c in d["checks"])
    assert client.post(f"/api/deals/{did}/send-to-calc", headers=H(MGR)).status_code in (409, 422)
    # завод, модель и цена — из ответа завода; после этого проверки проходят
    factories = _ok(client.get("/api/refs/factories", headers=H(MGR)))
    _ok(client.patch(f"{url}/{pos['id']}", headers=H(MGR), json={"factory_id": factories[0]["id"], "model": "RQ-50", "unit_price": 1_500_000, "currency": "CNY"}))
    d = _ok(client.get(f"/api/deals/{did}", headers=H(MGR)))
    assert not [c for c in d["checks"] if "Позиция 1: не заполнено" in c], d["checks"]
    # обязательное при создании нельзя стереть правкой
    assert client.patch(f"{url}/{pos['id']}", headers=H(MGR), json={"machine_type": ""}).status_code == 422


def test_required_fields_setting(client):
    meta = _ok(client.get("/api/admin/settings/meta", headers=H(GD)))["items"]["request_required_fields"]
    assert meta["group"] == "Заявка" and {o["value"] for o in meta["options"]} >= {"machine_type", "factory", "unit_price"}
    put = lambda who, v: client.put("/api/admin/settings/request_required_fields", headers=H(who), json={"value": v})   # noqa: E731
    assert put(CD, {"create": [], "calc": []}).status_code == 403
    # модель обязательна при создании → и перед расчётом; цена перед расчётом не требуется; неизвестные ключи отбрасываются
    r = _ok(put(GD, {"create": ["machine_type", "model", "xxx"], "calc": ["factory"]}))
    assert r["value"] == {"create": ["machine_type", "model"], "calc": ["machine_type", "factory", "model"]}
    tpl = _ok(client.get("/api/deals/rfq/template", headers=H(MGR)))
    assert set(tpl["required"]) == {"1.1", "1.2"} and tpl["required_fields"]["create"] == ["machine_type", "model"]
    did = _deal(client)
    assert client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"machine_type": "ПДМ"}).status_code == 422
    _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"machine_type": "ПДМ", "model": "X"}), 201)
    checks = _ok(client.get(f"/api/deals/{did}", headers=H(MGR)))["checks"]
    assert any("завод" in c for c in checks) and not any("цена" in c for c in checks)
    _ok(put(GD, {"create": ["machine_type", "qty"], "calc": ["factory", "model", "qty", "unit_price", "currency"]}))
