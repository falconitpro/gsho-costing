import json, re
from playwright.sync_api import sync_playwright, expect
P=json.load(open("prep.json")); U="http://127.0.0.1:5173"; S="shots/"
issues=[]
def watch(page, who):
    page.on("console", lambda m: m.type=="error" and issues.append(f"[{who}] console: {m.text}"))
    page.on("response", lambda r: (r.status>=400 and "/api/" in r.url and not r.url.endswith("/api/auth/me")) and issues.append(f"[{who}] {r.status} {r.request.method} {r.url}"))
    page.on("pageerror", lambda e: issues.append(f"[{who}] pageerror: {e}"))
def as_user(ctx, email):
    r=ctx.request.get(U+"/api/auth/me", headers={"X-Dev-User": email}); assert r.ok, r.status   # dev-стенд: сессия по заголовку, дальше cookie
    p=ctx.new_page(); watch(p, email); return p
with sync_playwright() as pw:
    b=pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    ctx=b.new_context(viewport={"width":1440,"height":1000}, locale="ru-RU", timezone_id="Europe/Moscow")
    p=as_user(ctx,"fd@example.com")
    p.on("dialog", lambda d: d.accept("ошибочный ввод суммы"))
    p.goto(U+"/finance"); expect(p.get_by_role("heading", name="Финансы")).to_be_visible()
    expect(p.locator("header nav").get_by_text("Финансы")).to_be_visible()
    p.get_by_text("Ближайшие платежи").wait_for(); p.screenshot(path=S+"01_overview_empty.png", full_page=True)
    p.get_by_role("button", name="Платёжный календарь").click()
    p.get_by_text(P["number"]).first.wait_for(); p.screenshot(path=S+"02_calendar.png", full_page=True)
    rows=p.locator("tbody tr").filter(has_text=P["number"]); assert rows.count()==3, rows.count()
    # ликвидность: счёт
    p.get_by_role("button", name="Ликвидность и валюта").click()
    p.get_by_text("Финансовых счетов нет").wait_for(); p.screenshot(path=S+"03_liquidity_empty.png", full_page=True)
    p.locator(".section-head").filter(has_text="Счета и остатки").get_by_role("button").click()
    sel=p.locator("label:has-text('Банковский профиль') select"); sel.wait_for(); sel.select_option(index=1)
    p.get_by_role("button", name="Создать счёт").click()
    p.get_by_text("остаток не подтверждён").first.wait_for()
    p.get_by_role("button", name="Подтвердить остаток").click()
    p.locator("label:has-text('Сумма, RUB') input").fill("8000000")
    p.locator("label:has-text('Основание') input").fill("выписка за 09.11")
    p.get_by_role("button", name="Подтвердить", exact=True).click()
    p.get_by_text("8 000 000,00 RUB").first.wait_for()
    # операция
    p.locator(".section-head").filter(has_text="Банковские операции").get_by_role("button", name="Операция").click()
    p.locator("label:has-text('Сумма, RUB') input").fill("3000000")
    p.locator("label:has-text('Контрагент') input").fill("АО «Алданзолото»")
    p.locator("label:has-text('Назначение') input").fill("аванс по договору "+P["number"])
    p.locator("label:has-text('Банковский №') input").fill("п/п 4512")
    p.get_by_role("button", name="Сохранить черновик").click()
    p.get_by_role("button", name="Провести").wait_for(); p.screenshot(path=S+"04_tx_draft.png", full_page=True)
    p.get_by_role("button", name="Провести").click()
    p.get_by_role("button", name="Сторнировать").wait_for()
    p.get_by_text("11 000 000,00 RUB").first.wait_for()   # остаток счёта обновился
    p.get_by_role("button", name="Разнести на обязательства").click()
    p.get_by_placeholder("🔍 найти строку: договор, сделка, контрагент").fill(P["number"]); p.wait_for_timeout(800)
    adds=p.get_by_role("button", name="Добавить"); adds.first.wait_for(); adds.nth(0).click(); adds.nth(1).click()
    amt=p.locator("table input[inputmode=decimal]"); expect(amt).to_have_count(2)
    amt.nth(0).fill("2500000"); amt.nth(1).fill("600000")
    p.get_by_text("больше свободного остатка").wait_for(); p.screenshot(path=S+"05_alloc_over.png", full_page=True)
    assert p.get_by_role("button", name="Разнести", exact=True).is_disabled()
    amt.nth(1).fill("500000"); p.get_by_role("button", name="Разнести", exact=True).click()
    p.get_by_role("button", name="Подтвердить", exact=True).first.wait_for()
    p.screenshot(path=S+"06_alloc_provisional.png", full_page=True)
    for _ in range(2): p.get_by_role("button", name="Подтвердить", exact=True).first.click(); p.wait_for_timeout(600)
    expect(p.get_by_text("подтверждено")).to_have_count(2)
    # календарь: статусы и карточка
    p.get_by_role("button", name="Платёжный календарь").click()
    p.locator("td .badge", has_text="частично оплачено").first.wait_for()
    p.locator("tbody tr").filter(has_text="частично оплачено").first.click()
    p.get_by_role("button", name="Объяснить цифру").click(); p.get_by_text("Учтено").wait_for()
    p.screenshot(path=S+"07_obligation_card.png", full_page=True)
    expect(p.locator("td > .badge", has_text=re.compile("^оплачено$"))).to_have_count(1)
    # ссылка на договор
    p.get_by_role("link", name=re.compile("договор "+P["number"])).click(); p.wait_for_url(re.compile("/legal")); p.get_by_role("button", name=re.compile("Оплата")).first.click(); p.get_by_text("Оплаты в «Финансах»").first.wait_for()
    p.screenshot(path=S+"08_contract_link.png", full_page=True)
    p.get_by_text("Оплаты в «Финансах»").first.click(); p.wait_for_url(re.compile("contract_id=")); p.get_by_text("отбор по договору").wait_for()
    # сторно в UI
    p.get_by_role("button", name="Ликвидность и валюта").click(); p.locator("tbody tr").filter(has_text="п/п 4512").first.click()
    p.get_by_role("button", name="Сторнировать").click(); p.locator("td .badge", has_text=re.compile("^сторно$")).first.wait_for()
    p.locator("td .badge", has_text="сторнирована").first.wait_for(); p.screenshot(path=S+"09_reversed.png", full_page=True)
    p.get_by_role("button", name="Обзор").click(); p.get_by_text("Ближайшие платежи").wait_for(); p.screenshot(path=S+"10_overview_after.png", full_page=True)
    # браузерное «назад» возвращает вкладку
    p.go_back(); p.get_by_text("Банковские операции").wait_for()
    # старая отметка оплаты этапа → «требует сверки» → счёт → проведение → подтверждение (D-117)
    p.goto(U+f"/deals/{P['deal_id']}?tab=execution"); row=p.locator("tr").filter(has_text="Аванс от клиента").first; row.wait_for()
    expect(p.get_by_role("link", name="Платежи сделки в «Финансах» →")).to_be_visible()
    row.locator("input[type=date]").last.fill("2026-11-06"); row.locator("input[type=number]").fill("4000000")
    row.get_by_role("button", name="отметить").click()
    p.get_by_text("в подтверждённые остатки она не входит").wait_for()
    badge=p.get_by_role("link", name="требует сверки в «Финансах»"); badge.wait_for(); p.screenshot(path=S+"14_execution_legacy_mark.png", full_page=True)
    badge.click(); p.wait_for_url(re.compile("tab=liquidity")); p.get_by_text("Сверка: счёт и банковский документ").wait_for()
    p.screenshot(path=S+"15_legacy_reconcile.png", full_page=True)
    box=p.locator(".card").filter(has_text="Сверка: счёт и банковский документ").last
    box.locator("select").select_option(index=1); box.locator("input").fill("п/п 7781"); box.get_by_role("button", name="Сохранить").click()
    p.get_by_role("button", name="Провести").wait_for(); p.wait_for_timeout(500); p.get_by_role("button", name="Провести").click()
    p.get_by_role("button", name="Сторнировать").wait_for()
    p.get_by_role("button", name="Подтвердить", exact=True).first.click(); p.wait_for_timeout(600)
    p.goto(U+f"/finance?tab=calendar&deal_id={P['deal_id']}"); p.get_by_text("отбор по сделке").wait_for()
    p.locator("td > .badge", has_text=re.compile("^оплачено$")).first.wait_for(); p.screenshot(path=S+"16_deal_calendar_paid.png", full_page=True)
    # источники этапа 2: заказ поставщику, партия (валюта не указана → конфликт), запчасти без даты
    p.goto(U+"/finance?tab=calendar&source=purchase_order"); p.get_by_text("ЗК-2026-0901").first.wait_for()
    expect(p.locator("tbody tr").filter(has_text="ЗК-2026-0901")).to_have_count(2); p.screenshot(path=S+"17_calendar_po.png", full_page=True)
    p.goto(U+"/finance?tab=calendar&source=batch&inactive=1"); p.get_by_text("брокер и СВХ").first.wait_for()
    p.locator("tbody tr").filter(has_text="фрахт").get_by_text("валюта не определена").first.wait_for()
    p.screenshot(path=S+"18_calendar_batch.png", full_page=True)
    p.goto(U+"/finance?tab=calendar"); p.get_by_text("Ожидается, срок не согласован").wait_for(); p.get_by_text("ЗЧ-2026-0042").wait_for()
    p.get_by_text("Ожидается, срок не согласован").scroll_into_view_if_needed(); p.screenshot(path=S+"19_undated_parts.png", full_page=True)
    p.get_by_role("link", name="ЗЧ-2026-0042").click(); p.wait_for_url(re.compile("/deals/parts/"))
    p.goto(U+"/finance"); p.get_by_text("Запчасти: заказано и не отгружено").wait_for(); p.screenshot(path=S+"20_overview_sources.png", full_page=True)
    # мобильная ширина
    m=ctx.new_page(); watch(m,"fd-mobile"); m.set_viewport_size({"width":390,"height":844}); m.goto(U+"/finance?tab=calendar"); m.get_by_text("АО «Алданзолото»").first.wait_for(); m.screenshot(path=S+"11_mobile_calendar.png", full_page=False)
    # КД: раздел по умолчанию закрыт (D-116) — пункта меню нет, на странице пояснение, запросов к API нет
    cd=as_user(b.new_context(viewport={"width":1440,"height":900}),"cd@example.com"); cd.goto(U+"/finance"); cd.get_by_text("доступен при праве").wait_for()
    assert cd.locator("header nav").get_by_text("Финансы").count()==0
    cd.screenshot(path=S+"12_cd_denied.png")
    # менеджер: пункта нет, страница — сообщение
    mg=as_user(b.new_context(viewport={"width":1440,"height":900}),"manager@example.com"); mg.goto(U+"/finance"); mg.get_by_text("доступен при праве").wait_for()
    assert mg.locator("header nav").get_by_text("Финансы").count()==0
    mg.screenshot(path=S+"13_manager_denied.png")
    b.close()
print("UI scenario OK"); print("\n".join(issues) or "no console errors / API errors")
