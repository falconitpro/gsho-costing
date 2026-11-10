import json, io
from playwright.sync_api import sync_playwright, expect
from openpyxl import load_workbook
P=json.load(open("prep.json")); U="http://127.0.0.1:5173"; S="shots/"
issues=[]
def watch(page, who):
    page.on("console", lambda m: m.type=="error" and issues.append(f"[{who}] console: {m.text}"))
    page.on("response", lambda r: (r.status>=400 and "/api/" in r.url and not r.url.endswith("/api/auth/me")) and issues.append(f"[{who}] {r.status} {r.request.method} {r.url}"))
    page.on("pageerror", lambda e: issues.append(f"[{who}] pageerror: {e}"))
def as_user(b, email, w=1440):
    ctx=b.new_context(viewport={"width":w,"height":1000}, locale="ru-RU", timezone_id="Europe/Moscow", accept_downloads=True)
    assert ctx.request.get(U+"/api/auth/me", headers={"X-Dev-User": email}).ok
    p=ctx.new_page(); watch(p, email); return p
def xlsx(page, label="⇩ Excel"):
    with page.expect_download() as d:
        page.get_by_role("button", name=label).first.click()
    dl=d.value; data=open(dl.path(),"rb").read()
    return dl.suggested_filename, load_workbook(io.BytesIO(data))
with sync_playwright() as pw:
    b=pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    p=as_user(b,"fd@example.com")
    p.goto(U+"/finance"); p.get_by_role("button", name="Дебиторка и кредиторка").click()
    p.get_by_text("АО «Алданзолото»").first.wait_for()
    expect(p.get_by_text("Ожидаемые суммы без согласованной даты")).to_be_visible()
    p.screenshot(path=S+"01_debt.png", full_page=True)
    name, wb = xlsx(p)
    assert name.startswith("finance-debt-") and "Задолженность" in wb.sheetnames and "Пояснения" in wb.sheetnames, (name, wb.sheetnames)
    p.get_by_role("button", name="Нам должны").click(); p.get_by_text("Мы должны").first.wait_for(state="detached") if False else None
    p.get_by_text("▾ АО «Алданзолото»").click(); expect(p.get_by_text("▸ АО «Алданзолото»")).to_be_visible()
    # экономика: объяснение цифр
    p.goto(U+f"/finance?tab=economy&deal={P['deal_id']}"); p.get_by_text("Результат (P&L)").wait_for()
    p.locator("tr", has_text="Выручка, ₽").get_by_role("button", name="Объяснить цифру").last.click()
    p.get_by_text("Как получена цифра: признанная выручка").wait_for()
    expect(p.locator(".card", has_text="Как получена цифра").get_by_role("link", name="УПД № У-115")).to_be_visible()
    p.screenshot(path=S+"02_explain_revenue.png", full_page=False)
    p.locator("tr", has_text="Факт").filter(has_text="дн.").get_by_role("button", name="Объяснить цифру").click()
    p.get_by_text("Как получена цифра: кассовый разрыв — факт").wait_for()
    p.get_by_text("Накопленное сальдо по дням").click()
    p.screenshot(path=S+"03_explain_gap.png", full_page=True)
    name, wb = xlsx(p)
    assert "Статьи" in wb.sheetnames and wb["Статьи"].max_row == 19, wb.sheetnames
    # календарь и сверка — выгрузка с фильтрами
    p.goto(U+"/finance?tab=calendar&direction=in"); p.get_by_text("АО «Алданзолото»").first.wait_for()
    name, wb = xlsx(p); assert name.startswith("finance-calendar-")
    assert all(r[2].value == "нам платят" for r in wb["Календарь"].iter_rows(min_row=2)), "фильтр направления не применён"
    p.goto(U+"/finance?tab=reconciliation"); p.get_by_text("Все ·").first.wait_for()
    name, wb = xlsx(p); assert "Сверка" in wb.sheetnames
    p.goto(U+"/finance?tab=liquidity"); p.get_by_text("Прогноз остатков").wait_for()
    name, wb = xlsx(p); assert "Прогноз остатков" in wb.sheetnames
    m=as_user(b,"fd@example.com", 390)
    m.goto(U+"/finance?tab=debt"); m.get_by_text("АО «Алданзолото»").first.wait_for()
    m.screenshot(path=S+"04_debt_390.png", full_page=True)
    b.close()
print("ISSUES:", issues or "none")
