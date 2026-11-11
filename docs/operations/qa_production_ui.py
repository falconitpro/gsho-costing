"""Браузерная проверка рабочего режима на пустой базе (после docs/operations/qa_production_init.py — те же шаги, база сохранена).
Backend :8000 с APP_ENV=prod, AUTH_PROVIDER=password; vite :5173. Аргумент — временный пароль первого пользователя.

    python3 docs/operations/qa_production_ui.py <временный пароль>
"""
import sys

from playwright.sync_api import expect, sync_playwright

U, SHOTS = "http://localhost:5173", "docs/operations/screens"     # localhost: Chrome принимает cookie Secure без HTTPS
TEMP, NEW = sys.argv[1], "Own-secret-river-2026"
issues = []

with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    for w, h in ((1440, 900), (390, 844)):
        ctx = b.new_context(viewport={"width": w, "height": h}, locale="ru-RU", timezone_id="Europe/Moscow")
        p = ctx.new_page()
        p.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and issues.append(m.text[:160]))
        p.on("pageerror", lambda e: issues.append(str(e)))
        p.on("response", lambda r: r.status >= 500 and "/api/" in r.url and issues.append(f"{r.status} {r.url}"))
        p.goto(U + "/")
        p.wait_for_url("**/login**")
        expect(p.get_by_text("Войти через Microsoft 365")).to_have_count(0)
        p.screenshot(path=f"{SHOTS}/p01_login_{w}.png")
        first = w == 1440
        p.locator("input[type=email], input[name=login], input[autocomplete=username]").first.fill("owner@example.test")
        p.locator("input[type=password]").first.fill(TEMP if first else NEW)
        p.get_by_role("button", name="Войти").click()
        if first:
            expect(p.get_by_text("Смена временного пароля")).to_be_visible()
            p.screenshot(path=f"{SHOTS}/p02_must_change_{w}.png")
            fields = p.locator("input[type=password]")
            n = fields.count()
            if n >= 3:
                fields.nth(0).fill(TEMP)
            fields.nth(n - 2).fill(NEW)
            fields.nth(n - 1).fill(NEW)
            p.get_by_role("button", name="Сменить пароль").click()
        expect(p.get_by_role("button", name="Помощник", exact=True)).to_be_visible(timeout=15000)
        expect(p.get_by_text("Войти как")).to_have_count(0)
        p.wait_for_load_state("networkidle")
        if p.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"):
            issues.append(f"горизонтальная прокрутка рабочего места на {w}")
        p.screenshot(path=f"{SHOTS}/p03_workplace_empty_{w}.png")
        for path in ("/deals", "/catalog", "/counterparties", "/legal", "/settings?s=users", "/settings?s=security", "/settings?s=backup", "/support"):
            p.goto(U + path)
            p.wait_for_load_state("networkidle")
            if p.locator("text=Что-то пошло не так").count() or p.locator("text=Ошибка 500").count():
                issues.append(f"ошибка на {path} ({w})")
        p.screenshot(path=f"{SHOTS}/p04_support_empty_{w}.png")
        ctx.close()
    b.close()
print("ISSUES:", issues or "none")
sys.exit(1 if issues else 0)
