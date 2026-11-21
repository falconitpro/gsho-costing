"""Сквозные сценарии с проверкой цифр (D-119): заявка → расчёт → утверждение → КП → торги → договор;
сценарий 6 — Канцелярия (D-139): входящее → рассмотрение → резолюция → ответ → согласование → подпись → отправка → закрытие.

Каждый сценарий создаёт свою синтетическую сделку через API, а затем сверяет одни и те же деньги в трёх местах:
  1) ответ сервера (расчёт), 2) экран «Расчёт и утверждение» в браузере (панель решения, история КП),
  3) файл КП (.docx), который уходит клиенту; в сценарии «договор» — ещё и платежи клиента в этапах исполнения.

Запуск (backend на :8000 с AUTH_PROVIDER=dev и демо-данными `python -m scripts.seed`, frontend `npx vite --port 5173`):
    python3 scripts/e2e/scenarios.py http://localhost:5173 http://localhost:8000 [путь к chrome]
Рабочие данные не трогает. Код выхода 1 — есть расхождения.
"""
import asyncio
import io
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from docx import Document
from playwright.async_api import async_playwright

FE, BE = sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/")
CHROME = sys.argv[3] if len(sys.argv) > 3 else None
MGR, LOG, CD, GD = "manager@example.com", "logist@example.com", "cd@example.com", "evgeniy@dobrov.pro"
CLERK, LAW, FD = "office@example.com", "lawyer@example.com", "fd@example.com"
ROLE_USER = {"lawyer": LAW, "fd": FD, "cd": CD, "gd": GD, "manager": MGR, "logistician": LOG, "clerk": CLERK}

fails: list[str] = []
checks = 0


def api(user, method, path, body=None, raw=False):
    req = urllib.request.Request(BE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Dev-User": user, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
            return data if raw else (json.loads(data) if data else None)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} → {e.code}: {e.read()[:400]!r}")


def num(text: str) -> float:
    """«2 277 545 CNY», «17,63 %», «≈ 29 194 032 ₽» → число (первое в строке)."""
    m = re.search(r"-?\d[\d\s  ]*(?:[.,]\d+)?", text or "")
    if not m:
        raise ValueError(f"нет числа в «{text}»")
    return float(re.sub(r"[\s  ]", "", m.group(0)).replace(",", "."))


def check(scn: str, what: str, got, want, tol: float = 1.0):
    """Совпадение с точностью до округления (по умолчанию ±1 единица валюты)."""
    global checks
    checks += 1
    if got is None or want is None or abs(float(got) - float(want)) > tol:
        fails.append(f"{scn}: {what} — на проверке {got!r}, ожидалось {want!r}")


def check_true(scn: str, what: str, cond: bool, detail: str = ""):
    global checks
    checks += 1
    if not cond:
        fails.append(f"{scn}: {what}" + (f" ({detail})" if detail else ""))


# ---------- сделка через API ----------

def new_deal(site: str, positions: list[dict]) -> tuple[str, dict, list[dict]]:
    client = api(MGR, "GET", "/api/refs/clients")[0]
    factory = api(MGR, "GET", "/api/refs/factories")[0]
    did = api(MGR, "POST", "/api/deals", {"client_id": client["id"], "site": site})["id"]
    pos = [api(MGR, "POST", f"/api/deals/{did}/positions", {"factory_id": factory["id"], "machine_type": "ПДМ", "currency": "CNY", **p}) for p in positions]
    return did, api(MGR, "GET", f"/api/deals/{did}")["offer"], pos


def logistics(did: str, pos: list[dict], again: bool = False):
    api(MGR, "POST", f"/api/deals/{did}/send-to-calc")
    if again:                                    # после доработки партия уже есть — логист подтверждает расчёт
        api(LOG, "POST", f"/api/deals/{did}/logistics/complete")
        return
    items = {"door_to_door": {"amount": 100000, "currency": "CNY"}, "broker_rub": 100000, "comments": {"door_to_door": "e2e", "broker_rub": "e2e"}}
    api(LOG, "POST", f"/api/deals/{did}/logistics/batches", {"items": items, "position_ids": [p["id"] for p in pos]})
    for p in pos:
        api(LOG, "PUT", f"/api/deals/{did}/logistics/positions/{p['id']}", {"hs_code": "8429519000", "util_applies": False})
    api(LOG, "POST", f"/api/deals/{did}/logistics/complete")


