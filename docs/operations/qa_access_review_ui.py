"""Браузерная проверка «Пересмотр прав» (этап 2б). Синтетическая БД; backend :8000, vite :5173.
    python3 docs/operations/qa_access_review_ui.py <email наблюдателя> <пароль ЗГД>"""
import sys
from playwright.sync_api import expect, sync_playwright

U, SHOTS = "http://127.0.0.1:5173", "docs/operations/screens"
TARGET, GD_PW = sys.argv[1], sys.argv[2]
GD = "evgeniy@dobrov.pro"
issues = []

with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, locale="ru-RU", timezone_id="Europe/Moscow")
    assert ctx.request.get(U + "/api/auth/me", headers={"X-Dev-User": GD}).ok
    p = ctx.new_page()
    p.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and issues.append(m.text[:160]))
    p.on("pageerror", lambda e: issues.append(str(e)))
    def resp(r):
        if r.status >= 400 and "/api/" in r.url:
            t = ""
            try: t = r.text()[:100]
            except Exception: pass
            if "step_up_required" in t or "не изменились" in t: return
            issues.append(f"{r.status} {r.url} {t}")
    p.on("response", resp)
    p.goto(U + "/settings?s=access_review")
    p.get_by_role("heading", name="Пересмотр прав").wait_for()
    p.get_by_role("button", name="Новая проверка").click()
    dlg = p.get_by_role("dialog", name="Новый пересмотр прав")
    dlg.get_by_label("Наблюдатель").check()
    p.screenshot(path=f"{SHOTS}/r01_new_1440.png")
    dlg.get_by_role("button", name="Создать").click()
    p.get_by_text("Проверено 0 из").wait_for()
    row = p.locator("tr", has=p.get_by_text(TARGET))
    row.get_by_role("button", name="▸").first.click() if row.get_by_role("button", name="▸").count() else row.locator(".ar-name").click()
    p.get_by_role("heading", name="Права на начало проверки").first.wait_for()
    # «Изменено» без изменения прав — понятный отказ
    row.get_by_role("button", name="Изменено").click()
    p.get_by_label("Что изменено").fill("Попытка без изменения")
    p.get_by_role("button", name="Сохранить «Изменено»").click()
    p.get_by_text("Права сотрудника не изменились").wait_for()
    # «Отключить» → диалог → подтверждение паролем → решение записано
    row.get_by_role("button", name="Отключить").click()
    od = p.get_by_role("dialog", name="Отключить доступ сотрудника")
    od.get_by_label("Для подтверждения введите").fill(od.locator("b").nth(1).inner_text())
    od.get_by_role("button", name="Отключить доступ").click()
    su = p.get_by_role("dialog", name="Подтвердите действие")
    su.wait_for()                                   # подтверждение сброшено в подготовке — диалог обязан появиться
    su.get_by_label("Ваш пароль").fill(GD_PW)
    su.get_by_role("button", name="Подтвердить").click()
    p.get_by_role("dialog", name="Доступ отключён").get_by_role("button", name="Готово").click()
    p.get_by_label("только нерешённые").uncheck()
    expect(p.locator("tr", has=p.get_by_text(TARGET)).get_by_text("отключён")).to_be_visible()
    # остальные — «Оставить» по одному
    p.get_by_label("только нерешённые").check()
    while p.locator(".ar-table tbody tr", has=p.get_by_role("button", name="Оставить")).count():
        p.locator(".ar-table tbody tr", has=p.get_by_role("button", name="Оставить")).first.get_by_role("button", name="Оставить").click()
        p.wait_for_timeout(300)
    for w in (1440, 768, 390):
        p.set_viewport_size({"width": w, "height": 900}); p.wait_for_timeout(200)
        over = p.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        if over > 1: issues.append(f"прокрутка {over}px @{w}")
        p.screenshot(path=f"{SHOTS}/r02_campaign_{w}.png", full_page=True)
    p.set_viewport_size({"width": 1440, "height": 900})
    p.get_by_role("button", name="Завершить").click()
    p.get_by_role("dialog", name="Завершить пересмотр").get_by_role("button", name="Завершить").click()
    p.get_by_text("завершена").first.wait_for()
    p.screenshot(path=f"{SHOTS}/r03_completed_1440.png", full_page=True)
    b.close()
print("ISSUES:", issues or "none")
