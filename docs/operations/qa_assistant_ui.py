"""Браузерная проверка «Помощник CRM» (этап 6). Синтетическая БД после qa_briefing_prep.py; backend :8000, vite :5173.
    python3 docs/operations/qa_assistant_ui.py
Менеджер: первое открытие (пояснение), быстрый вопрос «Что требует моего решения?» → ответ из «Ваших приоритетов» и переход
по источнику; справка; общий режим выключен честно; история и удаление диалога; Esc сохраняет черновик; ширины 1440–390."""
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
    p.get_by_role("button", name="Помощник", exact=True).click()
    dr = p.get_by_role("dialog", name="Помощник CRM")
    expect(dr.get_by_text("Не вводите пароли")).to_be_visible()
    expect(dr.get_by_text("без внешней модели")).to_be_visible()
    dr.get_by_role("button", name="Понятно").click()
    dr.get_by_role("button", name="Что требует моего решения?").click()
    expect(dr.locator(".ai-msg.assistant").last).to_contain_text("Отправить КП клиенту «Север»")
    p.screenshot(path=f"{SHOTS}/ai01_answer_1440.png")
    dr.locator(".ai-chip").first.click()
    p.wait_for_url("**/deals/**")
    # справка
    dr.get_by_label("Режим помощника").select_option("help")
    dr.get_by_label("Вопрос помощнику").fill("Как сменить пароль?")
    dr.get_by_label("Вопрос помощнику").press("Enter")
    expect(dr.locator(".ai-msg.assistant").last).to_contain_text("Сменить пароль")
    # общий режим — честно выключен
    dr.get_by_label("Режим помощника").select_option("general")
    expect(dr.get_by_text("Контекст CRM не используется")).to_be_visible()
    dr.get_by_label("Вопрос помощнику").fill("Какая погода?")
    dr.get_by_role("button", name="Спросить").click()
    expect(dr.locator(".ai-msg.assistant").last).to_contain_text("Общий ИИ отключён")
    # неоднозначный вопрос о долге — уточнение, без сумм
    dr.get_by_label("Режим помощника").select_option("crm")
    dr.get_by_label("Вопрос помощнику").fill("сколько должны?")
    dr.get_by_label("Вопрос помощнику").press("Enter")
    expect(dr.locator(".ai-msg.assistant").last).to_contain_text("Уточните")
    # Esc закрывает, черновик остаётся
    dr.get_by_label("Вопрос помощнику").fill("черновик вопроса")
    p.keyboard.press("Escape")
    expect(dr).to_be_hidden()
    p.get_by_role("button", name="Помощник", exact=True).click()
    expect(dr.get_by_label("Вопрос помощнику")).to_have_value("черновик вопроса")
    # история и удаление
    dr.get_by_role("button", name="История").click()
    expect(dr.locator(".ai-hist")).to_have_count(1)
    dr.locator(".ai-hist").first.click()
    dr.get_by_role("button", name="Удалить диалог").click()
    dr.get_by_role("button", name="Удалить", exact=True).click()
    dr.get_by_role("button", name="История").click()
    expect(dr.get_by_text("Диалогов пока нет.")).to_be_visible()
    dr.get_by_role("button", name="← Назад").click()
    for w in (1280, 768, 390):
        p.set_viewport_size({"width": w, "height": 900}); p.wait_for_timeout(300)
        p.screenshot(path=f"{SHOTS}/ai02_drawer_{w}.png")
        if p.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"):
            issues.append(f"horizontal overflow {w}")
    print("ISSUES:", issues or "none")
    b.close()
