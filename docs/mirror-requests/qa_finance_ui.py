"""Браузерная проверка PR 4 (ФД): «Внутригрупповые расчёты» и переключатель «без внутригрупповых» в ликвидности."""
from playwright.sync_api import sync_playwright, expect
U="http://127.0.0.1:5173"; S="shots/"; issues=[]
with sync_playwright() as pw:
    b=pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    ctx=b.new_context(viewport={"width":1440,"height":1000}, locale="ru-RU", timezone_id="Europe/Moscow")
    assert ctx.request.get(U+"/api/auth/me", headers={"X-Dev-User": "fd@example.com"}).ok
    p=ctx.new_page()
    p.on("console", lambda m: m.type=="error" and issues.append(f"console: {m.text[:200]}"))
    p.on("response", lambda r: (r.status>=400 and "/api/" in r.url and not r.url.endswith("/api/auth/me")) and issues.append(f"{r.status} {r.request.method} {r.url}"))
    p.on("pageerror", lambda e: issues.append(f"pageerror: {e}"))
    p.goto(U+"/finance?tab=intragroup"); p.get_by_text("Сальдо между компаниями группы").wait_for()
    p.get_by_text("→ Альянс-Импекс").first.wait_for()
    p.get_by_text("→ Альянс-Импекс").first.click(); p.get_by_text("QA: Альянс-Импекс, аванс 30 %").wait_for()
    expect(p.get_by_text("QA: Shandong Lingong Heavy")).to_have_count(0)                   # внешняя закупка — не внутригрупповая
    p.screenshot(path=S+"f01_intragroup.png", full_page=True)
    p.goto(U+"/finance?tab=liquidity"); p.get_by_text("без внутригрупповых").wait_for()
    p.get_by_label("без внутригрупповых").check(); p.get_by_text("Без внутригрупповых: исключено").wait_for()
    p.screenshot(path=S+"f02_liquidity_nointra.png", full_page=True)
    b.close()
print("ISSUES:", issues or "none")
