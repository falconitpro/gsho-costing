"""Полный обход: все вкладки «Финансов» × роли × ширины; консоль, 4xx/5xx, все внутренние ссылки раздела открываются."""
import json, re
from playwright.sync_api import sync_playwright
U="http://127.0.0.1:5173"; S="shots/"
D=json.load(open("prep.json"))
TABS=["overview","calendar","debt","liquidity","economy","exposure","reconciliation"]
issues=[]; visited=set(); report=[]
def watch(page, who):
    page.on("console", lambda m: m.type=="error" and issues.append(f"[{who}] console: {m.text[:200]}"))
    page.on("response", lambda r: (r.status>=400 and "/api/" in r.url and not r.url.endswith("/api/auth/me")) and issues.append(f"[{who}] {r.status} {r.request.method} {r.url}"))
    page.on("pageerror", lambda e: issues.append(f"[{who}] pageerror: {e}"))
def as_user(b, email, w):
    ctx=b.new_context(viewport={"width":w,"height":900}, locale="ru-RU", timezone_id="Europe/Moscow")
    assert ctx.request.get(U+"/api/auth/me", headers={"X-Dev-User": email}).ok
    p=ctx.new_page(); watch(p, f"{email}@{w}"); return p
def settle(p):
    p.wait_for_load_state("networkidle"); p.wait_for_timeout(300)
    assert p.get_by_text("Загружаю…").count()==0 or p.wait_for_timeout(1500) is None
with sync_playwright() as pw:
    b=pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    for w in (1440, 1280, 768, 390):
        p=as_user(b,"fd@example.com", w)
        for t in TABS:
            url=f"{U}/finance?tab={t}" + (f"&deal={D['deal_id']}" if t=="economy" else "")
            p.goto(url); settle(p)
            err=p.locator(".alert.error").count()
            hscroll=p.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 2")
            report.append({"w": w, "tab": t, "errors_on_page": err, "page_hscroll": hscroll})
            if w in (1440, 390): p.screenshot(path=f"{S}{w}_{t}.png", full_page=True)
            if w==1440:
                for href in p.eval_on_selector_all("main a[href^='/'], a[href^='/finance'], a[href^='/deals'], a[href^='/legal']", "els => els.map(e => e.getAttribute('href'))"):
                    if href and not href.startswith("/api/"): visited.add(href)
        p.context.close()
    # каждая внутренняя ссылка из раздела открывается без ошибок
    p=as_user(b,"fd@example.com",1440); broken=[]
    for href in sorted(visited)[:120]:
        p.goto(U+href); p.wait_for_load_state("networkidle")
        if p.locator(".alert.error").count() or "Не найдено" in p.content():
            broken.append(href)
    # другие роли: КД — раздела нет, план/факт и сводка открываются; менеджер — задачи и уточнения; ЗГД — всё
    for email, paths in (("cd@example.com", ["/finance", f"/deals/{D['deal_id']}?tab=planfact", "/summary?kind=money", "/"]),
                         ("manager@example.com", ["/finance", "/tasks", "/"]),
                         ("evgeniy@dobrov.pro", [f"/finance?tab={t}" for t in TABS])):
        q=as_user(b, email, 1440)
        for x in paths:
            q.goto(U+x); settle(q)
            report.append({"user": email, "path": x, "errors_on_page": q.locator(".alert.error").count()})
        q.screenshot(path=f"{S}role_{email.split('@')[0]}.png", full_page=True)
    b.close()
print(json.dumps({"issues": issues, "links_checked": min(len(visited),120), "links_total": len(visited), "broken": broken,
                  "pages_with_error": [r for r in report if r.get("errors_on_page")], "page_hscroll": [r for r in report if r.get("page_hscroll")]}, ensure_ascii=False, indent=1))
