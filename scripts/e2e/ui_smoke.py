"""Проверка интерфейса по ролям (Playwright + Chrome): каждый раздел меню и каждая вкладка сделки открываются
без ошибок страницы, без ответов сервера 5xx и без «нет доступа» (403) там, где роль видит раздел.

Запуск (backend на :8000 с AUTH_PROVIDER=dev и демо-данными `python -m scripts.seed`, frontend `npx vite --port 5173`):
    python3 scripts/e2e/ui_smoke.py http://localhost:5173 http://localhost:8000 [путь к chrome]
Создаёт свою синтетическую сделку и доводит её до утверждения; рабочие данные не трогает. Код выхода 1 — есть ошибки.
"""
import asyncio
import json
import sys
import urllib.error
import urllib.request

from playwright.async_api import async_playwright

FE, BE = sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/")
CHROME = sys.argv[3] if len(sys.argv) > 3 else None
ROLES = [("manager@example.com", "менеджер"), ("logist@example.com", "логист"), ("service@example.com", "сервис"),
         ("cd@example.com", "КД"), ("fd@example.com", "ФД"), ("evgeniy@dobrov.pro", "ЗГД"), ("admin@example.com", "администратор"),
         ("observer@example.com", "наблюдатель"), ("lawyer@example.com", "юрист")]
MGR, LOG, CD = "manager@example.com", "logist@example.com", "cd@example.com"


def api(user, method, path, body=None):
    req = urllib.request.Request(BE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Dev-User": user, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} → {e.code}: {e.read()[:300]!r}")


def make_deal() -> str:
    """Синтетическая сделка до «Утверждено»: так у ролей открываются расчёт, торги, документы."""
    client = api(MGR, "GET", "/api/refs/clients")[0]
    factory = api(MGR, "GET", "/api/refs/factories")[0]
    did = api(MGR, "POST", "/api/deals", {"client_id": client["id"], "site": "Проверка интерфейса"})["id"]
    pos = api(MGR, "POST", f"/api/deals/{did}/positions", {"factory_id": factory["id"], "model": "UI-1", "machine_type": "ПДМ", "qty": 2,
                                                          "unit_price": 1500000, "currency": "CNY"})
    var = api(MGR, "GET", f"/api/deals/{did}")["offer"]
    api(MGR, "POST", f"/api/deals/{did}/send-to-calc")
    items = {"door_to_door": {"amount": 100000, "currency": "CNY"}, "broker_rub": 100000, "comments": {"door_to_door": "x", "broker_rub": "x"}}
    api(LOG, "POST", f"/api/deals/{did}/logistics/batches", {"items": items, "position_ids": [pos["id"]]})
    api(LOG, "PUT", f"/api/deals/{did}/logistics/positions/{pos['id']}", {"hs_code": "8429519000", "util_applies": False})
    api(LOG, "POST", f"/api/deals/{did}/logistics/complete")
    api(CD, "POST", f"/api/deals/{did}/price", {"variant_id": var["id"], "mode": "margin", "value": 25})
    schemes = api(CD, "GET", "/api/refs/payment-schemes")
    calc = api(CD, "GET", f"/api/deals/{did}/calc")
    api(CD, "POST", f"/api/deals/{did}/payment-terms", {"client_payments": schemes[0]["lines"],
                                                        "factory_payments": {pos["id"]: calc["payment_terms"]["default_factory"]}})
    st = api(CD, "POST", f"/api/deals/{did}/submit")["status"]
    print(f"сделка для проверки: {did} ({st})")
    return did


fails: list[str] = []
visited = 0


async def check_role(browser, user, title, mode, did):
    global visited
    ctx = await browser.new_context(viewport={"width": 1440, "height": 900}, extra_http_headers={"X-Dev-User": user})
    await ctx.add_init_script(f"try{{localStorage.setItem('gsho.uiMode','{mode}')}}catch(e){{}}")
    pg = await ctx.new_page()
    errs: list[str] = []
    pg.on("pageerror", lambda e: errs.append("ошибка страницы: " + str(e)[:160]))
    pg.on("response", lambda r: errs.append(f"{r.status} {r.request.method} {r.url.replace(FE, '')[:90]}")
          if "/api/" in r.url and (r.status >= 500 or r.status == 403) else None)

    async def visit(label, url=None, click=None):
        global visited
        errs.clear()
        if url:
            await pg.goto(FE + url, wait_until="networkidle")
        if click:
            await click.click()
            await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(300)
        visited += 1
        if errs:
            fails.append(f"{title} / {mode} / {label}: " + "; ".join(sorted(set(errs))[:4]))

    await visit("рабочее место", "/")
    if await pg.locator(".nav-more button").count():
        await pg.click(".nav-more button")
        await pg.wait_for_timeout(150)
    hrefs = sorted({h for h in [await a.get_attribute("href") for a in await pg.locator("header.top nav a").all()] if h and h.startswith("/")})
    await pg.keyboard.press("Escape")
    for h in hrefs:
        await visit(f"раздел {h}", h)
    # карточка сделки и все её вкладки, если роль видит сделку
    deals = json.loads(await (await ctx.request.get(BE + "/api/deals")).text()) if (await ctx.request.get(BE + "/api/deals")).ok else []
    if any(d.get("id") == did for d in deals):
        await visit("сделка", f"/deals/{did}")
        n = await pg.locator(".tabs button").count()
        for i in range(n):
            b = pg.locator(".tabs button").nth(i)
            await visit(f"сделка, вкладка «{(await b.inner_text()).strip()}»", click=b)
    await ctx.close()


async def main():
    did = make_deal()
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"]) if CHROME else await p.chromium.launch()
        for user, title in ROLES:
            for mode in ("new", "classic"):
                await check_role(browser, user, title, mode, did)
        await browser.close()
    print(f"проверено экранов: {visited}, ошибок: {len(fails)}")
    for f in fails:
        print("FAIL", f)
    sys.exit(1 if fails else 0)


asyncio.run(main())
