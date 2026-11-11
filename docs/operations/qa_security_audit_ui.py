"""Браузерная проверка «Журнал и предупреждения» (этап 3а). Синтетическая БД; backend :8000, vite :5173.
    python3 docs/operations/qa_security_audit_ui.py <пароль ЗГД>
Подготовка через API: синтетический администратор делает 7 выгрузок справочника (порог 5), ЗГД сбрасывает код сотруднику."""
import sys
import uuid
from playwright.sync_api import expect, sync_playwright

U, API, SHOTS = "http://127.0.0.1:5173", "http://127.0.0.1:8000", "docs/operations/screens"
GD, GD_PW = "evgeniy@dobrov.pro", sys.argv[1]
issues = []

with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, locale="ru-RU", timezone_id="Europe/Moscow")
    rq = ctx.request
    H = lambda e: {"X-Dev-User": e}   # noqa: E731
    tag = uuid.uuid4().hex[:6]
    adm = rq.post(API + "/api/admin/users", headers=H(GD), data={"email": f"qa.adm.{tag}@example.com", "display_name": f"Выгрузкин Пётр {tag}", "roles": ["admin"]}).json()
    emp = rq.post(API + "/api/admin/users", headers=H(GD), data={"email": f"qa.emp.{tag}@example.com", "display_name": f"Телефонова Анна {tag}", "roles": ["manager"]}).json()
    for _ in range(7):
        assert rq.get(API + "/api/admin/hs/export", headers=H(adm["email"])).ok
    assert rq.post(API + "/api/auth/step-up", headers=H(GD), data={"password": GD_PW}).ok
    assert rq.post(API + f"/api/security/users/{emp['id']}/mfa/reset", headers=H(GD)).ok
    # подтверждение «протухает» не раньше 10 минут — сбрасываем через выход ЗГД из dev-сессии не нужно: для resolve critical диалог проверим отдельно
    p = ctx.new_page()
    p.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and issues.append(m.text[:160]))
    p.on("pageerror", lambda e: issues.append(str(e)))
    p.on("response", lambda r: r.status >= 400 and "/api/" in r.url and issues.append(f"{r.status} {r.url}"))
    rq.get(API + "/api/auth/me", headers=H(GD))
    p.goto(U + "/settings?s=security_audit")
    p.get_by_role("heading", name="Журнал и предупреждения").wait_for()
    row = p.locator(".aud-table tbody tr", has=p.get_by_text("Частые выгрузки"))
    expect(row).to_contain_text(adm["display_name"])
    expect(row).to_contain_text("2")             # 6-я и 7-я выгрузки — одна запись, два повтора
    p.screenshot(path=f"{SHOTS}/a01_alerts_1440.png", full_page=True)
    row.get_by_role("button", name="Проверить").click()
    dlg = p.get_by_role("dialog", name="Частые выгрузки")
    expect(dlg.get_by_text("событий за 10 минут")).to_be_visible()
    expect(dlg.locator(".aud-table tbody tr")).to_have_count(2)
    expect(dlg.get_by_role("button", name="Взять в работу")).to_be_disabled()
    dlg.get_by_label("Комментарий").fill("Выгрузка справочника для сверки с брокером")
    p.screenshot(path=f"{SHOTS}/a02_alert_card_1440.png")
    dlg.get_by_role("button", name="Взять в работу").click()
    expect(dlg.get_by_text("Взял в работу")).to_be_visible()
    dlg.get_by_label("Комментарий").fill("Проверено, согласовано с ФД")
    dlg.get_by_role("button", name="Закрыть предупреждение").click()
    expect(dlg.get_by_text("Закрыл")).to_be_visible()
    p.keyboard.press("Escape")
    expect(p.locator(".aud-table tbody tr", has=p.get_by_text("Частые выгрузки"))).to_have_count(0)   # фильтр «открытые»
    # критическое: сброс кода
    crit = p.locator(".aud-table tbody tr", has=p.get_by_text(emp["display_name"]))
    expect(crit).to_contain_text("критическая")
    # переход к событиям сотрудника
    crit.get_by_role("button", name=emp["display_name"]).click()
    p.get_by_role("tab", name="Журнал событий", selected=True).wait_for()
    expect(p.locator(".aud-userchip")).to_contain_text(emp["display_name"])
    expect(p.locator(".aud-table tbody")).to_contain_text("сброс администратором")
    p.screenshot(path=f"{SHOTS}/a03_events_user_1440.png", full_page=True)
    p.locator(".aud-userchip").get_by_role("button").click()
    p.get_by_label("Тип").select_option("export")
    expect(p.locator(".aud-table tbody tr").first).to_contain_text("CSV")
    for w in (1280, 768, 390):
        p.set_viewport_size({"width": w, "height": 900}); p.wait_for_timeout(250)
        p.screenshot(path=f"{SHOTS}/a04_events_{w}.png", full_page=True)
        over = p.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        if over:
            issues.append(f"horizontal page overflow at {w}")
    p.get_by_role("tab", name="Предупреждения").click()
    p.get_by_label("Статус").select_option("")
    p.wait_for_timeout(300)
    p.screenshot(path=f"{SHOTS}/a05_alerts_390.png", full_page=True)
    p.set_viewport_size({"width": 1440, "height": 900})
    print("ISSUES:", issues or "none")
    b.close()
