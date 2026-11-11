"""Браузерная проверка безопасности (этап 2; 12 SEC/OFF, 10 §6–7, 4 ширины). Синтетические сотрудники из qa_security_prep.py.

    python3 docs/operations/qa_security_ui.py /tmp/qa_sec.json
"""
import base64
import hashlib
import hmac
import json
import struct
import sys
import time

from playwright.sync_api import expect, sync_playwright

U = "http://127.0.0.1:5173"
P = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "/tmp/qa_sec.json"))
PW = P["pw"]
SHOTS = "docs/operations/screens"
ADMIN, ADMIN_PW = "admin@example.com", "berghaus-demo-2026"
issues: list[str] = []
EXPECTED = ("/api/auth/me", "/api/auth/mfa/verify", "/api/auth/mfa/recovery", "/api/auth/step-up")   # 401 до входа и неверные коды — проверяем сознательно


def totp(secret, shift=0):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    h = hmac.new(key, struct.pack(">Q", int(time.time() // 30) + shift), hashlib.sha1).digest()
    o = h[-1] & 15
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000).zfill(6)


def ctx(b, width=1440, dev=None):
    c = b.new_context(viewport={"width": width, "height": 900}, locale="ru-RU", timezone_id="Europe/Moscow")
    if dev:
        assert c.request.get(U + "/api/auth/me", headers={"X-Dev-User": dev}).ok
    p = c.new_page()
    tag = f"{dev or 'anon'}@{width}"
    p.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and issues.append(f"[{tag}] console: {m.text[:200]}"))
    p.on("pageerror", lambda e: issues.append(f"[{tag}] pageerror: {e}"))
    def resp(r):
        if r.status >= 400 and "/api/" in r.url:
            body = ""
            try:
                body = r.text()[:120]
            except Exception:  # noqa: BLE001
                pass
            if any(r.url.split("?")[0].endswith(e) for e in EXPECTED) or any(k in body for k in ("step_up_required", "password_policy", "Учётная запись отключена")):
                return
            issues.append(f"[{tag}] {r.status} {r.request.method} {r.url} {body}")
    p.on("response", resp)
    return p


def no_overflow(p, tag):
    over = p.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    if over > 1:
        issues.append(f"{tag}: горизонтальная прокрутка {over}px")


def password_login(p, email, pw=PW):
    p.goto(U + "/login")
    p.get_by_label("Логин").fill(email)
    p.locator("input[name=password]").fill(pw)
    p.get_by_role("button", name="Войти в систему").click()


with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])

    # 1. Вход с кодом: пароль → второй шаг → неверный код → верный код → рабочее место
    p = ctx(b)
    password_login(p, P["mfa"]["email"])
    p.get_by_text("Код из приложения").wait_for()
    p.screenshot(path=f"{SHOTS}/s01_login_code_1440.png")
    p.get_by_placeholder("000000").fill("000000")
    p.get_by_role("button", name="Подтвердить вход").click()
    p.get_by_text("Код не подошёл").wait_for()
    p.get_by_placeholder("000000").fill(totp(P["mfa"]["secret"], 1))
    p.get_by_role("button", name="Подтвердить вход").click()
    p.get_by_role("link", name="Рабочее место").wait_for()

    # 2. Мастер включения кода: пароль → QR и ключ → код → коды восстановления (без «Я сохранил» — нельзя закрыть)
    p = ctx(b)
    password_login(p, P["wizard"]["email"])
    p.get_by_role("link", name="Рабочее место").wait_for()
    p.goto(U + "/settings?s=security")
    p.get_by_role("heading", name="Вход с кодом из приложения выключен").wait_for()
    p.get_by_role("button", name="Включить").click()
    wz = p.get_by_role("dialog", name="Вход с кодом из приложения")
    wz.get_by_role("button", name="Начать").click()
    wz.get_by_label("Текущий пароль").fill(PW)
    wz.get_by_role("button", name="Дальше").click()
    p.locator(".sec-qr svg").wait_for()
    secret = p.locator(".sec-qr-row code").inner_text().replace(" ", "")
    p.screenshot(path=f"{SHOTS}/s02_wizard_qr_1440.png")
    wz.get_by_placeholder("000000").fill(totp(secret))
    wz.get_by_role("button", name="Включить", exact=True).click()
    p.get_by_text("Сохраните коды сейчас").wait_for()
    assert p.locator(".sec-codes li").count() == 10
    expect(p.get_by_role("button", name="Готово")).to_be_disabled()
    p.get_by_label("Я сохранил коды в надёжном месте").check()
    p.get_by_role("button", name="Готово").click()
    p.get_by_role("heading", name="Вход с кодом из приложения включён").wait_for()
    # сессии: второй вход и «Завершить все, кроме текущего»
    other = ctx(b)
    password_login(other, P["wizard"]["email"])
    other.get_by_placeholder("000000").fill(totp(secret, 1))
    other.get_by_role("button", name="Подтвердить вход").click()
    other.get_by_role("link", name="Рабочее место").wait_for()
    p.reload()
    p.get_by_text("этот браузер").wait_for()
    p.get_by_role("button", name="Завершить все, кроме текущего").click()
    p.get_by_role("dialog", name="Завершить другие входы?").get_by_role("button", name="Завершить").click()
    expect(p.locator(".card", has=p.get_by_role("heading", name="Активные входы")).locator("tbody tr")).to_have_count(1)
    # смена пароля: чек-лист и ошибка сервера
    p.get_by_label("Текущий пароль").fill(PW)
    p.get_by_label("Новый пароль", exact=True).fill("Password1234")
    p.get_by_label("Повтор нового пароля").fill("Password1234")
    p.get_by_role("button", name="Сменить пароль").click()
    p.get_by_text("не из списка распространённых паролей").wait_for()
    for w in (1440, 1280, 768, 390):
        p.set_viewport_size({"width": w, "height": 900})
        p.wait_for_timeout(200)
        no_overflow(p, f"security@{w}")
        p.screenshot(path=f"{SHOTS}/s03_security_{w}.png", full_page=True)

    # 3. Временный пароль: только экран смены, затем вся система
    p = ctx(b)
    password_login(p, P["temp"]["email"])
    p.get_by_role("heading", name="Смена временного пароля").wait_for()
    assert p.get_by_role("link", name="Сделки").count() == 0
    p.screenshot(path=f"{SHOTS}/s04_temp_gate_1440.png")
    p.get_by_label("Временный пароль").fill(PW)
    p.get_by_label("Новый пароль", exact=True).fill("Cedar-north-river-31")
    p.get_by_label("Повтор нового пароля").fill("Cedar-north-river-31")
    p.get_by_role("button", name="Сменить пароль").click()
    p.get_by_role("link", name="Сделки").wait_for()

    # 4. Администратор: отключение — подтверждение паролем (step-up), ввод ФИО, фактические количества
    for w in (1440, 390):
        a = ctx(b, w, dev=ADMIN)
        a.goto(U + "/settings?s=security_admin")
        a.get_by_role("heading", name="Безопасность сотрудников").wait_for()
        no_overflow(a, f"security_admin@{w}")
        a.screenshot(path=f"{SHOTS}/s05_admin_list_{w}.png", full_page=True)
    a.set_viewport_size({"width": 1440, "height": 900})
    row = a.locator("tr", has=a.get_by_text(P["target"]["email"]))
    row.get_by_role("button", name="Открыть").click()
    card = a.get_by_role("dialog", name=P["target"]["name"])
    card.get_by_role("button", name="Отключить доступ").click()
    dlg = a.get_by_role("dialog", name="Отключить доступ сотрудника")
    dlg.get_by_text("Открытых задач — пометить «передать другому»: 1").wait_for()
    expect(dlg.get_by_role("button", name="Отключить доступ")).to_be_disabled()
    dlg.get_by_label("Для подтверждения введите").fill(P["target"]["name"])
    a.screenshot(path=f"{SHOTS}/s06_offboard_modal_1440.png")
    dlg.get_by_role("button", name="Отключить доступ").click()
    su = a.get_by_role("dialog", name="Подтвердите действие")
    su.get_by_label("Ваш пароль").fill("wrong-password")
    su.get_by_role("button", name="Подтвердить").click()
    su.get_by_text("Пароль неверный").wait_for()
    su.get_by_label("Ваш пароль").fill(ADMIN_PW)
    su.get_by_role("button", name="Подтвердить").click()
    a.get_by_role("dialog", name="Доступ отключён").get_by_text("Задач помечено «передать другому»: 1").wait_for()
    a.screenshot(path=f"{SHOTS}/s07_offboard_result_1440.png")
    a.get_by_role("dialog", name="Доступ отключён").get_by_role("button", name="Готово").click()
    a.get_by_label("показать отключённых").check()
    a.locator("tr", has=a.get_by_text(P["target"]["email"])).get_by_text("отключён").wait_for()
    # вход отключённого — отказ
    t = ctx(b)
    password_login(t, P["target"]["email"])
    t.get_by_text("Учётная запись отключена").wait_for()

    # 5. Старый экран «Администрирование»: снятие «Активен» открывает тот же диалог, а не тихий PATCH
    a.goto(U + "/settings?s=admin")
    a.locator("tr", has=a.get_by_text(P["temp"]["email"])).get_by_label(f"Отключить доступ: {P['temp']['name']}").click()
    a.get_by_role("dialog", name="Отключить доступ сотрудника").wait_for()
    a.keyboard.press("Escape")
    expect(a.get_by_role("dialog", name="Отключить доступ сотрудника")).to_have_count(0)
    b.close()

print("ISSUES:", issues or "none")
