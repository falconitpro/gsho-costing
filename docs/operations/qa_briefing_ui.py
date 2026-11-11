"""Браузерная проверка «Ваши приоритеты» (этап 5). Синтетическая БД; backend :8000, vite :5173.
    python3 docs/operations/qa_briefing_ui.py
Сначала qa_briefing_prep.py (сделка и три задачи менеджера). Проверяется порядок, закрепление, «скрыть»,
возврат из «Отложено и скрыто», переход в источник, ширины 1440/1280/768/390."""
from playwright.sync_api import expect, sync_playwright

U, API, SHOTS = "http://127.0.0.1:5173", "http://127.0.0.1:8000", "docs/operations/screens"
MGR = "manager@example.com"
issues = []

with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, locale="ru-RU", timezone_id="Europe/Moscow")
    ctx.request.get(API + "/api/auth/me", headers={"X-Dev-User": MGR})
    p = ctx.new_page()
    p.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and issues.append(m.text[:160]))
    p.on("pageerror", lambda e: issues.append(str(e)))
    p.on("response", lambda r: r.status >= 500 and "/api/" in r.url and issues.append(f"{r.status} {r.url}"))
    p.goto(U + "/")
    blk = p.locator(".brf")
    blk.wait_for()
    titles = blk.locator(".brf-title")
    expect(titles.first).to_have_text("Отправить КП клиенту «Север»")
    expect(blk.locator(".brf-item").first).to_contain_text("Просрочено на 2 дн.")
    expect(blk).to_contain_text("Сформировано")
    assert "совещан" not in blk.inner_text().lower()
    p.screenshot(path=f"{SHOTS}/b01_workplace_1440.png", full_page=True)
    # закрепить третью (завтра) — внутри своей важности, не выше просроченной
    item = blk.locator(".brf-item", has_text="Согласовать логистику")
    item.get_by_role("button", name="Действия: Согласовать логистику").click()
    item.get_by_role("menuitem", name="Закрепить").click()
    expect(blk.locator(".brf-item", has_text="Согласовать логистику")).to_contain_text("закреплено")
    expect(titles.first).to_have_text("Отправить КП клиенту «Север»")
    # скрыть сегодняшнюю → «Отложено и скрыто: 1» → вернуть
    item = blk.locator(".brf-item", has_text="Проверить RFQ от завода")
    item.get_by_role("button", name="Действия: Проверить RFQ от завода").click()
    item.get_by_role("menuitem", name="Скрыть, пока не изменится").click()
    expect(blk.locator(".brf-item", has_text="Проверить RFQ от завода")).to_have_count(0)
    blk.get_by_role("button", name="Отложено и скрыто: 1").click()
    dlg = p.get_by_role("dialog", name="Отложенные и скрытые")
    expect(dlg).to_contain_text("Проверить RFQ от завода")
    p.screenshot(path=f"{SHOTS}/b02_states_1440.png")
    dlg.get_by_role("button", name="Вернуть", exact=True).click()
    expect(blk.locator(".brf-item", has_text="Проверить RFQ от завода")).to_have_count(1)
    # переход по элементу ведёт в источник
    titles.first.click()
    p.wait_for_url("**/deals/**")
    p.go_back()
    blk.wait_for()
    for w in (1280, 768, 390):
        p.set_viewport_size({"width": w, "height": 900}); p.wait_for_timeout(300)
        p.screenshot(path=f"{SHOTS}/b03_workplace_{w}.png", full_page=True)
        if p.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"):
            issues.append(f"horizontal overflow {w}")
    print("ISSUES:", issues or "none")
    b.close()
