"""Сквозная проверка входа в браузере (Playwright + Chrome) на локальном стенде в режиме входа по паролю.

Запуск (backend с AUTH_PROVIDER=password на :8001, frontend: `VITE_API_URL=http://localhost:8001 npx vite --port 5174`):
    python3 scripts/e2e/login_flow.py http://localhost:5174 http://localhost:8001 <путь к chrome>
Сценарии: прямая ссылка без входа → страница входа → возврат по `next`; вход с кодом из приложения; ошибка пароля;
подтверждение (step-up) паролем перед опасным действием; выход. Создаёт своих синтетических сотрудников, рабочие данные не трогает.
"""
import asyncio, json, os, sys, time, uuid, urllib.request, http.cookiejar
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
from playwright.async_api import async_playwright

FE, BE, CHROME = sys.argv[1], sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else None)
PW = "Granite-e2e-2026-river"
res = []
def ok(name, cond, extra=""): res.append(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))

def make_user(roles):
    from datetime import datetime, timezone
    from app.core.auth import hash_password
    from app.db import SessionLocal
    from app.models import User
    email = f"e2e.{uuid.uuid4().hex[:8]}@example.com"
    with SessionLocal() as db:
        u = User(email=email, external_id=email, display_name=f"Проверкин Е2Е {email[4:8]}", roles=roles, password_hash=hash_password(PW), password_set_at=datetime.now(timezone.utc))
        db.add(u); db.commit()
    return email

def enable_mfa(email):
    from app.services import mfa as mfa_svc
    cj = http.cookiejar.CookieJar(); op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    def post(path, body):
        r = op.open(urllib.request.Request(BE + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Origin": BE}, method="POST")); return json.load(r)
    post("/api/auth/password-login", {"login": email, "password": PW, "remember": False})
    secret = post("/api/auth/mfa/setup", {"password": PW})["secret"]
    post("/api/auth/mfa/confirm", {"code": mfa_svc.code_now(secret)})
    return secret

async def login_ui(pg, email, password):
    await pg.fill("input[name=username]", email); await pg.fill("input[name=password]", password)
    await pg.click("button[type=submit]"); await pg.wait_for_timeout(900)

async def main():
    from app.services import mfa as mfa_svc
    mgr = make_user(["manager"]); adm = make_user(["admin"]); mfa_user = make_user(["cd"])
    secret = enable_mfa(mfa_user)
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        errs = []
        # 1. прямая ссылка без входа → вход → возврат на ту же страницу
        c = await b.new_context(viewport={"width": 1440, "height": 900}); pg = await c.new_page()
        pg.on("pageerror", lambda e: errs.append(str(e)[:120]))
        await pg.goto(FE + "/legal?tab=claims", wait_until="networkidle"); await pg.wait_for_timeout(500)
        ok("без входа → страница входа с next", "/login" in pg.url and "next=" in pg.url, pg.url)
        await login_ui(pg, mgr, "Wrong-password-123")
        body = await pg.locator("body").inner_text()
        ok("неверный пароль → понятная ошибка, остаёмся на входе", "/login" in pg.url and "Неверный логин или пароль" in body, pg.url)
        await login_ui(pg, mgr, PW); await pg.wait_for_load_state("networkidle"); await pg.wait_for_timeout(600)
        ok("после входа — возврат на исходный адрес", pg.url.endswith("/legal?tab=claims"), pg.url)
        ok("путь и раздел на месте", await pg.locator(".crumbs").count() == 1)
        await pg.goto(FE + "/", wait_until="networkidle"); await pg.wait_for_timeout(500)
        ok("рабочее место: входы «матрёшки» и переключатель вида", await pg.locator("[data-entry]").count() >= 2 and await pg.locator(".seg >> text=Прежний вид").count() == 1)
        # выход
        await pg.locator("header .hm-user button").first.click(); await pg.wait_for_timeout(300)
        btn = pg.locator("[role=menuitem]", has_text="Выйти").first
        if await btn.count():
            await btn.click(); await pg.wait_for_timeout(900)
            await pg.goto(FE + "/deals", wait_until="networkidle"); await pg.wait_for_timeout(400)
            ok("выход → снова страница входа", "/login" in pg.url, pg.url)
        else: ok("кнопка «Выйти» найдена", False)
        await c.close()
        # 2. вход с кодом из приложения
        c = await b.new_context(viewport={"width": 1440, "height": 900}); pg = await c.new_page()
        pg.on("pageerror", lambda e: errs.append(str(e)[:120]))
        await pg.goto(FE + "/deals", wait_until="networkidle"); await pg.wait_for_timeout(400)
        await login_ui(pg, mfa_user, PW)
        ok("пароль верный → запрос кода из приложения", await pg.locator("input[name=code]").count() == 1)
        await pg.fill("input[name=code]", "000000"); await pg.click("button[type=submit]"); await pg.wait_for_timeout(700)
        ok("неверный код → остаёмся на вводе кода", await pg.locator("input[name=code]").count() == 1 and "/login" in pg.url)
        await pg.fill("input[name=code]", mfa_svc.code_now(secret, time.time() + 30)); await pg.click("button[type=submit]")
        await pg.wait_for_load_state("networkidle"); await pg.wait_for_timeout(800)
        ok("верный код → возврат на /deals", pg.url.endswith("/deals"), pg.url)
        await c.close()
        # 3. подтверждение паролем (step-up) перед опасным действием
        c = await b.new_context(viewport={"width": 1440, "height": 900}); pg = await c.new_page()
        pg.on("pageerror", lambda e: errs.append(str(e)[:120]))
        await pg.goto(FE + "/settings?s=security_admin", wait_until="networkidle"); await pg.wait_for_timeout(400)
        await login_ui(pg, adm, PW); await pg.wait_for_load_state("networkidle"); await pg.wait_for_timeout(800)
        ok("админ: возврат в «Безопасность сотрудников»", "s=security_admin" in pg.url, pg.url)
        row = pg.locator("table tbody tr", has_text=mfa_user).first   # у него активный вход из сценария 2
        if await row.count():
            await row.locator("button", has_text="Открыть").click(); await pg.wait_for_timeout(700)
            b2 = pg.locator("button", has_text="Завершить все входы").first
            if await b2.count():
                await b2.click(); await pg.wait_for_timeout(700)
                dlg = pg.locator("form:has(button:has-text('Подтвердить')) input[type=password]")
                ok("опасное действие → окно подтверждения паролем", await dlg.count() >= 1)
                if await dlg.count():
                    await dlg.first.fill(PW); await pg.locator("form button", has_text="Подтвердить").first.click(); await pg.wait_for_timeout(1200)
                    done = await pg.locator("text=Завершено входов").count()
                    ok("после подтверждения окно закрыто, действие выполнено", await dlg.count() == 0 and done >= 1)
            else: ok("кнопка «Завершить сеансы» у сотрудника (сеансы есть после его входа)", False, "кнопки нет")
        else: ok("сотрудник найден в списке безопасности", False)
        await c.close(); await b.close()
    print("\n".join(res)); print("ошибки на страницах:", errs or "нет")
    sys.exit(1 if any(r.startswith("FAIL") for r in res) else 0)

asyncio.run(main())
