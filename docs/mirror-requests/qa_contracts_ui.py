"""Браузерная проверка PR 3: маржа группы на «Расчёт и утверждение» (КД), пакет компании группы с поставщиком (менеджер)."""
import json
from playwright.sync_api import sync_playwright, expect
P=json.load(open("prep_contracts.json")); U="http://127.0.0.1:5173"; S="shots/"
issues=[]
def watch(page, who):
    page.on("console", lambda m: m.type=="error" and issues.append(f"[{who}] console: {m.text[:200]}"))
    page.on("response", lambda r: (r.status>=400 and "/api/" in r.url and not r.url.endswith("/api/auth/me")) and issues.append(f"[{who}] {r.status} {r.request.method} {r.url}"))
    page.on("pageerror", lambda e: issues.append(f"[{who}] pageerror: {e}"))
def as_user(b, email):
    ctx=b.new_context(viewport={"width":1440,"height":1000}, locale="ru-RU", timezone_id="Europe/Moscow")
    assert ctx.request.get(U+"/api/auth/me", headers={"X-Dev-User": email}).ok
    p=ctx.new_page(); watch(p, email); return p
with sync_playwright() as pw:
    b=pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    c=as_user(b,"cd@example.com")
    c.goto(U+f"/deals/{P['deal_id']}?tab=pricing"); c.get_by_text("Через компанию группы").wait_for()
    c.get_by_text("Shandong Lingong Heavy → Альянс-Импекс").wait_for()
    c.screenshot(path=S+"c01_group_margin_cd.png", full_page=True)
    m=as_user(b,"manager@example.com")
    m.goto(U+f"/deals/{P['deal_id']}?tab=legal"); m.get_by_role("button", name="Закупка у поставщика").click()
    sel=m.locator("label", has_text="1. Поставщик").locator("select")
    opts=sel.locator("option").all_inner_texts()
    assert not any("Shandong" in o for o in opts), opts                                   # договор Альянс-Импекс с поставщиком — не для менеджера
    sel.select_option(index=next(i for i,o in enumerate(opts) if "(компания группы)" in o))
    m.get_by_text("После формирования сразу формируются пакеты компании группы").wait_for()
    m.screenshot(path=S+"c02_wizard_group.png", full_page=True)
    m.get_by_role("button", name="6. Предпросмотр и проверка").click()
    m.get_by_role("button", name="7. Сформировать пакет (черновик)").click()
    m.get_by_text("пакет «Альянс-Импекс — Shandong Lingong Heavy» v1 сформирован").wait_for(timeout=60000)
    m.screenshot(path=S+"c03_built_manager.png", full_page=True)
    expect(m.get_by_text("Закупка · Shandong Lingong Heavy")).to_have_count(0)           # договор Альянс-Импекс с поставщиком менеджеру не виден
    l=as_user(b,"lawyer@example.com")
    l.goto(U+f"/deals/{P['deal_id']}?tab=legal"); l.get_by_text("от имени Альянс-Импекс").wait_for()
    l.screenshot(path=S+"c04_packages_lawyer.png", full_page=True)
    b.close()
print("ISSUES:", issues or "none")
