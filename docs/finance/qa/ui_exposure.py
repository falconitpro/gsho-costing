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
    p.goto(U+"/finance"); p.get_by_role("button", name="Обеспечение и неустойки").click()
    p.get_by_role("heading", name="Обеспечение").wait_for()
    expect(p.get_by_text("Условия и решения редактируются в юридическом блоке")).to_be_visible()
    expect(p.get_by_text("истекает через 12 дн.")).to_be_visible()
    expect(p.get_by_text("аккредитив клиента")).to_be_visible()
    expect(p.get_by_text("не входит: ещё начисляется")).to_be_visible()
    p.screenshot(path=S+"01_exposure.png", full_page=True)
    # фильтр и переход в календарь по прогнозу неустойки
    p.locator("select:has(option[value=receivable])").select_option("receivable")
    expect(p.get_by_text("не входит: ещё начисляется")).to_have_count(0)
    p.get_by_role("link", name="ожидается").first.click()
    p.get_by_text("неустойка").first.wait_for()
    p.screenshot(path=S+"02_calendar_penalty.png", full_page=True)
    # переход в договор
    p.goto(U+"/finance?tab=exposure"); p.get_by_role("link", name=P["number"]).first.click()
    p.wait_for_url("**/legal?tab=contracts&open=*"); p.get_by_text(P["number"]).first.wait_for()
    p.screenshot(path=S+"03_contract.png")
    # сводка «Деньги» у КД — гарантии из общего сервиса
    q=as_user(b,"cd@example.com")
    q.goto(U+"/summary?kind=money"); q.get_by_text("банковская гарантия по сделке").first.wait_for()
    q.screenshot(path=S+"04_summary_cd.png", full_page=True)
    m=as_user(b,"fd@example.com", 390)
    m.goto(U+"/finance?tab=exposure"); m.get_by_role("heading", name="Обеспечение").wait_for()
    m.screenshot(path=S+"05_exposure_390.png", full_page=True)
    b.close()
print("ISSUES:", issues or "none")
