"""Браузер: ЗГД задаёт правило «ГШО, Китай → через Альянс-Импекс»; менеджер выбирает только конечного поставщика; ФД видит цепочку и утверждает."""
import json
from playwright.sync_api import sync_playwright, expect
P=json.load(open("prep_rules.json")); U="http://127.0.0.1:5173"; S="shots/"; issues=[]
def page(b, email):
    ctx=b.new_context(viewport={"width":1440,"height":1000}, locale="ru-RU", timezone_id="Europe/Moscow")
    assert ctx.request.get(U+"/api/auth/me", headers={"X-Dev-User": email}).ok
    p=ctx.new_page()
    p.on("console", lambda m: m.type=="error" and issues.append(f"[{email}] console: {m.text[:200]}"))
    p.on("response", lambda r: (r.status>=400 and "/api/" in r.url and not r.url.endswith("/api/auth/me")) and issues.append(f"[{email}] {r.status} {r.request.method} {r.url}"))
    p.on("pageerror", lambda e: issues.append(f"[{email}] pageerror: {e}"))
    return p
with sync_playwright() as pw:
    b=pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    g=page(b, "evgeniy@dobrov.pro")
    g.goto(U+"/settings?tab=rates"); g.get_by_text("Правила внутригрупповых цепочек").wait_for()
    g.get_by_role("button", name="+ правило").click()
    row=g.locator("tr", has=g.get_by_placeholder("CN или *")).last
    row.locator("select").nth(2).select_option(label="Поставщик КНР → компания в Китае → РФ")
    row.locator("input").nth(1).fill("Китай — через Альянс-Импекс")
    g.get_by_role("button", name="Сохранить правила").click(); g.get_by_text("Правила сохранены").wait_for()
    g.screenshot(path=S+"r01_rules_gd.png", full_page=False)
    m=page(b, "manager@example.com")
    m.goto(U+f"/deals/{P['deal_id']}?tab=positions"); m.get_by_text("Завод для запроса:").wait_for()
    sel=m.locator("select", has=m.locator("option", has_text="завод…"))
    opts=sel.locator("option").all_inner_texts()
    assert not any("Альянс-Импекс" in o for o in opts), opts                              # менеджер компанию группы не выбирает
    m.get_by_role("button", name="Отправить запрос заводу").click()
    m.get_by_text("Альянс-Импекс → Shandong Lingong Heavy [правило").first.wait_for()
    m.screenshot(path=S+"r02_request_manager.png", full_page=False)
    f=page(b, "fd@example.com")
    f.goto(U+f"/deals/{P['deal_id']}?tab=offers"); f.get_by_text("цепочка:").first.wait_for()
    f.screenshot(path=S+"r03_offers_fd.png", full_page=True)
    b.close()
print("ISSUES:", issues or "none")
