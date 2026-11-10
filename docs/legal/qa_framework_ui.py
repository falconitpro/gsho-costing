"""Браузер: КД делает договор рамочным в карточке; менеджер в мастере пакета видит «спецификация к рамочному»."""
import json
from playwright.sync_api import sync_playwright
P=json.load(open("prep_framework.json")); U="http://127.0.0.1:5173"; S="shots/"; issues=[]
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
    c=page(b, "cd@example.com")
    c.goto(U+f"/legal?tab=contracts&open={P["contract_id"]}"); c.get_by_text("Рамочный договор", exact=True).wait_for()
    c.get_by_label("Рамочный договор").click(); c.get_by_text("спецификаций:").wait_for(); assert c.get_by_label("Рамочный договор").is_checked()
    c.screenshot(path=S+"fw01_card_cd.png")
    m=page(b, "manager@example.com")
    m.goto(U+f"/deals/{P['deal_id']}?tab=legal"); m.get_by_role("button", name="Закупка у поставщика").click()
    sel=m.locator("label", has_text="Оформление").locator("select")
    opts=sel.locator("option").all_inner_texts(); assert any("спецификация к рамочному" in o for o in opts), opts
    sel.select_option(index=1); m.screenshot(path=S+"fw02_wizard_manager.png", full_page=True)
    b.close()
print("ISSUES:", issues or "none")
