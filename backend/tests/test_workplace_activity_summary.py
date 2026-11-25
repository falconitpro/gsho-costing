"""D-175 (пакет A2): «Последняя активность» на рабочем месте и «Сводка → Показатели» — только чтение и только в пределах прав."""
from datetime import date

from tests.test_api_flow import client, _ok, H, MGR, LOG, CD  # noqa: F401

GD, FD = "gd@example.com", "fd@example.com"


def _deal(client, site):
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    return _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": site}), 201)["id"]


def test_activity_visible_deals_only_and_whitelist(client):
    did = _deal(client, "Активность A2")
    gd = _ok(client.get("/api/workplace/activity?limit=50", headers=H(GD)))
    mine = [i for i in gd["items"] if i["deal_id"] == did]
    assert any(i["title"] == "Создана сделка" and i["author"] for i in mine)
    assert all(i["url"].startswith(f"/deals/{did}") for i in mine)
    # в ленте — только события из белого списка, без сумм и цен
    kinds = {i["kind"] for i in gd["items"]}
    assert not any(k.startswith("finance_") or k.startswith("variant.") for k in kinds)
    assert not any("₽" in i["title"] for i in gd["items"])
    # логист не видит черновик менеджера — и событий по нему тоже
    lg = _ok(client.get("/api/workplace/activity?limit=50", headers=H(LOG)))
    assert not any(i["deal_id"] == did for i in lg["items"])
    # постраничность: next_before ведёт на более ранние события, без повторов
    p1 = _ok(client.get("/api/workplace/activity?limit=3", headers=H(GD)))
    if p1["next_before"]:
        p2 = _ok(client.get(f"/api/workplace/activity?limit=3&before={p1['next_before']}", headers=H(GD)))
        assert not ({i["id"] for i in p1["items"]} & {i["id"] for i in p2["items"]})
        assert all(i["at"] <= p1["items"][-1]["at"] for i in p2["items"])


def test_summary_overview_rules_and_rights(client):
    _deal(client, "Показатели A2")
    d = _ok(client.get("/api/summary/overview?kind=quarter", headers=H(GD)))
    today = date.today()
    q0 = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    assert d["period"]["from"] == q0.isoformat() and not d["period"]["future"]
    keys = [k["key"] for k in d["kpis"]]
    assert {"created", "won", "lost", "win_rate"} <= set(keys)
    created = next(k for k in d["kpis"] if k["key"] == "created")
    assert created["current"] >= 1
    # плана продаж нет — отклонение к плану не считается и не придумывается
    sp = next(r for r in d["table"] if r["key"] == "sales_plan")
    assert sp["plan"] is None and sp["deviation"] is None
    # динамика: число интервалов квартала — 3 месяца; будущие — без значений
    assert len(d["series"]) == 3 and all((b["won"] is None) == b["future"] for b in d["series"])
    # месяц — недели, год — 12 месяцев; будущий период — пустой
    assert len(_ok(client.get("/api/summary/overview?kind=year", headers=H(GD)))["series"]) == 12
    fut = _ok(client.get(f"/api/summary/overview?kind=year&anchor={today.year + 1}-06-01", headers=H(GD)))
    assert fut["period"]["future"] and fut["kpis"] == []
    # менеджер: без финансовой аналитики — без сумм и денег
    m = _ok(client.get("/api/summary/overview", headers=H(MGR)))
    assert not m["finance"] and "won_sum" not in [k["key"] for k in m["kpis"]] and not any(r["key"].startswith("money_") for r in m["table"])
    # запчасти: деньги «Финансов» не делятся по направлениям — не показываются
    p = _ok(client.get("/api/summary/overview?direction=parts", headers=H(GD)))
    assert not any(r["key"].startswith("money_") for r in p["table"])
    # роли без сводных показателей
    assert client.get("/api/summary/overview", headers=H(LOG)).status_code == 403
