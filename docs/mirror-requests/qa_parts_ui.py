"""Браузерная проверка зеркальных запросов запчастей (PR 2): менеджер → КД → менеджер."""
import json
from playwright.sync_api import sync_playwright, expect
P=json.load(open("prep_parts.json")); U="http://127.0.0.1:5173"; S="shots/"
issues=[]
def watch(page, who):
    page.on("console", lambda m: m.type=="error" and issues.append(f"[{who}] console: {m.text[:200]}"))
    page.on("response", lambda r: (r.status>=400 and "/api/" in r.url and not r.url.endswith("/api/auth/me")) and issues.append(f"[{who}] {r.status} {r.request.method} {r.url}"))
    page.on("pageerror", lambda e: issues.append(f"[{who}] pageerror: {e}"))
def as_user(b, email, w=1440):
    ctx=b.new_context(viewport={"width":w,"height":1000}, locale="ru-RU", timezone_id="Europe/Moscow")
    assert ctx.request.get(U+"/api/auth/me", headers={"X-Dev-User": email}).ok
    p=ctx.new_page(); watch(p, email); return p
REQ=U+f"/deals/parts/{P['request_id']}"
with sync_playwright() as pw:
    b=pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    m=as_user(b,"manager@example.com")
    m.goto(REQ); m.get_by_text("Запросы поставщикам").wait_for()
    m.locator("tbody tr input[type=checkbox]").first.check()
    m.locator("select[multiple]").first.select_option(label="Альянс-Импекс · CN")
    m.get_by_text("через «Альянс-Импекс» — конечные поставщики").wait_for()
    expect(m.get_by_role("button", name="Запросить цены")).to_be_disabled()
    m.get_by_label("через «Альянс-Импекс» — конечные поставщики").select_option(label="Ningbo Hydraulic Parts · CN")
    m.screenshot(path=S+"p01_request_via_group.png")
    m.get_by_role("button", name="Запросить цены").click()
    m.get_by_text("Альянс-Импекс → поставщик Ningbo Hydraulic Parts").first.wait_for()
    expect(m.get_by_text("запрос от Альянс-Импекс")).to_have_count(0)            # запрос поставщику менеджеру не виден
    m.get_by_role("button", name="отправить").click()
    m.get_by_text("почта «Альянс-Импекс» не настроена").or_(m.get_by_text("почта не подключена")).first.wait_for()
    m.screenshot(path=S+"p02_sent_manager.png", full_page=True)
    c=as_user(b,"cd@example.com")
    c.goto(REQ); c.get_by_text("Ningbo Hydraulic Parts · запрос от Альянс-Импекс", exact=True).first.wait_for()
    row=c.locator("tr", has=c.get_by_role("cell", name="Ningbo Hydraulic Parts · запрос от Альянс-Импекс", exact=True)).first
    row.locator("a").first.click()
    form=c.locator("div", has=c.get_by_text("— ответ по строкам")).filter(has=c.locator("table")).last
    form.locator("tbody select").first.select_option(label="в наличии")
    form.locator("tbody input[type=number]").nth(0).fill("85")
    form.locator("tbody select").nth(1).select_option("CNY")
    form.locator("tbody input[type=number]").nth(1).fill("12")
    c.get_by_role("button", name="Сохранить ответ").click()
    c.get_by_text("Сравнение ответов").wait_for()
    c.get_by_text("поставщик 85 CNY").first.wait_for()
    c.get_by_text("выбирается предложение компании группы").wait_for()
    c.screenshot(path=S+"p03_compare_cd_chain.png", full_page=True)
    m.goto(REQ); m.get_by_text("Сравнение ответов").wait_for()
    expect(m.get_by_text("поставщик 85 CNY")).to_have_count(0)                # цены поставщика у менеджера нет
    expect(m.get_by_text("85 CNY")).to_have_count(0)
    m.locator("table", has=m.get_by_text("Мин. партия")).last.get_by_role("button", name="выбрать").click()
    m.get_by_text("выбран").first.wait_for()
    m.screenshot(path=S+"p04_selected_manager.png", full_page=True)
    b.close()
print("ISSUES:", issues or "none")
