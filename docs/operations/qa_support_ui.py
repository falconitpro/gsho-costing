"""Браузерная проверка «Поддержка» (этап 4). Синтетическая БД; backend :8000 (FILE_SCAN=clamav, clamd), vite :5173.
    python3 docs/operations/qa_support_ui.py
Менеджер создаёт обращение с файлом и строкой, похожей на скрипт → ЗГД (поддержка) назначает, отвечает, запрашивает данные →
менеджер видит отметку на кнопке, отвечает, специалист решает → менеджер закрывает. Ширины 1440/768/390."""
from playwright.sync_api import expect, sync_playwright

U, API, SHOTS = "http://127.0.0.1:5173", "http://127.0.0.1:8000", "docs/operations/screens"
GD, MGR = "evgeniy@dobrov.pro", "manager@example.com"
XSS = '<img src=x onerror="window.__xss=1">'
issues = []

with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, locale="ru-RU", timezone_id="Europe/Moscow")
    rq = ctx.request
    who = lambda e: rq.get(API + "/api/auth/me", headers={"X-Dev-User": e})   # noqa: E731  (dev-вход: cookie сессии)
    p = ctx.new_page()
    p.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and issues.append(m.text[:160]))
    p.on("pageerror", lambda e: issues.append(str(e)))
    p.on("response", lambda r: r.status >= 500 and "/api/" in r.url and issues.append(f"{r.status} {r.url}"))
    who(MGR)
    p.goto(U + "/deals")
    p.get_by_role("button", name="Поддержка").click()
    p.get_by_role("heading", name="Поддержка").wait_for()
    p.get_by_role("button", name="Новое обращение").click()
    dlg = p.get_by_role("dialog", name="Новое обращение")
    dlg.get_by_label("Тема").fill(f"Не сохраняется сделка {XSS}")
    expect(dlg.get_by_role("button", name="Отправить")).to_be_disabled()
    dlg.get_by_label("Описание").fill("Нажимаю «Сохранить» в карточке сделки — крутится и ничего. Ожидал сохранение. Около 10:30.")
    dlg.locator("input[type=file]").set_input_files([{"name": "скрин.png", "mimeType": "image/png", "buffer": b"\x89PNG qa"}])
    expect(dlg.get_by_text("скрин.png")).to_be_visible()
    dlg.get_by_text("Что будет передано").click()
    expect(dlg.get_by_text("страница: /deals")).to_be_visible()
    p.screenshot(path=f"{SHOTS}/s01_new_1440.png")
    dlg.get_by_role("button", name="Отправить").click()
    p.get_by_text("создано. Ответ появится здесь").wait_for()
    number = p.locator(".sup-head h2 .muted").inner_text()
    expect(p.locator(".sup-head h2")).to_contain_text(XSS)            # текстом, не тегом
    assert p.evaluate("window.__xss") is None, "XSS выполнился"
    expect(p.get_by_text("проверен антивирусом")).to_be_visible()
    p.screenshot(path=f"{SHOTS}/s02_ticket_1440.png", full_page=True)
    # специалист
    who(GD)
    p.goto(U + "/support/admin")
    p.get_by_role("heading", name="Очередь поддержки").wait_for()
    row = p.locator(".sup-desktop tbody tr", has=p.get_by_text(number))
    expect(row).to_contain_text("новое")
    p.screenshot(path=f"{SHOTS}/s03_queue_1440.png", full_page=True)
    row.click()
    p.get_by_label("Назначено").select_option(index=1)     # единственный специалист поддержки в демо-данных — ЗГД
    p.get_by_text("Назначение сохранено").wait_for()
    p.get_by_label("Ответ автору").fill("Посмотрю. Какой браузер и номер сделки?")
    p.get_by_role("button", name="Отправить").click()
    expect(p.locator(".sup-msg.specialist")).to_contain_text("Какой браузер")
    p.get_by_role("button", name="Запросить данные у автора").click()
    d2 = p.get_by_role("dialog", name="Запросить данные у автора")
    d2.get_by_label("Что нужно от автора").fill("Номер сделки и скриншот консоли")
    d2.get_by_role("button", name="Запросить данные у автора").click()
    expect(p.locator(".sup-head")).to_contain_text("ждём автора")
    # автор: отметка на кнопке, ответ, закрытие
    who(MGR)
    p.goto(U + "/deals")
    expect(p.locator(".sup-badge")).to_be_visible()
    p.get_by_role("button", name="Поддержка").click()
    p.get_by_role("tab", name="Ждут меня").click()
    p.locator(".sup-desktop tbody tr", has=p.get_by_text(number)).click()
    p.get_by_label("Ваш ответ").fill("Сделка 2026-0012, Chrome 140")
    p.get_by_role("button", name="Отправить").click()
    expect(p.locator(".sup-head")).to_contain_text("в работе")
    expect(p.locator(".sup-badge")).to_have_count(0)
    who(GD)
    p.goto(U + "/support/admin")
    p.locator(".sup-desktop tbody tr", has=p.get_by_text(number)).click()
    p.get_by_role("button", name="Отметить решённым").click()
    d3 = p.get_by_role("dialog", name="Отметить решённым")
    d3.get_by_label("Решение").fill("Исправлено в обновлении от 11.11")
    d3.get_by_role("button", name="Отметить решённым").click()
    expect(p.locator(".sup-head")).to_contain_text("решено")
    who(MGR)
    p.goto(U + "/support")
    p.get_by_role("tab", name="Решённые").click()
    p.locator(".sup-desktop tbody tr", has=p.get_by_text(number)).click()
    p.get_by_role("button", name="Закрыть обращение").click()
    d4 = p.get_by_role("dialog", name="Закрыть обращение")
    expect(d4.get_by_text("Это не удаление")).to_be_visible()
    d4.get_by_role("button", name="Закрыть обращение").click()
    expect(p.locator(".sup-head")).to_contain_text("закрыто")
    expect(p.get_by_text("Обращение закрыто. Если проблема повторилась")).to_be_visible()
    for w in (768, 390):
        p.set_viewport_size({"width": w, "height": 900}); p.wait_for_timeout(250)
        p.screenshot(path=f"{SHOTS}/s04_ticket_{w}.png", full_page=True)
        if p.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"):
            issues.append(f"horizontal overflow ticket {w}")
    # мобильная форма: создать и ответить
    p.goto(U + "/support")
    p.get_by_role("button", name="Новое обращение").click()
    dlg = p.get_by_role("dialog", name="Новое обращение")
    dlg.get_by_label("Тема").fill("Вопрос с телефона")
    dlg.get_by_label("Описание").fill("Как приложить фото приёмки с телефона?")
    dlg.get_by_label("Категория").select_option("question")
    p.screenshot(path=f"{SHOTS}/s05_new_390.png", full_page=True)
    if p.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"):
        issues.append("horizontal overflow form 390")
    dlg.get_by_role("button", name="Отправить").click()
    p.get_by_text("создано. Ответ появится здесь").wait_for()
    p.get_by_label("Ваш ответ").fill("Дополнение: телефон Android")
    p.get_by_role("button", name="Отправить").click()
    expect(p.locator(".sup-msg")).to_have_count(2)
    p.goto(U + "/support"); p.wait_for_timeout(300)
    p.screenshot(path=f"{SHOTS}/s06_list_390.png", full_page=True)
    print("number:", number, "ISSUES:", issues or "none")
    b.close()
