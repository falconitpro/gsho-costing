"""Автоматическая приёмка «Пульт ЗГД и поручения» (D-158, ТЗ аудитора «План автоматизированной приёмки»).

34 проверяемых случая (A01…A34 — шаги PDF «Сценарии_пульт_ЗГД_и_поручения») + AUD-01…06 + адаптивность.
Каждый случай проверяет результат машинно (ответ сервера, данные, состояние интерфейса); скриншот — только доказательство.
Статусы: PASS / FAIL / BLOCKED (не выполнен: нет среды или упал обязательный предыдущий шаг) / FLAKY (упал, со второй попытки прошёл —
для приёмки считается непройденным). Код выхода ≠ 0, если есть хоть один не-PASS.

Данные: свой «эталонный» набор на каждый прогон (метка RUN_ID в названиях) — сделка с известной маржой, два просроченных платежа клиента
в юанях, просроченный этап исполнения, черновик без цены; ожидаемые значения известны заранее. Проверки идут по своим объектам и по
приращениям показателей, поэтому повторный прогон на той же базе не ломается; созданные поручения в конце отменяются.

Запуск (стенд разработки: APP_ENV=dev, вход заголовком X-Dev-User; в рабочем режиме он отключён — скрипт это проверит):
  BASE_URL=http://localhost:5173 API_URL=http://localhost:8000 BROWSER_EXECUTABLE=$(command -v google-chrome) \
  python3 scripts/acceptance/acceptance.py [папка-отчёта]
Отчёт: summary.html, junit.xml, results.json, manifest.json, screenshots/, trace.zip, api-failures.json.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import platform
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("BASE_URL", "http://localhost:5173").rstrip("/")
API = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
BROWSER = os.environ.get("BROWSER_EXECUTABLE") or None
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ACCEPT_OUT", "artifacts/acceptance"))
RUN_ID = os.environ.get("RUN_ID") or dt.datetime.now().strftime("%m%d-%H%M%S")
RETRY = os.environ.get("ACCEPT_RETRY", "1") == "1"
UI = os.environ.get("ACCEPT_UI", "classic")      # classic — прежний вид, new — «Новый вид» (D-159, переключатель)
TAG = f"[приёмка {RUN_ID}]"

MGR, CD, FD, LOG, LAW, ADM, SVC = ("manager@example.com", "cd@example.com", "fd@example.com", "logist@example.com", "lawyer@example.com",
                                   "admin@example.com", "service@example.com")
GD = os.environ.get("ACCEPT_GD_EMAIL", "")       # по умолчанию — найти пользователя с ролью ЗГД в справочнике сотрудников

API_FAILS: list[dict] = []


class Http:
    """Минимальный клиент API от имени пользователя стенда (заголовок X-Dev-User)."""

    def __init__(self, user: str):
        self.user = user

    def call(self, method: str, path: str, body=None, expect=None):
        data = json.dumps(body).encode() if body is not None else None
        rq = urllib.request.Request(API + path, data=data, method=method,
                                    headers={"X-Dev-User": self.user, "Content-Type": "application/json", "X-Run-Id": RUN_ID})
        try:
            with urllib.request.urlopen(rq, timeout=60) as r:
                code, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read()
        try:
            js = json.loads(raw or b"null")
        except ValueError:
            js = {"raw": raw[:300].decode(errors="replace")}
        if expect is not None and code not in (expect if isinstance(expect, tuple) else (expect,)):
            API_FAILS.append({"method": method, "url": path, "status": code, "role": self.user.split("@")[0], "expected": expect,
                              "detail": (js.get("detail") if isinstance(js, dict) else str(js))[:300] if js else ""})
            raise AssertionError(f"{method} {path} от {self.user.split('@')[0]}: ожидался {expect}, получен {code}: {str(js)[:200]}")
        return code, js

    def get(self, path, expect=200):
        return self.call("GET", path, expect=expect)[1]

    def post(self, path, body=None, expect=(200, 201)):
        return self.call("POST", path, body or {}, expect=expect)[1]

    def put(self, path, body=None, expect=200):
        return self.call("PUT", path, body or {}, expect=expect)[1]


# ------------------------------------------------------------------ реестр случаев

CASES: list[dict] = []


def case(cid: str, role: str, title: str, expected: str, needs: tuple = (), group: str = "", retry: bool = False):
    """retry=True — только для проверок без побочных эффектов: повтор после падения даёт статус FLAKY (не засчитывается)."""
    def deco(fn):
        CASES.append({"id": cid, "role": role, "title": title, "expected": expected, "needs": needs, "fn": fn, "group": group, "retry": retry})
        return fn
    return deco


class Ctx:
    """Общее состояние прогона: пользователи, эталонные объекты, страницы браузера."""
    def __init__(self):
        self.ids: dict[str, str] = {}
        self.golden: dict = {}
        self.pages: dict = {}
        self.shots: list[str] = []
        self.browser = None
        self.ctxs: list = []
        self.console: list[str] = []

    def page(self, user: str, width: int = 1440, height: int = 900):
        key = (user, width)
        if key not in self.pages:
            c = self.browser.new_context(viewport={"width": width, "height": height}, extra_http_headers={"X-Dev-User": user},
                                         timezone_id="Europe/Moscow", locale="ru-RU")
            c.tracing.start(screenshots=True, snapshots=True)
            role = {GD: "zgd", CD: "kd", MGR: "manager", FD: "fd", LOG: "logist", LAW: "outsider"}.get(user, "user")
            self.ctxs.append((c, f"{role}-{width}"))     # в именах файлов — роль, не адрес
            p = c.new_page()
            p.on("pageerror", lambda e, u=user: self.console.append(f"{u}: {e}"))
            p.goto(BASE + "/")
            p.evaluate(f"localStorage.setItem('gsho.gdView','pult'); localStorage.setItem('gsho.newUi','{'1' if UI == 'new' else '0'}')")
            self.pages[key] = p
        return self.pages[key]

    def shot(self, page, cid: str, full=False):
        (OUT / "screenshots").mkdir(parents=True, exist_ok=True)
        name = f"screenshots/{cid}-{len(self.shots):03d}.png"
        page.screenshot(path=str(OUT / name), full_page=full)
        self.shots.append(name)
        return name


X = Ctx()


def H(email):
    return Http(email)


def pult(page):
    page.goto(BASE + "/")
    page.wait_for_selector(".wp-exec-queue", timeout=45000)
    return page


def card(page, aid):
    page.goto(f"{BASE}/assignments?scope=all&status=any&open={aid}")
    page.wait_for_selector(".asg-card h2", timeout=30000)
    return page


def sig_row(page, title: str):
    """Строка сигнала сделки прогона на вкладке «Все проблемы» (на «Обзоре» — только 5 групп); группа раскрывается при необходимости."""
    pult(page)
    page.click(".wp-exec-tabs button:has-text('Все проблемы')")
    page.wait_for_selector(".wp-exec-chips")
    grp = page.locator(".wp-exec-grp").filter(has_text=X.golden["number"]).first
    row = grp.locator(".wp-exec-esc").filter(has_text=title).first
    if not row.is_visible() and grp.locator(".wp-exec-more").count():
        grp.locator(".wp-exec-more").click()
    row.wait_for(state="visible", timeout=15000)
    return row


def ex():
    return H(GD).get("/api/workplace/executive")["executive"]


def kpi(e, key):
    return next(k for k in e["kpis"] if k["key"] == key)


def no_hscroll(page):
    sw, iw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    assert sw <= iw, f"горизонтальная прокрутка: ширина документа {sw} > окна {iw}"
    return sw


# ------------------------------------------------------------------ подготовка: среда и эталонные данные

@case("S00", "система", "Среда: стенд разработки, сервисы отвечают, вход тестовыми пользователями", "API и интерфейс отвечают; X-Dev-User работает; ЗГД найден")
def s00(_):
    global GD
    code, prov = Http(ADM).call("GET", "/api/auth/providers")
    assert code == 200, f"API не отвечает: {code}"
    me = H(ADM).get("/api/auth/me")
    assert me.get("email") == ADM, "вход по X-Dev-User не работает — это не стенд разработки (APP_ENV=dev, AUTH_PROVIDER=dev)"
    if not GD:
        users = H(ADM).get("/api/team")["users"]
        gd = [u for u in users if "gd" in (u.get("roles") or []) and u.get("is_active")]
        assert gd, "нет активного пользователя с ролью ЗГД"
        gd.sort(key=lambda u: not u["email"].endswith("@example.com"))     # тестовая учётная запись — предпочтительнее
        GD = gd[0]["email"]
    with urllib.request.urlopen(BASE + "/", timeout=30) as r:
        assert r.status == 200
    for u in (GD, CD, MGR, FD, LOG, LAW):
        X.ids[u] = H(u).get("/api/auth/me")["id"]


def _approved_deal(currency: str, margin: float) -> str:
    """Сделка до утверждения расчёта (как в backend-тестах): позиция завода в юанях, логистика, цена по марже, условия оплаты."""
    m, lg, cd = H(MGR), H(LOG), H(CD)
    clients = m.get("/api/refs/clients")
    factories = m.get("/api/refs/factories")
    did = m.post("/api/deals", {"client_id": clients[0]["id"], "site": f"{TAG} эталон"})["id"]
    pos = m.post(f"/api/deals/{did}/positions", {"factory_id": factories[0]["id"], "model": "NG-7", "machine_type": "ПДМ", "qty": 1,
                                                 "unit_price": 1500000, "currency": "CNY"})
    var = m.get(f"/api/deals/{did}")["offer"]
    m.post(f"/api/deals/{did}/send-to-calc")
    items = {"door_to_door": {"amount": 200000, "currency": "CNY"}, "broker_rub": 200000, "comments": {"door_to_door": "x", "broker_rub": "x"}}
    lg.post(f"/api/deals/{did}/logistics/batches", {"items": items, "position_ids": [pos["id"]]})
    lg.put(f"/api/deals/{did}/logistics/positions/{pos['id']}", {"hs_code": "8429519000", "util_applies": False})
    lg.post(f"/api/deals/{did}/logistics/complete")
    cd.post(f"/api/deals/{did}/price", {"variant_id": var["id"], "mode": "margin", "value": margin, "currency": currency})
    schemes = cd.get("/api/refs/payment-schemes")
    calc = cd.get(f"/api/deals/{did}/calc")
    cd.post(f"/api/deals/{did}/payment-terms", {"client_payments": schemes[0]["lines"], "factory_payments": {pos["id"]: calc["payment_terms"]["default_factory"]}})
    assert cd.post(f"/api/deals/{did}/submit")["status"] == "approved"
    return did


@case("S01", "система", "Эталонные данные прогона", "сделка с маржой 17,5 % выиграна; оба платежа клиента в CNY просрочены на 40+ дн.; "
      "один этап исполнения просрочен; черновик без цены; руководители направлений назначены; у ФД — временный заместитель", needs=("S00",))
def s01(_):
    e0 = ex()
    X.golden["before"] = {k["key"]: k["value"] for k in e0["kpis"]}
    X.golden["active_before"] = kpi(e0, "active")
    did = _approved_deal("CNY", 17.5)
    H(MGR).post(f"/api/deals/{did}/kp-sent", {"recipients": ["client@example.com"]})
    H(CD).post(f"/api/deals/{did}/outcome", {"outcome": "won", "reason": "цена"})
    stages = H(CD).get(f"/api/deals/{did}/execution/stages")["stages"]
    pays = [s for s in stages if s["code"] in ("client_advance", "client_final")]
    assert len(pays) == 2, f"ожидалось 2 платежа клиента, есть {len(pays)}"
    late_day = (dt.date.today() - dt.timedelta(days=40)).isoformat()
    for s in pays:
        H(CD).post(f"/api/deals/{did}/execution/stages/{s['id']}/refine", {"refined_on": late_day, "note": TAG})
    other = next(s for s in stages if s["code"] not in ("client_advance", "client_final") and not s.get("actual_on") and s["kind"] != "payment_in")
    H(CD).post(f"/api/deals/{did}/execution/stages/{other['id']}/refine", {"refined_on": (dt.date.today() - dt.timedelta(days=5)).isoformat(), "note": TAG})
    deal = H(GD).get(f"/api/deals/{did}")
    X.golden.update(deal_id=did, number=deal["number"], pays=[s["id"] for s in pays], pay_amounts=[s["amount"] for s in pays], late_stage=other["id"], late_stage_title=other["title"])
    draft = H(MGR).post("/api/deals", {"client_id": H(MGR).get("/api/refs/clients")[0]["id"], "site": f"{TAG} без цены"})
    X.golden["draft"] = draft["id"]
    H(GD).put("/api/management/directions", {"heads": {"sales": X.ids[CD], "finance": X.ids[FD]}, "members": {}})
    to = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    H(GD).put(f"/api/team/{X.ids[FD]}/deputy", {"deputy_id": X.ids[LOG], "deputy_from": dt.date.today().isoformat(), "deputy_to": to})
    X.golden["deputy_to"] = to


# ------------------------------------------------------------------ А. Пульт и показатели

@case("A01", "ЗГД", "Пульт: шапка, 6 показателей, очередь", "6 карточек; у каждой период, время расчёта, методика; переключатель вида; очередь видна; нет ошибок страницы",
      needs=("S01",), group="А", retry=True)
def a01(_):
    e = ex()
    assert [k["key"] for k in e["kpis"]] == ["active", "risk", "won", "margin", "receivable", "exec"]
    for k in e["kpis"]:
        assert k["period"] and k["calculated_at"] and k["method"] and k["data_quality"] in ("confirmed", "partial", "stale", "unavailable", "unverified"), k["key"]
        if k["value"] is None:
            assert k["data_quality"] == "unavailable" and k["quality_note"], f"{k['key']}: нет данных без причины"
    p = pult(X.page(GD))
    assert p.locator(".wp-exec-kpi").count() == 6
    assert p.locator(".wp-exec-switch").is_visible() and p.locator("text=Создать поручение").first.is_visible()
    assert p.locator(".wp-exec-kpi-foot").count() == 6
    X.shot(p, "A01")


@case("A02", "ЗГД", "Пульт целиком: блоки и ближайшие сроки", "очередь, поручения, направления, денежный поток, согласования, юр. блок, ближайшие сроки; "
      "у срока — тип, ответственный, срочность; сроки по возрастанию даты", needs=("S01",), group="А", retry=True)
def a02(_):
    p = pult(X.page(GD))
    for t in ("Требует моего вмешательства", "Поручения", "Направления", "Денежный поток", "Согласования", "Юридический блок", "Ближайшие сроки"):
        assert p.locator(f"h2:has-text('{t}')").count() >= 1, f"нет блока «{t}»"
    d = ex()["deadlines"]
    for x in d:
        assert x["type"] and x["urgency"] in ("overdue", "today", "soon", "later") and x["url"].startswith("/")
    assert [x["on"] for x in d] == sorted(x["on"] for x in d)
    X.shot(p, "A02", full=True)


@case("A03", "ЗГД", "Дебиторка: методика, список, валюты, рубли справочно", "в окне — «Как считается» и источник; каждая строка — ссылка; "
      "суммы в валюте; если есть не рубли — справка «по курсу ЦБ на ДД.ММ»", needs=("S01",), group="А", retry=True)
def a03(_):
    e = ex()
    k = kpi(e, "receivable")
    dr = e["drill"].get("receivable")
    if k["value"] is not None:
        assert dr is not None and len(dr["rows"]) == k["value"], "число строк в расшифровке ≠ показателю"
        assert all(r["url"].startswith("/") for r in dr["rows"])
        if k["reference"]:
            assert re.search(r"по курсу ЦБ на \d\d\.\d\d", k["reference"]["text"]) and all(x["on"] for x in k["reference"]["rates"])
    p = pult(X.page(GD))
    p.click(".wp-exec-kpi >> nth=4")
    p.wait_for_selector(".wp-exec-method")
    assert "Как считается" in p.locator(".wp-exec-method").inner_text()
    X.shot(p, "A03")
    p.keyboard.press("Escape")


@case("A04", "ЗГД", "Активные сделки: состав суммы, сделки без цены не нули (AUD-06)", "черновик без цены → «частичные данные: сумма по X из Y», Y = показатель, X < Y; "
      "в расшифровке — сделки прогона", needs=("S01",), group="А")
def a04(_):
    e = ex()
    k = kpi(e, "active")
    assert k["data_quality"] == "partial", f"ожидались частичные данные, получено {k['data_quality']}"
    m = re.search(r"сумма по (\d+) из (\d+)", k["quality_note"])
    assert m and int(m.group(2)) == k["value"] and int(m.group(1)) < int(m.group(2)), k["quality_note"]
    assert k["value"] == X.golden["active_before"]["value"] + 1, "черновик прогона не попал в активные (выигранная сделка из активных уходит)"
    rows = e["drill"]["active"]["rows"]
    assert len(rows) == k["value"] and any(r["url"] == "/deals/" + X.golden["draft"] and r["value"] is None for r in rows), "черновик без цены не в списке или с суммой"
    p = pult(X.page(GD))
    p.click(".wp-exec-kpi >> nth=0")
    p.wait_for_selector(".wp-exec-drill")
    X.shot(p, "A04")
    p.keyboard.press("Escape")


@case("A05", "ЗГД", "Исполнение: просроченные этапы отдельно от сделок", "число этапов = строк расшифровки; этап прогона в списке; "
      "сделки с просроченными этапами и сдвиг срока — отдельными строками", needs=("S01",), group="А", retry=True)
def a05(_):
    e = ex()
    k = kpi(e, "exec")
    rows = e["drill"]["exec"]["rows"]
    assert k["unit"] == "этапов" and len(rows) == k["value"]
    assert any(X.golden["number"] in r["title"] and X.golden["late_stage_title"] in r["title"] for r in rows), "этап прогона не в списке"
    assert len(k["extra"]) == 2
    risk = kpi(e, "risk")
    assert any(X.golden["number"] in r["title"] for r in e["drill"]["risk"]["rows"]) and len(e["drill"]["risk"]["rows"]) == risk["value"]
    p = pult(X.page(GD))
    p.click(".wp-exec-kpi >> nth=5")
    p.wait_for_selector(".wp-exec-drill")
    X.shot(p, "A05")
    p.keyboard.press("Escape")


@case("A06", "ЗГД", "Очередь: каждый платёж отдельно, группа по сделке (AUD-03)", "2 сигнала по двум платежам сделки прогона, оба «критично», в одной группе, "
      "сумма в CNY и рубли справочно; у сигнала объект, направление, причина, ответственный, руководитель, следующий шаг", needs=("S01",), group="А")
def a06(_):
    e = ex()
    did = X.golden["deal_id"]
    mine = [s for s in e["escalations"] if s["group"] == f"deal:{did}" and ":pay:" in s["key"]]
    assert len(mine) == 2 and len({s["key"] for s in mine}) == 2, f"сигналов по платежам: {len(mine)}"
    for s in mine:
        assert s["priority"] == "critical" and s["impact"]["currency"] == "CNY" and "по курсу ЦБ" in (s["impact"].get("rub_ref") or "")
        assert s["object_type"] == "сделка" and s["object_number"] == X.golden["number"] and s["direction"] == "Коммерция"
        assert s["owner"] and s["head"] and s["next_action"] and s["url"].startswith("/deals/")
    assert any(s["key"] == f"deal:{did}:stages_late" for s in e["escalations"]), "нет сигнала о просроченном этапе"
    order = {"critical": 0, "high": 1, "normal": 2, "info": 3}
    assert [order[s["priority"]] for s in e["escalations"]] == sorted(order[s["priority"]] for s in e["escalations"])
    X.shot(pult(X.page(GD)), "A06")


@case("K01", "ЗГД", "Маржа: эталонная сделка и методика", "у сделки прогона на пульте — ровно маржа из её утверждённого расчёта (карточка сделки), "
      "≈17,5 % (заказано; цена округляется); "
      "показатель — средневзвешенное по цене, лежит между минимумом и максимумом строк расшифровки", needs=("S01",), group="А", retry=True)
def k01(_):
    e = ex()
    k = kpi(e, "margin")
    rows = e["drill"]["margin"]["rows"]
    mine = next((r for r in rows if r["url"] == "/deals/" + X.golden["deal_id"]), None)
    assert mine, "сделки прогона нет в расшифровке маржи (выиграна в этом квартале)"
    card_m = H(GD).get(f"/api/deals/{X.golden['deal_id']}")["now"]["sale"]["margin_pct"]     # источник — карточка сделки (утверждённый расчёт)
    want = f"{float(card_m):.1f} %".replace(".", ",")
    assert mine["value"] == want, f"на пульте {mine['value']}, в карточке сделки {want}"
    assert abs(float(card_m) - 17.5) < 0.5, f"расчёт дал маржу {card_m}, заказано 17,5 % (цена округляется)"
    vals = [float(r["value"].split()[0].replace(",", ".")) for r in rows if r["value"]]
    v = float(str(k["value"]).split()[0].replace(",", "."))
    assert min(vals) - 0.05 <= v <= max(vals) + 0.05, f"показатель {v} вне диапазона строк {min(vals)}…{max(vals)}"
    if k["data_quality"] == "partial":
        assert re.search(r"есть у (\d+) из (\d+)", k["quality_note"])


@case("K02", "ЗГД", "Выиграно за квартал и активные: список = число", "число строк расшифровки равно показателю; сделка прогона — в «выиграно»",
      needs=("S01",), group="А", retry=True)
def k02(_):
    e = ex()
    for key in ("won", "active", "risk"):
        assert len(e["drill"][key]["rows"]) == kpi(e, key)["value"], key
    assert any(r["url"] == "/deals/" + X.golden["deal_id"] for r in e["drill"]["won"]["rows"])


# ------------------------------------------------------------------ Б. Оргструктура

@case("A07", "ЗГД", "Руководители и состав направлений", "у «Коммерции» и «Финансов» — руководители; не назначенные — «не назначен»; "
      "закрепление сотрудника меняет его подразделение", needs=("S01",), group="Б", retry=True)
def a07(_):
    d = H(GD).get("/api/management/directions")
    by = {x["key"]: x for x in d["items"]}
    assert by["sales"]["head_id"] == X.ids[CD] and by["finance"]["head_id"] == X.ids[FD]
    assert any(x["head_id"] is None for x in d["items"]), "ожидалось хотя бы одно направление без руководителя"
    p = pult(X.page(GD))
    p.click("button:has-text('Руководители')")
    p.wait_for_selector(".sec-modal select")
    X.shot(p, "A07")
    p.keyboard.press("Escape")


@case("A08", "ЗГД", "Карточка направления «Коммерция»", "руководитель; сотрудник-менеджер «по роли»; поручения и история", needs=("S01",), group="Б", retry=True)
def a08(_):
    d = H(GD).get("/api/workplace/executive/direction/sales")
    assert d["head"]["id"] == X.ids[CD]
    st = {s["id"]: s for s in d["staff"]}
    assert X.ids[MGR] in st and st[X.ids[MGR]]["pinned"] is False
    p = X.page(GD)
    p.goto(BASE + "/directions/sales")
    p.wait_for_selector(".dir-head")
    X.shot(p, "A08", full=True)


@case("A09", "ЗГД", "Временное замещение руководителя «Финансов»", "в «Направлениях» и карточке — «замещение до ДД.ММ: ФИО заместителя»", needs=("S01",), group="Б", retry=True)
def a09(_):
    d = H(GD).get("/api/management/directions")
    fin = next(x for x in d["items"] if x["key"] == "finance")
    assert fin["deputy"] and fin["deputy"]["id"] == X.ids[LOG] and fin["deputy"]["temporary"] and fin["deputy"]["to"] == X.golden["deputy_to"]
    p = X.page(GD)
    p.goto(BASE + "/directions/finance")
    p.wait_for_selector(".dir-head")
    assert "замещение" in p.locator(".dir-head").inner_text()
    X.shot(p, "A09")


# ------------------------------------------------------------------ В. Поручение напрямую сотруднику из сигнала

@case("A10", "ЗГД", "«Поручить» из сигнала: ожидаемый результат обязателен (AUD-05)", "без ожидаемого результата кнопка недоступна; сервер отвечает 422",
      needs=("A06",), group="В")
def a10(_):
    e = ex()
    sig = next(s for s in e["escalations"] if s["group"] == f"deal:{X.golden['deal_id']}" and ":pay:" in s["key"])
    X.golden["sig"] = sig
    code, js = H(GD).call("POST", "/api/management/assignments", {"title": "x", "assignee_id": X.ids[MGR], "due_on": (dt.date.today() + dt.timedelta(days=2)).isoformat()})
    assert code == 422 and "ожидаемый результат" in str(js.get("detail")), f"сервер без ожидаемого результата: {code} {js}"
    p = X.page(GD)
    row = sig_row(p, sig["title"])
    row.locator("button:has-text('Поручить')").click()
    p.wait_for_selector(".sec-modal")
    p.check(".asg-mode input >> nth=1")
    p.select_option(".sec-modal select >> nth=0", "sales")
    p.select_option(".sec-modal select >> nth=1", value=X.ids[MGR])
    ta = p.locator(".sec-modal textarea >> nth=0")
    for _ in range(10):                       # в CI поле изредка возвращало подсказку сигнала после очистки — чистим до подтверждения
        ta.fill("")
        p.wait_for_timeout(150)
        if ta.input_value() == "":
            break
    btn = p.locator(".sec-modal .asg-actions button:has-text('Поручить')")
    for _ in range(20):                       # состояние кнопки — после перерисовки формы
        if btn.is_disabled():
            break
        p.wait_for_timeout(100)
    assert btn.is_disabled(), ("кнопка «Поручить» доступна без ожидаемого результата: "
                               f"поля={p.eval_on_selector_all('.sec-modal textarea, .sec-modal input', 'els => els.map(e => (e.type||e.tagName)+\'=\'+(e.type===\'radio\'?e.checked:e.value))')}")
    X.shot(p, "A10")


@case("A11", "ЗГД", "Поручение создано из сигнала", "источник = ключ сигнала; руководитель направления видит; срок по московской дате (AUD-04)",
      needs=("A10",), group="В")
def a11(_):
    p = X.page(GD)
    p.fill(".sec-modal textarea >> nth=0", f"Клиент письменно подтвердил дату оплаты {TAG}")
    due_ui = p.input_value(".sec-modal input[type=date]")
    msk = dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).date()
    assert due_ui >= msk.isoformat(), f"срок по умолчанию {due_ui} раньше московской даты {msk}"
    assert p.get_attribute(".sec-modal input[type=date]", "min") == msk.isoformat()
    X.shot(p, "A11")
    p.click(".sec-modal .asg-actions button:has-text('Поручить')")
    p.wait_for_timeout(1500)
    items = H(GD).get("/api/management/assignments?scope=all&status=any")["items"]
    a = next(x for x in items if x["source_key"] == X.golden["sig"]["key"])
    assert a["mode"] == "direct" and a["assignee_id"] == X.ids[MGR] and a["head_id"] == X.ids[CD] and a["direction_key"] == "sales"
    assert a["expected_result"].startswith("Клиент письменно")
    X.golden["A"] = a


@case("A12", "ЗГД", "Сигнал связан с поручением", "в строке сигнала — номер поручения и статус, кнопки «Поручить» нет", needs=("A11",), group="В")
def a12(_):
    e = ex()
    s = next(x for x in e["escalations"] if x["key"] == X.golden["sig"]["key"])
    assert s["assignment"] and s["assignment"]["number"] == X.golden["A"]["number"]
    p = X.page(GD)
    row = sig_row(p, X.golden["sig"]["title"])
    assert X.golden["A"]["number"] in row.inner_text() and row.locator("button:has-text('Поручить')").count() == 0
    X.shot(p, "A12")


def _act(user, action, **kw):
    return H(user).post(f"/api/management/assignments/{X.golden['A']['id']}/action", {"action": action, **kw})


@case("A13", "Исполнитель", "Исполнитель видит поручение «Мне» и уведомление", "в реестре «Мне»; уведомление со ссылкой на карточку", needs=("A11",), group="В")
def a13(_):
    a = X.golden["A"]
    mine = H(MGR).get("/api/management/assignments?scope=mine&status=open")["items"]
    assert any(x["id"] == a["id"] for x in mine)
    notes = H(MGR).get("/api/notifications")
    items = notes.get("items", notes) if isinstance(notes, dict) else notes
    assert any(a["number"] in (n.get("text") or "") and (n.get("url") or n.get("link") or "").endswith(a["id"]) for n in items), "нет уведомления исполнителю"
    p = X.page(MGR)
    p.goto(BASE + "/assignments")
    p.wait_for_selector(".asg-list")
    X.shot(p, "A13")


@case("A14", "Исполнитель", "Карточка у исполнителя", "ожидаемый результат, автор, срок, объект; действия — «Принять», «Комментарий»", needs=("A11",), group="В")
def a14(_):
    v = H(MGR).get(f"/api/management/assignments/{X.golden['A']['id']}")
    assert v["actions"] == ["accept", "comment"] and v["expected_result"]
    p = card(X.page(MGR), X.golden["A"]["id"])
    assert p.locator(".asg-acts button").all_inner_texts() == ["Принять", "Комментарий"]
    X.shot(p, "A14")


@case("A15", "Исполнитель", "Блокировка без причины отклоняется", "422 на сервере; в карточке — сообщение, карточка не пропадает", needs=("A14",), group="В")
def a15(_):
    assert _act(MGR, "accept")["status"] == "accepted" and _act(MGR, "start")["status"] == "in_progress"
    code, js = H(MGR).call("POST", f"/api/management/assignments/{X.golden['A']['id']}/action", {"action": "block"})
    assert code == 422 and "причин" in js["detail"].lower()
    p = card(X.page(MGR), X.golden["A"]["id"])
    p.click(".asg-acts button:has-text('Заблокировано')")
    p.click(".asg-form .asg-actions button:has-text('Заблокировано')")
    p.wait_for_selector(".asg-err")
    assert p.locator(".asg-card h2").is_visible()
    X.shot(p, "A15")


@case("A16", "Исполнитель", "Блокировка с причиной", "статус «заблокировано», причина в карточке; уведомление автору и руководителю", needs=("A15",), group="В")
def a16(_):
    r = _act(MGR, "block", comment=f"Бухгалтер клиента в отпуске {TAG}")
    assert r["status"] == "blocked" and "отпуске" in r["block_reason"]
    for u in (GD, CD):
        notes = H(u).get("/api/notifications")
        items = notes.get("items", notes) if isinstance(notes, dict) else notes
        assert any(X.golden["A"]["number"] in (n.get("text") or "") and "заблокировано" in (n.get("text") or "") for n in items), f"нет уведомления {u}"
    X.shot(card(X.page(MGR), X.golden["A"]["id"]), "A16")


@case("A17", "Исполнитель", "Подтверждение и отчёт", "файл приложен (антивирус пройден); без отчёта сдать нельзя (422)", needs=("A16",), group="В")
def a17(_):
    assert _act(MGR, "unblock")["status"] == "in_progress"
    p = card(X.page(MGR), X.golden["A"]["id"])
    p.set_input_files(".asg-up input", files=[{"name": f"письмо клиента {RUN_ID}.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 acceptance"}])
    p.wait_for_selector(".asg-files li button.asg-num")
    v = H(MGR).get(f"/api/management/assignments/{X.golden['A']['id']}")
    assert len(v["files"]) == 1 and v["files"][0]["name"].startswith("письмо клиента")
    X.golden["file_url"] = v["files"][0]["url"]
    code, _ = H(MGR).call("POST", f"/api/management/assignments/{X.golden['A']['id']}/action", {"action": "submit"})
    assert code == 422
    X.shot(p, "A17")


@case("A18", "Исполнитель", "Сдача на проверку", "статус «на проверке»; исполнителю доступны только комментарии", needs=("A17",), group="В")
def a18(_):
    assert _act(MGR, "submit", report="Клиент письмом подтвердил оплату")["status"] == "review"
    assert H(MGR).get(f"/api/management/assignments/{X.golden['A']['id']}")["actions"] == ["comment"]
    X.shot(card(X.page(MGR), X.golden["A"]["id"]), "A18")


@case("A19", "Руководитель", "Руководитель направления не принимает прямое поручение", "видит, комментирует; «Принять результат» — 403 на сервере, кнопки нет",
      needs=("A18",), group="В")
def a19(_):
    v = H(CD).get(f"/api/management/assignments/{X.golden['A']['id']}")
    assert v["my_role"] == ["head"] and v["actions"] == ["comment"]
    code, _ = H(CD).call("POST", f"/api/management/assignments/{X.golden['A']['id']}/action", {"action": "approve"})
    assert code == 403
    p = card(X.page(CD), X.golden["A"]["id"])
    assert p.locator(".asg-acts button:has-text('Принять результат')").count() == 0
    X.shot(p, "A19")


@case("A20", "Посторонний", "Посторонний: поручение и файл недоступны", "карточка и файл — 404; в интерфейсе «Поручение не найдено»", needs=("A17",), group="В")
def a20(_):
    assert H(LAW).call("GET", f"/api/management/assignments/{X.golden['A']['id']}")[0] == 404
    assert H(LAW).call("GET", X.golden["file_url"])[0] == 404
    assert H(LAW).call("GET", "/api/management/assignees")[0] == 403          # AUD-01
    p = X.page(LAW)
    p.goto(f"{BASE}/assignments?open={X.golden['A']['id']}")
    p.wait_for_selector("text=Поручение не найдено", timeout=20000)
    X.shot(p, "A20")


@case("A21", "ЗГД", "Возврат на доработку только с причиной", "без причины — 422; с причиной — «на доработке»", needs=("A18",), group="В")
def a21(_):
    code, _ = H(GD).call("POST", f"/api/management/assignments/{X.golden['A']['id']}/action", {"action": "return"})
    assert code == 422
    assert _act(GD, "return", comment="Нужна выписка банка")["status"] == "rework"
    assert _act(MGR, "submit", report="Приложил письмо с номером платёжки")["status"] == "review"
    X.shot(card(X.page(GD), X.golden["A"]["id"]), "A21")


@case("A22", "ЗГД", "Перенос срока", "только автор/ЗГД и с причиной; исполнитель — 403; первоначальный срок сохранён", needs=("A21",), group="В")
def a22(_):
    a = X.golden["A"]
    new = (dt.date.fromisoformat(a["due_on"]) + dt.timedelta(days=2)).isoformat()
    assert H(MGR).call("POST", f"/api/management/assignments/{a['id']}/action", {"action": "reschedule", "due_on": new, "comment": "x"})[0] == 403
    assert H(GD).call("POST", f"/api/management/assignments/{a['id']}/action", {"action": "reschedule", "due_on": new})[0] == 422
    r = _act(GD, "reschedule", due_on=new, comment="Ждём поступление по банку")
    assert r["due_on"] == new and r["first_due_on"] == a["due_on"] and r["rescheduled"]


@case("A23", "ЗГД", "Приёмка результата и полная история", "«выполнено»; история по порядку: создано → … → результат принят; у каждого события — автор и время",
      needs=("A22",), group="В")
def a23(_):
    r = _act(GD, "approve")
    assert r["status"] == "done"
    h = H(GD).get(f"/api/management/assignments/{X.golden['A']['id']}")["history"]
    acts = [x["action"] for x in h]
    assert acts == ["create", "accept", "start", "block", "unblock", "file", "submit", "return", "submit", "reschedule", "approve"], acts
    assert all(x["by"] and x["at"] for x in h) and [x["at"] for x in h] == sorted(x["at"] for x in h)
    X.shot(card(X.page(GD), X.golden["A"]["id"]), "A23", full=True)


# ------------------------------------------------------------------ Г. Поручение руководителю подразделения

@case("A24", "ЗГД", "Поручение руководителю подразделения", "назначенному руководителю — создаётся; другому сотруднику — 422", needs=("S01",), group="Г")
def a24(_):
    due = (dt.date.today() + dt.timedelta(days=4)).isoformat()
    base = {"title": f"План продаж на декабрь {TAG}", "expected_result": "план по клиентам", "mode": "head", "direction_key": "sales", "due_on": due}
    assert H(GD).call("POST", "/api/management/assignments", {**base, "assignee_id": X.ids[MGR]})[0] == 422
    X.golden["H"] = H(GD).post("/api/management/assignments", {**base, "assignee_id": X.ids[CD]})
    p = X.page(GD)
    p.goto(BASE + "/assignments?new=1")
    p.wait_for_selector(".sec-modal")
    p.select_option(".sec-modal select >> nth=0", "sales")
    assert "Руководитель:" in p.locator(".sec-modal").inner_text()
    X.shot(p, "A24")
    p.keyboard.press("Escape")


@case("A25", "Руководитель", "Руководитель принимает и передаёт исполнителю своего подразделения", "список — только сотрудники «Коммерции» (id, имя); "
      "сотрудник другого подразделения — 422", needs=("A24",), group="Г")
def a25(_):
    hid = X.golden["H"]["id"]
    assert H(CD).post(f"/api/management/assignments/{hid}/action", {"action": "accept"})["status"] == "accepted"
    lst = H(CD).get("/api/management/assignees")
    assert {d["key"] for d in lst["directions"]} == {"sales"} and all(set(u) == {"id", "name"} for u in lst["users"])
    assert X.ids[MGR] in {u["id"] for u in lst["users"]} and X.ids[FD] not in {u["id"] for u in lst["users"]}
    assert H(CD).call("POST", f"/api/management/assignments/{hid}/action", {"action": "delegate", "executor_id": X.ids[FD]})[0] == 422
    assert H(CD).post(f"/api/management/assignments/{hid}/action", {"action": "delegate", "executor_id": X.ids[MGR]})["executor_id"] == X.ids[MGR]
    X.shot(card(X.page(CD), hid), "A25")


@case("A26", "Исполнитель", "Исполнитель поручения руководителю", "не может принять поручение (403), может начать работу", needs=("A25",), group="Г")
def a26(_):
    hid = X.golden["H"]["id"]
    v = H(MGR).get(f"/api/management/assignments/{hid}")
    assert "executor" in v["my_role"] and "accept" not in v["actions"]
    assert H(MGR).call("POST", f"/api/management/assignments/{hid}/action", {"action": "accept"})[0] == 403
    assert H(MGR).post(f"/api/management/assignments/{hid}/action", {"action": "start"})["status"] == "in_progress"
    X.shot(card(X.page(MGR), hid), "A26")


# ------------------------------------------------------------------ Д. Контроль ЗГД

@case("A27", "ЗГД", "Счётчики поручений = реестр", "«в работе / просрочено / срок сегодня / на проверке» совпадают с числом строк реестра по тем же фильтрам",
      needs=("A26",), group="Д")
def a27(_):
    s = ex()["assignments"]
    for key, st in (("open", "inwork"), ("overdue", "overdue"), ("today", "today"), ("review", "review")):
        n = len(H(GD).get(f"/api/management/assignments?scope=all&status={st}")["items"])
        assert s[key] == n, f"{key}: на пульте {s[key]}, в реестре {n}"
    p = pult(X.page(GD))
    p.click(".wp-exec-asg a >> nth=0")
    p.wait_for_selector(".asg-list")
    assert "status=inwork" in p.url
    X.shot(p, "A27")


@case("A28", "ЗГД", "«Все проблемы»: фильтр и группы", "вкладка открывается; фильтр «критично» оставляет только критичные", needs=("S01",), group="Д", retry=True)
def a28(_):
    p = pult(X.page(GD))
    p.click(".wp-exec-tabs button:has-text('Все проблемы')")
    p.click(".wp-exec-chips button:has-text('критично')")
    pr = p.locator(".wp-exec-esc .wp-exec-prio").all_inner_texts()
    assert pr and all(t == "Критично" for t in pr)
    X.shot(p, "A28")


@case("A29", "ЗГД", "Устранённые сигналы", "после отметки оплаты сигнал уходит из очереди и появляется в «Устранено за 30 дней» с датами и длительностью",
      needs=("A06",), group="Д")
def a29(_):
    did, sid = X.golden["deal_id"], X.golden["pays"][0]
    key = f"deal:{did}:pay:{sid}"
    H(FD).post(f"/api/deals/{did}/execution/stages/{sid}/actual", {"actual_on": dt.date.today().isoformat(), "actual_amount": X.golden["pay_amounts"][0], "note": TAG},
            expect=(200, 201))
    e = ex()
    assert key not in {s["key"] for s in e["escalations"]}
    h = H(GD).get("/api/management/signals/history")["items"]
    row = next((x for x in h if x["key"] == key), None)
    assert row and row["first_seen"] and row["resolved_at"] and row["hours"] >= 0
    p = pult(X.page(GD))
    p.click(".wp-exec-tabs button:has-text('Все проблемы')")
    p.wait_for_selector("h2:has-text('Устранено за 30 дней')")
    X.shot(p, "A29", full=True)


# ------------------------------------------------------------------ Е. Адаптивность

def _width(cid, w, h):
    p = pult(X.page(GD, w, h))
    no_hscroll(p)
    btn = p.locator("button:has-text('Создать поручение')").first
    box = btn.bounding_box()
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= w, "кнопка «Создать поручение» за краем экрана"
    assert p.locator(".wp-exec-kpi").first.is_visible() and p.locator(".wp-exec-queue").is_visible()
    X.shot(p, cid, full=w <= 768)


@case("A30", "ЗГД", "Ширина 1280", "нет горизонтальной прокрутки; KPI, очередь и кнопки видны", needs=("S01",), group="Е", retry=True)
def a30(_):
    _width("A30", 1280, 900)


@case("A31", "ЗГД", "Ширина 768", "нет горизонтальной прокрутки; основные действия доступны", needs=("S01",), group="Е", retry=True)
def a31(_):
    _width("A31", 768, 1024)


@case("A32", "ЗГД", "Ширина 390", "нет горизонтальной прокрутки; карточки и кнопки помещаются", needs=("S01",), group="Е", retry=True)
def a32(_):
    _width("A32", 390, 844)


@case("A33", "ЗГД", "Ширина 360", "нет переполнения из-за номеров, сумм, ФИО и кнопок", needs=("S01",), group="Е", retry=True)
def a33(_):
    _width("A33", 360, 780)


@case("A34", "Исполнитель", "Карточка поручения на телефоне 390", "одна колонка, история и файлы видны, действия не обрезаны", needs=("A23",), group="Е", retry=True)
def a34(_):
    p = card(X.page(MGR, 390, 844), X.golden["A"]["id"])
    no_hscroll(p)
    for sel in (".asg-hist", ".asg-files"):
        assert p.locator(sel).count() == 1
    box = p.locator(".asg-card").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    X.shot(p, "A34", full=True)


# ------------------------------------------------------------------ AUD-01…06 (регрессия; часть покрыта шагами выше)

@case("AUD-01", "все роли", "Справочник для поручений: ЗГД — полный, руководитель — свой, остальные — 403", "как в описании", needs=("S01",), group="AUD")
def aud01(_):
    assert "position" in H(GD).get("/api/management/assignees")["users"][0]
    for u in (MGR, LAW, SVC):      # логист в прогоне временно замещает руководителя «Финансов» — ему список его подразделения положен
        assert H(u).call("GET", "/api/management/assignees")[0] == 403, u
    lg = H(LOG).get("/api/management/assignees")
    assert {d["key"] for d in lg["directions"]} == {"finance"}, "заместитель руководителя видит только его подразделение"


@case("AUD-02", "ЗГД", "Направление прямого поручения проверяет сервер; закрепление сотрудника", "чужое направление — 422; закрепление меняет руководителя",
      needs=("S01",), group="AUD")
def aud02(_):
    due = (dt.date.today() + dt.timedelta(days=2)).isoformat()
    b = {"title": f"проверка {TAG}", "expected_result": "y", "mode": "direct", "assignee_id": X.ids[MGR], "due_on": due}
    assert H(GD).call("POST", "/api/management/assignments", {**b, "direction_key": "finance"})[0] == 422
    H(GD).put("/api/management/directions", {"members": {X.ids[MGR]: "finance"}})
    try:
        r = H(GD).post("/api/management/assignments", b)
        assert r["direction_key"] == "finance" and r["head_id"] == X.ids[FD]
        X.golden.setdefault("extra", []).append(r["id"])
    finally:
        H(GD).put("/api/management/directions", {"members": {X.ids[MGR]: None}})


@case("AUD-03", "ЗГД", "Каждый просроченный платёж — отдельный сигнал", "см. A06", needs=("A06",), group="AUD")
def aud03(_):
    pass


@case("AUD-04", "ЗГД", "Московская дата в форме поручения", "см. A11 (срок по умолчанию и минимум — по МСК, браузер в зоне Europe/Moscow)", needs=("A11",), group="AUD")
def aud04(_):
    pass


@case("AUD-05", "ЗГД", "Ожидаемый результат обязателен (UI и сервер)", "см. A10", needs=("A10",), group="AUD")
def aud05(_):
    pass


@case("AUD-06", "ЗГД", "Неполные суммы — «частичные данные», не ноль", "см. A04", needs=("A04",), group="AUD")
def aud06(_):
    pass


@case("Z99", "система", "Уборка и ошибки страниц", "поручения прогона отменены, заместитель снят; ошибок страницы (pageerror) нет", needs=("S00",))
def z99(_):
    for aid in [X.golden.get("H", {}).get("id")] + X.golden.get("extra", []):
        if aid:
            H(GD).call("POST", f"/api/management/assignments/{aid}/action", {"action": "cancel", "comment": f"уборка {TAG}"})
    if X.ids.get(FD):
        H(GD).call("PUT", f"/api/team/{X.ids[FD]}/deputy", {"deputy_id": None})
    g = X.golden
    if g.get("deal_id"):      # сигналы прогона не должны копиться в очереди следующих прогонов
        did = g["deal_id"]
        for sid, amt in zip(g.get("pays", []), g.get("pay_amounts", [])):
            H(FD).call("POST", f"/api/deals/{did}/execution/stages/{sid}/actual", {"actual_on": dt.date.today().isoformat(), "actual_amount": amt, "note": f"уборка {TAG}"})
        H(CD).call("POST", f"/api/deals/{did}/execution/stages/{g['late_stage']}/refine",
                   {"refined_on": (dt.date.today() + dt.timedelta(days=30)).isoformat(), "note": f"уборка {TAG}"})
    assert not X.console, "ошибки страницы: " + "; ".join(X.console[:5])


# ------------------------------------------------------------------ прогон и отчёт

def run() -> int:
    from playwright.sync_api import sync_playwright
    OUT.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}
    t_start = time.time()
    with sync_playwright() as pw:
        X.browser = pw.chromium.launch(executable_path=BROWSER, args=["--no-sandbox"])
        for c in CASES:
            r = {k: c[k] for k in ("id", "role", "title", "expected", "group")}
            blocked = [n for n in c["needs"] if results.get(n, {}).get("status") not in ("PASS",)]
            if blocked:
                r.update(status="BLOCKED", error=f"не выполнен обязательный шаг: {', '.join(blocked)}", seconds=0, shots=[])
                results[c["id"]] = r
                print(f"{c['id']:7} BLOCKED  {c['title']}", flush=True)
                continue
            attempts = 2 if RETRY and c["retry"] else 1
            err = None
            for att in range(attempts):
                n0 = len(X.shots)
                t0 = time.time()
                try:
                    c["fn"](X)
                    err = None
                    break
                except Exception as e:  # noqa: BLE001
                    err = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
                    for p in list(X.pages.values())[-1:]:
                        try:
                            X.shot(p, f"{c['id']}-FAIL")
                        except Exception:  # noqa: BLE001
                            pass
            status = "PASS" if err is None and att == 0 else ("FLAKY" if err is None else "FAIL")
            r.update(status=status, error=err or "", seconds=round(time.time() - t0, 2), shots=X.shots[n0:], attempt=att + 1)
            results[c["id"]] = r
            print(f"{c['id']:7} {status:7}  {c['title']}" + (f"\n          {err.splitlines()[0]}" if err else ""), flush=True)
        for ctx, name in X.ctxs:
            try:
                (OUT / "traces").mkdir(exist_ok=True)
                ctx.tracing.stop(path=str(OUT / "traces" / f"{name}.zip"))
            except Exception:  # noqa: BLE001
                pass
        X.browser.close()
    report(list(results.values()), time.time() - t_start)
    bad = [r for r in results.values() if r["status"] != "PASS"]
    print(f"\nИТОГО: {len(results)} — PASS {len(results) - len(bad)}, не PASS {len(bad)}; отчёт: {OUT}/summary.html")
    return 1 if bad else 0


def _git_sha() -> str:
    for k in ("GITHUB_SHA",):
        if os.environ.get(k):
            return os.environ[k]
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "неизвестно"


def report(rows: list[dict], seconds: float) -> None:
    mig = sorted(p.name for p in Path(__file__).resolve().parents[2].joinpath("backend", "migrations").glob("*.sql"))
    try:
        from importlib.metadata import version
        pw_ver = version("playwright")
    except Exception:  # noqa: BLE001
        pw_ver = ""
    try:
        node = subprocess.check_output(["node", "--version"], text=True).strip()
    except Exception:  # noqa: BLE001
        node = ""
    manifest = {"sha": _git_sha(), "run_id": RUN_ID, "ui": UI, "started": dt.datetime.now().isoformat(timespec="seconds"), "seconds": round(seconds, 1),
                "base_url": BASE, "api_url": API, "python": platform.python_version(), "node": node, "playwright": pw_ver,
                "schema_migrations_in_code": mig[-1] if mig else "", "browser": BROWSER or "chromium (playwright)"}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    (OUT / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    (OUT / "api-failures.json").write_text(json.dumps(API_FAILS, ensure_ascii=False, indent=1))
    # JUnit
    from xml.sax.saxutils import escape, quoteattr
    fails = sum(r["status"] == "FAIL" for r in rows)
    other = sum(r["status"] in ("BLOCKED", "FLAKY") for r in rows)
    out = [f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="acceptance" tests="{len(rows)}" failures="{fails}" errors="{other}" time="{seconds:.1f}">']
    for r in rows:
        out.append(f'  <testcase classname="acceptance.{r["group"] or "setup"}" name={quoteattr(r["id"] + " " + r["title"])} time="{r["seconds"]}">')
        if r["status"] == "FAIL":
            out.append(f'    <failure message={quoteattr(r["error"].splitlines()[0] if r["error"] else "")}>{escape(r["error"])}</failure>')
        elif r["status"] in ("BLOCKED", "FLAKY"):
            out.append(f'    <error type="{r["status"]}" message={quoteattr(r["error"].splitlines()[0] if r["error"] else r["status"])}>{escape(r["error"])}</error>')
        out.append("  </testcase>")
    out.append("</testsuite>\n")
    (OUT / "junit.xml").write_text("\n".join(out))
    # HTML
    color = {"PASS": "#1a7f37", "FAIL": "#cf222e", "BLOCKED": "#9a6700", "FLAKY": "#8250df"}
    cnt = {s: sum(r["status"] == s for r in rows) for s in color}
    trs = []
    for r in rows:
        shots = " ".join(f'<a href="{html.escape(s)}">{html.escape(Path(s).stem)}</a>' for s in r.get("shots", []))
        err = f'<pre>{html.escape(r["error"])}</pre>' if r["error"] else ""
        trs.append(f'<tr><td>{html.escape(r["id"])}</td><td><b style="color:{color[r["status"]]}">{r["status"]}</b></td><td>{html.escape(r["role"])}</td>'
                   f'<td><b>{html.escape(r["title"])}</b><div class="e">Ожидается: {html.escape(r["expected"])}</div>{err}</td><td>{r["seconds"]}</td><td>{shots}</td></tr>')
    page = f"""<!doctype html><html lang="ru"><meta charset="utf-8"><title>Приёмка {html.escape(manifest['sha'][:7])}</title>
