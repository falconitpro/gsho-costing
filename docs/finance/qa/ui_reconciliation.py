import json
from playwright.sync_api import sync_playwright, expect
P=json.load(open("prep.json")); U="http://127.0.0.1:5173"; S="shots/"
issues=[]
def watch(page, who):
    page.on("console", lambda m: m.type=="error" and issues.append(f"[{who}] console: {m.text}"))
    page.on("response", lambda r: (r.status>=400 and "/api/" in r.url and not r.url.endswith("/api/auth/me")) and issues.append(f"[{who}] {r.status} {r.request.method} {r.url}"))
    page.on("pageerror", lambda e: issues.append(f"[{who}] pageerror: {e}"))
def as_user(b, email, w=1440):
    ctx=b.new_context(viewport={"width":w,"height":1000}, locale="ru-RU", timezone_id="Europe/Moscow")
    assert ctx.request.get(U+"/api/auth/me", headers={"X-Dev-User": email}).ok
    p=ctx.new_page(); watch(p, email); return p
with sync_playwright() as pw:
    b=pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    p=as_user(b,"fd@example.com")
    p.goto(U+"/finance"); p.get_by_role("button", name="Сверка и уточнения").click()
    p.get_by_role("button", name="Операции без разнесения · 1").wait_for()
    p.screenshot(path=S+"01_reconciliation.png", full_page=True)
    p.get_by_role("button", name="Операции без разнесения · 1").click()
    row=p.locator("tbody tr").filter(has_text="п/п 881").first
    row.get_by_role("button", name="Запросить уточнение").click()
    p.get_by_text("Ответственный увидит:").wait_for()
    expect(p.get_by_text("Назначение платежа: оплата по счёту 15")).to_be_visible()
    p.locator("label:has-text('Ответственный') select").select_option(label="Бочаров Егор (менеджер)")
    p.get_by_placeholder("Вопрос — что нужно уточнить").fill("Чья это оплата 77 000? По какому договору?")
    p.screenshot(path=S+"02_ask_form.png", full_page=True)
    p.get_by_role("button", name="Отправить").click()
    p.get_by_text("Уточнение: операция п/п 881").wait_for()
    p.screenshot(path=S+"03_question_open.png", full_page=True)
    expect(p.get_by_text("ответственный Бочаров Егор (менеджер)")).to_be_visible()
    # менеджер: задача в «Моих задачах» → страница ответа (раздела «Финансы» у него нет)
    m=as_user(b,"manager@example.com")
    m.goto(U+"/tasks"); m.get_by_text("Уточнение: операция п/п 881").first.wait_for()
    m.screenshot(path=S+"04_manager_tasks.png", full_page=True)
    m.locator("a", has_text="Открыть →").filter(has=None).last.click() if False else m.get_by_role("link", name="Финансы").first.click()
    m.get_by_role("heading", name="Уточнение по «Финансам»").wait_for()
    expect(m.get_by_text("Чья это оплата 77 000? По какому договору?")).to_be_visible()
    m.locator("input[type=file]").set_input_files({"name": "платёжка.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 test"})
    m.get_by_text("Файл «платёжка.pdf» приложен").wait_for()
    m.get_by_placeholder("Ваш ответ").fill("Это оплата АО «Рудник» по договору "+P["number"]+", платёж 1.")
    m.get_by_role("button", name="Отправить ответ").click()
    m.get_by_text("Ответ отправлен").wait_for()
    m.screenshot(path=S+"05_manager_answered.png", full_page=True)
    m.goto(U+"/finance"); m.get_by_text("доступен при праве").wait_for()
    # ФД: очередь «Уточнения» → проверка ответа
    p.goto(U+"/finance?tab=reconciliation&queue=questions"); p.get_by_role("button", name="Проверить ответ").first.click()
    p.get_by_text("Это оплата АО «Рудник»").wait_for()
    expect(p.get_by_role("link", name="платёжка.pdf")).to_be_visible()
    p.get_by_role("button", name="Ответ проверен").click()
    p.get_by_text("ответ проверен", exact=True).first.wait_for()
    p.screenshot(path=S+"06_verified.png", full_page=True)
    w=as_user(b,"fd@example.com", 390)
    w.goto(U+"/finance?tab=reconciliation"); w.get_by_text("Операции без разнесения").first.wait_for()
    w.screenshot(path=S+"07_reconciliation_390.png", full_page=True)
    b.close()
print("ISSUES:", issues or "none")