def terms_and_submit(did: str, pos: list[dict]) -> str:
    schemes = api(CD, "GET", "/api/refs/payment-schemes")
    calc = api(CD, "GET", f"/api/deals/{did}/calc")
    api(CD, "POST", f"/api/deals/{did}/payment-terms", {"client_payments": schemes[0]["lines"],
                                                        "factory_payments": {p["id"]: calc["payment_terms"]["default_factory"] for p in pos}})
    return api(CD, "POST", f"/api/deals/{did}/submit")["status"]


def money(did: str, vid: str) -> dict:
    """Ожидаемые цифры из ответа сервера — так же, как их показывает экран: в валюте сделки по курсу цены, на 1 шт."""
    calc = api(CD, "GET", f"/api/deals/{did}/calc")
    deal = api(CD, "GET", f"/api/deals/{did}")
    v = next(x for x in deal["variants"] if x["id"] == vid)
    base = calc["result"]["variants"][vid]["base"]
    units = calc["units"][vid]
    cur = calc["deal_currency"]["code"]
    fx = v["price_fx_rate"] if cur != "RUB" else 1.0
    neg = (calc.get("negotiated") or {}).get(vid) or {}     # после торгов действует цена последнего шага (D-093)
    price_rub = neg["price_rub"] if neg.get("price_rub") is not None else v["price_rub"]
    price = price_rub / fx if neg.get("price_rub") is not None else v["price_amount"]
    check_true("расчёт", "расчёт по действующей цене", abs(base["price_rub"] - price_rub) < 1, f"{base['price_rub']} ≠ {price_rub}")
    return {"cur": cur, "fx": fx, "units": units, "vat": calc.get("sales_vat_pct") or 22, "status": deal["status"],
            "price": price, "price_rub": price_rub, "unit": price / units,
            "cost_unit": base["cost_total_rub"] / fx / units, "profit_unit": base["profit_rub"] / fx / units, "margin": base["margin_pct"]}


# ---------- файл КП ----------

def kp_rows(did: str, rev_id: str | None = None) -> tuple[dict[str, str], str]:
    path = f"/api/deals/{did}/kp/revisions/{rev_id}/file?format=docx" if rev_id else f"/api/deals/{did}/kp/file?format=docx"
    doc = Document(io.BytesIO(api(MGR, "GET", path, raw=True)))
    rows: dict[str, str] = {}
    for t in doc.tables:
        for r in t.rows:
            cells = [c.text.strip() for c in r.cells]
            if len(cells) >= 2 and cells[0] and cells[0] not in rows:
                rows[cells[0]] = cells[1]
    return rows, "\n".join(p.text for p in doc.paragraphs)


