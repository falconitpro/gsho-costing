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
    r=ctx.request.get(U+"/api/auth/me", headers={"X-Dev-User": email}); assert r.ok
    p=ctx.new_page(); watch(p, email); return p
with sync_playwright() as pw:
    b=pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    p=as_user(b,"fd@example.com")
    p.goto(U+"/finance"); p.get_by_role("button", name="Экономика сделок").click()
    p.locator("label:has-text('Сделка') select").select_option(P["deal_id"])
    p.get_by_text("Результат (P&L)").wait_for()
    expect(p.get_by_text("Выручка не признана")).to_be_visible()
    p.screenshot(path=S+"01_economy_before.png", full_page=True)
    # признание выручки по УПД
    rec=p.locator(".card").filter(has_text="Признание выручки и расходов")
    rec.get_by_placeholder("сумма").fill("10000000")
    rec.get_by_placeholder("номер документа").fill("У-115")
    rec.get_by_role("button", name="Признать").click()
    p.get_by_text("Признание записано").wait_for()
    expect(rec.get_by_text("УПД № У-115")).to_be_visible()
    expect(p.get_by_text("Выручка не признана")).to_have_count(0)
    p.screenshot(path=S+"02_economy_after_revenue.png", full_page=True)
    # расход в валюте
    rec.locator("select").first.select_option("expense")
    rec.locator("select").nth(1).select_option("finance")
    rec.get_by_placeholder("сумма").fill("8000")
    rec.locator("select").nth(2).select_option("CNY")
    rec.get_by_placeholder("курс, ₽").fill("12,5")
    rec.get_by_placeholder("источник курса").fill("курс банка")
    rec.get_by_placeholder("номер документа").fill("А-7")
    rec.get_by_role("button", name="Признать").click()
    try: p.get_by_text("Признание записано").wait_for(timeout=8000)
    except Exception: p.screenshot(path=S+"err.png", full_page=True); raise
    expect(rec.get_by_text("100 000,00").first).to_be_visible()
    # ссылка в карточку сделки → план/факт
    p.get_by_role("link", name="Платежи и план/факт").click()
    try: p.get_by_text("Экономика в Финансах").wait_for(timeout=8000)
    except Exception: p.screenshot(path=S+"err.png", full_page=False); raise
    expect(p.get_by_text("Кассовый разрыв, ₽")).to_be_visible()
    p.screenshot(path=S+"03_planfact_fd.png", full_page=True)
    # КД: вкладки экономики в разделе нет (раздел закрыт), план/факт открывается, ссылки на Финансы нет
    q=as_user(b,"cd@example.com")
    q.goto(U+f"/deals/{P['deal_id']}?tab=planfact"); q.get_by_text("План и факт").first.wait_for()
    expect(q.get_by_text("Экономика в Финансах")).to_have_count(0)
    q.screenshot(path=S+"04_planfact_cd.png", full_page=True)
    # узкий экран
    m=as_user(b,"fd@example.com", 390)
    m.goto(U+f"/finance?tab=economy&deal={P['deal_id']}"); m.get_by_text("Результат (P&L)").wait_for()
    m.screenshot(path=S+"05_economy_390.png", full_page=True)
    # менеджер: вкладки нет и раздела нет
    g=as_user(b,"manager@example.com")
    g.goto(U+"/finance"); g.get_by_text("доступен при праве").wait_for()
    b.close()
print("ISSUES:", issues or "none")