<style>body{{font:14px system-ui,sans-serif;margin:24px;color:#1f2328}}table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #d0d7de;padding:6px 8px;vertical-align:top;text-align:left}}
.e{{color:#59636e;font-size:12px}}pre{{white-space:pre-wrap;font-size:12px;background:#fff8f8;padding:6px;margin:4px 0}}</style>
<h1>Автоматическая приёмка «Пульт ЗГД и поручения»</h1>
<p>Коммит <b>{html.escape(manifest['sha'])}</b> · вид интерфейса: <b>{'новый' if UI == 'new' else 'прежний'}</b> · прогон {html.escape(RUN_ID)} · {html.escape(manifest['started'])} · {manifest['seconds']} с ·
миграции в коде до <b>{html.escape(manifest['schema_migrations_in_code'])}</b> · Python {manifest['python']} · Node {html.escape(node)} · Playwright {html.escape(pw_ver)}</p>
<p>{' · '.join(f'<b style="color:{color[s]}">{s}: {cnt[s]}</b>' for s in color)} · всего {len(rows)}.
Приёмка пройдена только если всё — PASS (BLOCKED и FLAKY не засчитываются).</p>
<p>Доказательства: screenshots/, traces/ (Playwright trace — <code>npx playwright show-trace traces/&lt;файл&gt;.zip</code>), api-failures.json, junit.xml.</p>
<table><tr><th>ID</th><th>Статус</th><th>Роль</th><th>Проверка</th><th>с</th><th>Снимки</th></tr>{''.join(trs)}</table></html>"""
    (OUT / "summary.html").write_text(page)


if __name__ == "__main__":
    sys.exit(run())