def check_kp(scn: str, did: str, m: dict, rev_id: str | None = None, label: str = "КП", qty_text: str | None = None):
    """qty_text — разные модели в одном предложении: количество по моделям и цена за комплект вместо средней «за единицу»."""
    rows, text = kp_rows(did, rev_id)
    cur = m["cur"]
    unit_key = f"Цена за единицу, {cur} без НДС"
    check_true(scn, f"{label}: строка «{unit_key}»", unit_key in rows, ", ".join(rows)[:200])
    if unit_key in rows and qty_text:
        check_true(scn, f"{label}: цена за комплект, а не средняя за единицу", rows[unit_key].startswith("за комплект"), rows[unit_key])
        check(scn, f"{label}: цена за комплект", num(rows[unit_key]), round(m["price"]))
    elif unit_key in rows:
        check(scn, f"{label}: цена за единицу", num(rows[unit_key]), round(m["unit"]))
    if qty_text:
        check_true(scn, f"{label}: количество по моделям", rows.get("Количество", "") == qty_text, rows.get("Количество", ""))
    else:
        check(scn, f"{label}: количество", num(rows.get("Количество", "")), m["units"], tol=0)
    check(scn, f"{label}: итого без НДС", num(rows.get(f"Итого, {cur} без НДС", "")), round(m["price"]))
    gross_key = next((k for k in rows if k.startswith(f"Итого, {cur} с НДС")), None)
    check_true(scn, f"{label}: строка «Итого с НДС»", gross_key is not None)
    if gross_key:
        check(scn, f"{label}: итого с НДС {m['vat']:g} %", num(rows[gross_key]), round(m["price"] * (1 + m["vat"] / 100)), tol=2)
    check_true(scn, f"{label}: валюта в тексте", f"Цены указаны в {cur} без НДС" in text)
    check_true(scn, f"{label}: контактное лицо — имя, не идентификатор", not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", text))
    return rows


# ---------- Excel расчёта ----------

def check_excel(scn: str, did: str, vid: str, m: dict):
    """D-125: выгрузка расчёта (последняя версия) — те же цифры, что на сервере и на экране: цена, курсовая разница, прибыль, НДС."""
    from openpyxl import load_workbook
    vers = api(CD, "GET", f"/api/deals/{did}/versions")
    vers = vers.get("versions", vers) if isinstance(vers, dict) else vers
    ver = max((v for v in vers if v.get("excel_path")), key=lambda v: v.get("version_no") or 0, default=None)
    check_true(scn, "Excel: файл расчёта есть", ver is not None)
    if not ver:
        return
    wb = load_workbook(io.BytesIO(api(CD, "GET", "/api/files/" + urllib.parse.quote(ver["excel_path"]), raw=True)))
    calc = api(CD, "GET", f"/api/deals/{did}/calc")
    base = calc["result"]["variants"][vid]["base"]
    # столбцы сценариев: пессимистичный, базовый, оптимистичный — берём «Базовый»
    rows = {str(r[0].value): r[2].value for r in wb["Сводка"].iter_rows(min_col=1, max_col=4) if r[0].value}
    def get(prefix):
        return next((v for k, v in rows.items() if k.startswith(prefix)), None)
    check(scn, "Excel: цена продажи без НДС", get("Цена продажи без НДС"), base["price_rub"], tol=1)
    check(scn, "Excel: курсовая разница по оплатам клиента", get("Курсовая разница по оплатам клиента") or 0, base.get("client_fx_diff_rub") or 0, tol=1)
    check(scn, "Excel: прибыль с учётом стоимости денег", get("Прибыль с учётом стоимости денег"), base["profit_rub"], tol=1)
    check(scn, "Excel: цена + курсовая разница − себестоимость = прибыль",
          (get("Цена продажи без НДС") or 0) + (get("Курсовая разница по оплатам клиента") or 0) - base["cost_total_rub"], base["profit_rub"], tol=2)
    ws = wb["Платежи и денежный поток"]
    vat_rows, on = [], False
    for r in ws.iter_rows(min_col=1, max_col=3):
        t = str(r[0].value or "")
        if t.endswith("НДС в денежном потоке (базовый сценарий)"):
            on = True; continue
        if on and t.startswith("Остаток после расчётов"):
            break
        if on and isinstance(r[2].value, (int, float)):
            vat_rows.append(r[2].value)
    check_true(scn, "Excel: блок «НДС в денежном потоке»", bool(vat_rows))
    check(scn, "Excel: НДС за сделку сходится в 0", sum(vat_rows), 0, tol=2)


# ---------- экран ----------

async def screen(browser, did: str) -> dict:
    """Панель решения и последняя строка истории КП на «Расчёте и утверждении» глазами КД."""
    ctx = await browser.new_context(viewport={"width": 1440, "height": 1000}, extra_http_headers={"X-Dev-User": CD})
    pg = await ctx.new_page()
    errs: list[str] = []
    pg.on("pageerror", lambda e: errs.append(str(e)[:160]))
    await pg.goto(f"{FE}/deals/{did}?tab=pricing", wait_until="networkidle")
    await pg.wait_for_selector(".dp .dp-k", timeout=20000)
    out: dict = {"errors": errs}
    out["margin"] = await pg.locator(".dp .dp-m b").text_content()
    for k in await pg.locator(".dp .dp-k").all():
        label = (await k.locator("span").first.text_content() or "").strip()
        out[label] = await k.locator("b").text_content()
    # история КП (если есть): последняя строка — цена за 1 шт. и итого
    await pg.get_by_role("button", name="КП и история").click()
    await pg.wait_for_load_state("networkidle")
    more = pg.get_by_role("button", name=re.compile(r"^показать редакции"))
    if await more.count():
        await more.first.click()
    rows = pg.locator("table:has(th:text-is('Автор')) tbody tr")
    if await rows.count():
        last = rows.nth(await rows.count() - 1)
        tds = last.locator("td")
        out["kp_unit"] = await tds.nth(3).text_content()
        out["kp_total"] = await tds.nth(4).text_content()
    await ctx.close()
    return out


def check_screen(scn: str, s: dict, m: dict, with_kp: bool = True):
    many = m["units"] > 1
    cur = m["cur"]
    check_true(scn, "экран без ошибок страницы", not s["errors"], "; ".join(s["errors"]))
    price = s.get("Цена за 1 шт. без НДС" if many else "Цена без НДС", "")
    check_true(scn, f"экран: цена в {cur}", cur in price or (cur == "RUB" and "₽" in price), price)
    check(scn, "экран: цена" + (" за 1 шт." if many else ""), num(price), round(m["unit"]))
    check(scn, "экран: себестоимость" + (" 1 машины" if many else ""), num(s.get("Себестоимость 1 машины" if many else "Себестоимость", "")), round(m["cost_unit"]))
    check(scn, "экран: прибыль" + (" с 1 машины" if many else ""), num(s.get("Прибыль с 1 машины" if many else "Прибыль", "")), round(m["profit_unit"]))
    check(scn, "экран: маржа, %", num(s["margin"]), m["margin"], tol=0.01)
    if with_kp:
        check_true(scn, "экран: строка в истории КП", "kp_unit" in s)
        if "kp_unit" in s:
            check(scn, "экран, история КП: за 1 шт.", num(s["kp_unit"]), round(m["unit"]))
            check(scn, "экран, история КП: итого", num(s["kp_total"]), round(m["price"]))


# ---------- сценарии ----------

async def s1_single(browser):
    """1. Одна модель × 2, CNY, маржа 20 %"""
    scn = "1. Одна модель × 2, CNY, маржа 20 %"
    did, var, pos = new_deal("E2E: одна модель", [{"model": "E2E-1", "qty": 2, "unit_price": 1500000}])
    logistics(did, pos)
    api(CD, "POST", f"/api/deals/{did}/price", {"variant_id": var["id"], "mode": "margin", "value": 20})
    check_true(scn, "утверждено", terms_and_submit(did, pos) == "approved")
    m = money(did, var["id"])
    check(scn, "сервер: цена в валюте × курс = цена в рублях", m["price"] * m["fx"], m["price_rub"])
    # D-121: НДС в денежном потоке — от клиента 22 % от цены, в бюджет = НДС с цены − ввозной, каждый платёж 28-го числа
    vat = api(CD, "GET", f"/api/deals/{did}/calc")["result"]["variants"][var["id"]]["base"]["vat"]
    imp = sum(x["amount_rub"] for x in vat["import"])
    fxd = api(CD, "GET", f"/api/deals/{did}/calc")["result"]["variants"][var["id"]]["base"].get("client_fx_diff_rub") or 0
    # D-123: оплаты клиента в рублях — по прогнозу курса на даты оплат, НДС — 22 % от них
    check(scn, "НДС от клиента = 22 % от оплат (₽ по прогнозу курса)", vat["from_client_rub"], (m["price_rub"] + fxd) * m["vat"] / 100, tol=2)
    check(scn, "НДС в бюджет = НДС с цены − ввозной", sum(q["amount_rub"] for q in vat["budget"]), vat["from_client_rub"] - imp, tol=2)
    check_true(scn, "платежи НДС — 28-го числа или следующий рабочий день", all(d[8:10] >= "28" or d[8:10] <= "03" for q in vat["budget"] for d in q["dates"]))
    check_kp(scn, did, m)
    check_excel(scn, did, var["id"], m)
    check_screen(scn, await screen(browser, did), m)


async def s2_two_models(browser):
    """2. Две модели в одном предложении (3 + 1 шт.)"""
    scn = "2. Две модели в одном предложении (3 + 1 шт.)"
    did, var, pos = new_deal("E2E: две модели", [{"model": "E2E-A", "qty": 3, "unit_price": 1200000}, {"model": "E2E-B", "qty": 1, "unit_price": 2600000}])
    logistics(did, pos)
    api(CD, "POST", f"/api/deals/{did}/price", {"variant_id": var["id"], "mode": "margin", "value": 18})
    check_true(scn, "утверждено", terms_and_submit(did, pos) == "approved")
    m = money(did, var["id"])
    check(scn, "цена за комплект (разные модели — без «за 1 шт.»)", m["units"], 1, tol=0)
    check(scn, "сервер: цена в валюте × курс = цена в рублях", m["price"] * m["fx"], m["price_rub"])
    check_kp(scn, did, m, qty_text="E2E-A — 3 шт.; E2E-B — 1 шт.")
    check_screen(scn, await screen(browser, did), m)


async def s3_usd_rub_input(browser):
    """3. Сделка в USD, цена введена в рублях"""
    scn = "3. Сделка в USD, цена введена в рублях"
    did, var, pos = new_deal("E2E: USD", [{"model": "E2E-U", "qty": 2, "unit_price": 1500000}])
    logistics(did, pos)
    api(CD, "PUT", f"/api/deals/{did}/deal-currency", {"currency": "USD"})
    api(CD, "POST", f"/api/deals/{did}/price", {"variant_id": var["id"], "mode": "amount", "value": 40000000, "currency": "RUB"})   # 40 млн ₽ за 1 шт.
    check_true(scn, "утверждено", terms_and_submit(did, pos) == "approved")
    m = money(did, var["id"])
    check_true(scn, "валюта сделки USD", m["cur"] == "USD", m["cur"])
    check(scn, "сервер: введённые 40 000 000 ₽ за 1 шт. × 2", m["price_rub"], 80000000)
    check(scn, "сервер: цена в USD = рубли / курс", m["price"], m["price_rub"] / m["fx"])
    check_kp(scn, did, m)
    check_excel(scn, did, var["id"], m)
    check_screen(scn, await screen(browser, did), m)


async def s4_rework_qty(browser):
    """4. Доработка: количество 2 → 3"""
    scn = "4. Доработка: количество 2 → 3, повторное утверждение"
    did, var, pos = new_deal("E2E: доработка", [{"model": "E2E-R", "qty": 2, "unit_price": 1500000}])
    logistics(did, pos)
    api(CD, "POST", f"/api/deals/{did}/price", {"variant_id": var["id"], "mode": "margin", "value": 20})
    m1 = money(did, var["id"])
    reasons = api(CD, "GET", "/api/rework-reasons")
    code = (reasons[0] if isinstance(reasons, list) else reasons.get("items", [{}])[0]).get("code", "")
    api(CD, "POST", f"/api/deals/{did}/rework", {"block": "manager", "reason_code": code, "comment": "клиент просит 3 машины"})
    api(MGR, "PATCH", f"/api/deals/{did}/positions/{pos[0]['id']}", {"qty": 3})
    logistics(did, pos, again=True)
    api(CD, "POST", f"/api/deals/{did}/price", {"variant_id": var["id"], "mode": "margin", "value": 20})
    check_true(scn, "утверждено после доработки", terms_and_submit(did, pos) == "approved")
    m = money(did, var["id"])
    check(scn, "единиц после доработки", m["units"], 3, tol=0)
    check_true(scn, "итог вырос вместе с количеством", m["price"] > m1["price"] * 1.3, f"{m1['price']:.0f} → {m['price']:.0f}")
    check_kp(scn, did, m)
    check_screen(scn, await screen(browser, did), m)


async def s5_bargain_contract(browser):
    """5. Торги −3 % → выигрыш → договор"""
    scn = "5. Торги −3 % → выигрыш → платежи клиента в договоре = КП с НДС"
    did, var, pos = new_deal("E2E: торги и договор", [{"model": "E2E-T", "qty": 2, "unit_price": 1500000}])
    logistics(did, pos)
    api(CD, "POST", f"/api/deals/{did}/price", {"variant_id": var["id"], "mode": "margin", "value": 20})
    check_true(scn, "утверждено", terms_and_submit(did, pos) == "approved")
    m0 = money(did, var["id"])
    step = api(CD, "POST", f"/api/deals/{did}/negotiation/steps", {"prices": {var["id"]: {"mode": "pct", "value": 3}}, "reason": "client", "comment": "e2e"})
    m = money(did, var["id"])
    check(scn, "сервер: цена после торгов = −3 %", m["price_rub"], m0["price_rub"] * 0.97, tol=2)
    check(scn, "сервер: цена шага торгов", step["price_rub"], m["price_rub"], tol=2)
    revs = api(CD, "GET", f"/api/deals/{did}/kp/revisions")["rows"]
    last = revs[-1]
    check(scn, "история КП: последняя редакция = цена торгов, за 1 шт.", last["unit_price"], m["unit"])
    check_kp(scn, did, m, rev_id=last["id"], label="КП редакции торгов")
    check_kp(scn, did, m, label="КП текущее")
    check_screen(scn, await screen(browser, did), m)
    api(MGR, "POST", f"/api/deals/{did}/kp-sent", {"recipients": ["e2e@example.com"]})
    check_true(scn, "выигрыш → договор", api(CD, "POST", f"/api/deals/{did}/outcome", {"outcome": "won", "reason": "e2e"})["status"] == "contract")
    st = api(CD, "GET", f"/api/deals/{did}/execution/stages")
    pay_in = [s for s in st["stages"] if s["code"] in ("client_advance", "client_final")]
    check_true(scn, "в договоре есть аванс и окончательный расчёт клиента", len(pay_in) == 2, ", ".join(s["code"] for s in pay_in))
    if pay_in:
        # D-120: платежи клиента — в валюте договора с НДС; справочно — рубли без НДС, как в расчёте
        check_true(scn, "платежи клиента — в валюте договора, с НДС", all(s["currency"] == m["cur"] and s.get("with_vat") for s in pay_in),
                   json.dumps([(s["code"], s["currency"], s.get("with_vat")) for s in pay_in], ensure_ascii=False))
        check(scn, f"платежи клиента = цена после торгов с НДС, {m['cur']}", sum(s["amount"] for s in pay_in), m["price"] * (1 + m["vat"] / 100), tol=2)
        for s_ in pay_in:   # справочно: ₽ без НДС = сумма без НДС × прогноз курса на плановую дату оплаты
            check(scn, f"{s_['code']}: справочно ₽ без НДС по курсу на дату оплаты", s_["amount_rub_net"], s_["amount"] / (1 + m["vat"] / 100) * s_["ref_rate"], tol=s_["amount"] * 0.0001 + 2)
        rows, _ = kp_rows(did)
        gross_key = next((k for k in rows if k.startswith(f"Итого, {m['cur']} с НДС")), "")
        check(scn, "платежи клиента = «Итого с НДС» в КП", sum(s["amount"] for s in pay_in), num(rows.get(gross_key, "")), tol=2)


def upload(user, path, name, data: bytes, kind: str):
    bd = "e2eboundary7d1f"
    body = (f'--{bd}\r\nContent-Disposition: form-data; name="kind"\r\n\r\n{kind}\r\n--{bd}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{name}"\r\nContent-Type: application/pdf\r\n\r\n').encode() + data + f"\r\n--{bd}--\r\n".encode()
    req = urllib.request.Request(BE + path, method="POST", data=body, headers={"X-Dev-User": user, "Content-Type": f"multipart/form-data; boundary={bd}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"POST {path} → {e.code}: {e.read()[:400]!r}")


async def s6_office(browser):
    """6. Канцелярия: входящее → резолюция → ответ → согласование → подпись → отправка → закрыто"""
    scn = "6. Канцелярия: полный путь письма"
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
    meta = api(CLERK, "GET", "/api/office/meta")
    ru = next(e for e in meta["entities"] if e["code"] == "ru")
    uid = lambda pre: next(u["id"] for u in meta["users"] if u["name"].startswith(pre))   # noqa: E731
    did, _, _ = new_deal("E2E: письмо по сделке", [{"model": "E2E-L", "qty": 1, "unit_price": 1000000}])
    d = api(CLERK, "POST", "/api/office/docs", {"entity_id": ru["id"], "kind": "in", "letter_type": "request", "channel": "post_reg", "tracking": "80000000000001",
                                                 "subject": "E2E: запрос сроков поставки", "counterparty": "АО «E2E»", "deal_ids": [did], "deal_section": "sale"})
    check_true(scn, "номер входящего присвоен при регистрации", bool(d["number"]) and "-ВХ-" in d["number"], d["number"])
    check_true(scn, "без скана — не на рассмотрение", d["status"] == "registered" and not d["has_scan"])
    upload(CLERK, f"/api/office/docs/{d['id']}/files", "e2e_вх.pdf", pdf, "scan")
    d = api(CLERK, "POST", f"/api/office/docs/{d['id']}/review", {"reviewer_id": uid("Добров")})
    check_true(scn, "на рассмотрении у ЗГД", d["status"] == "review")
    d = api(GD, "POST", f"/api/office/docs/{d['id']}/resolution", {"kind": "execute", "executor_id": uid("Бочаров"), "text": "e2e: ответить"})
    check_true(scn, "резолюция → исполнитель", d["status"] == "assigned" and d["executor_id"] == uid("Бочаров"))
    api(MGR, "POST", f"/api/office/docs/{d['id']}/action", {"action": "take"})
    o = api(MGR, "POST", "/api/office/docs", {"entity_id": ru["id"], "kind": "out", "subject": "E2E: ответ о сроках", "reply_to_id": d["id"],
                                               "body": "Сообщаем: отгрузка 05.12.2026."})
    check_true(scn, "ответ унаследовал адресата и сделку", o["counterparty"] == "АО «E2E»" and o["deal_ids"] == [did])
    routes = [r for r in meta["routes"] if r["is_active"] and (not r["kinds"] or "out" in r["kinds"]) and (not r["entity_id"] or r["entity_id"] == ru["id"])]
    req = next((r for r in routes if r["required"]), None)
    o = api(MGR, "POST", f"/api/office/docs/{o['id']}/approval", {"route_id": req["id"]} if req else {"mode": "seq", "steps": [{"role": "lawyer"}]})
    check_true(scn, "черновик на согласовании", o["status"] == "approval")
    for _ in range(10):   # каждый согласующий в свою очередь
        o = api(CLERK, "GET", f"/api/office/docs/{o['id']}")
        if o["status"] != "approval":
            break
        st = next(s_ for s_ in o["approval"]["steps"] if s_["status"] == "pending")
        who = ROLE_USER.get(st.get("role") or "", GD)
        if st.get("user_id"):
            who = next((em for em in ROLE_USER.values() if api(em, "GET", "/api/auth/me")["id"] == st["user_id"]), GD)
        api(who, "POST", f"/api/office/docs/{o['id']}/approval/decide", {"decision": "approve", "comment": "e2e"})
    check_true(scn, "согласовано", o["status"] == "approved", o["status"])
    sg = next((x for x in meta["signers"] if x["entity_id"] == ru["id"] and x["valid_today"]), None)
    if sg is None:
        sg = next(x for x in api(GD, "POST", "/api/office/signers", {"entity_id": ru["id"], "full_name": "E2E Подписант", "position": "ГД"})["signers"]
                  if x["full_name"] == "E2E Подписант")
    o = api(CLERK, "POST", f"/api/office/docs/{o['id']}/sign", {"signer_id": sg["id"], "method": "manual"})
    check_true(scn, "подписано, номер исходящего", o["status"] == "signed" and "-ИСХ-" in (o["number"] or ""), o["number"] or "")
    upload(CLERK, f"/api/office/docs/{o['id']}/files", "e2e_исх_подписано.pdf", pdf, "scan")
    o = api(CLERK, "POST", f"/api/office/docs/{o['id']}/action", {"action": "sent", "channel": "email"})
    check_true(scn, "отправлено", o["status"] == "sent")
    d = api(CLERK, "GET", f"/api/office/docs/{d['id']}")
    check_true(scn, "входящее закрыто ответом", d["status"] == "closed", d["status"])
    feed = api(MGR, "GET", f"/api/deals/{did}/feed?messages=true")
    nums = " ".join(i.get("subject", "") + " " + i.get("body", "") for i in feed["items"])
    check_true(scn, "оба письма — в ленте сделки", d["number"] in nums and o["number"] in nums)
    page = await browser.new_page(extra_http_headers={"X-Dev-User": CLERK})
    await page.goto(f"{FE}/office?t=out&doc={o['id']}")
    await page.wait_for_selector(".office-card", timeout=20000)
    txt = await page.inner_text(".office-card")
    check_true(scn, "экран: карточка исходящего — номер и «отправлено»", o["number"] in txt and "тправлен" in txt)
    await page.close()


SCENARIOS = [s1_single, s2_two_models, s3_usd_rub_input, s4_rework_qty, s5_bargain_contract, s6_office]


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"]) if CHROME else await p.chromium.launch()
        for s in SCENARIOS:
            before = len(fails)
            try:
                await s(browser)
            except SystemExit as e:          # ошибка API — сценарий не дошёл до конца
                fails.append(f"{s.__name__}: {e}")
            except Exception as e:           # noqa: BLE001 — сценарий сломался: показать и идти дальше
                fails.append(f"{s.__name__}: {type(e).__name__}: {e}")
            print(("OK  " if len(fails) == before else "FAIL") + " " + (s.__doc__ or s.__name__))
        await browser.close()
    print(f"сценариев: {len(SCENARIOS)}, проверок: {checks}, расхождений: {len(fails)}")
    for f in fails:
        print("FAIL", f)
    sys.exit(1 if fails else 0)


asyncio.run(main())
