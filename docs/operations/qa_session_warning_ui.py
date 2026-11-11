"""Браузерная проверка D-041: «Запомнить логин» и предупреждение за 5 минут до выхода.
Запуск: backend на синтетической БД (:8000), vite (:5173); python3 docs/operations/qa_session_warning_ui.py"""
import subprocess
from playwright.sync_api import expect, sync_playwright

U, DB = "http://127.0.0.1:5173", "postgresql://gsho@localhost:5432/gsho"
EMAIL, PW = "manager@example.com", "berghaus-demo-2026"
SHOTS = "docs/operations/screens"
issues = []


def sql(q):
    subprocess.run(["psql", DB, "-qc", q], check=True)


with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    for w in (1440, 390):
        c = b.new_context(viewport={"width": w, "height": 900}, locale="ru-RU")
        p = c.new_page()
        p.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and issues.append(m.text[:160]))
        p.on("pageerror", lambda e: issues.append(str(e)))
        p.goto(U + "/login")
        expect(p.get_by_text("Запомнить логин")).to_be_visible()
        p.get_by_label("Логин", exact=True).fill(EMAIL)
        p.locator("input[name=password]").fill(PW)
        p.get_by_role("button", name="Войти в систему").click()
        p.get_by_role("link", name="Рабочее место").wait_for()
        p.wait_for_load_state("networkidle")
        p.wait_for_timeout(1500)
        # бездействие почти 8 ч: до выхода ~3 минуты
        sql(f"update auth_sessions set last_seen_at = now() - interval '7 hours 57 minutes', idle_expires_at = now() + interval '3 minutes' "
            f"where user_id = (select id from users where email='{EMAIL}') and revoked_at is null and auth_method='password'")
        # без действий: предупреждение должно появиться при следующем минутном опросе (перезагрузка была бы активностью)
        dlg = p.get_by_role("dialog", name="Скоро потребуется войти заново")
        dlg.wait_for(timeout=75_000)
        expect(dlg.get_by_role("timer")).to_contain_text("Осталось 2:")
        p.screenshot(path=f"{SHOTS}/s08_session_warning_{w}.png")
        dlg.get_by_role("button", name="Продолжить работу").click()
        expect(dlg).to_have_count(0)
        # выход, логин запомнен, курсор сразу в пароле
        p.get_by_role("button", name="Выйти").click()
        p.get_by_role("button", name="Войти в систему").wait_for()
        expect(p.get_by_label("Логин", exact=True)).to_have_value(EMAIL)
        expect(p.locator("input[name=password]")).to_be_focused()
        # снятая галочка забывает логин
        p.get_by_text("Запомнить логин").click()
        p.locator("input[name=password]").fill(PW)
        p.get_by_role("button", name="Войти в систему").click()
        p.get_by_role("link", name="Рабочее место").wait_for()
        p.get_by_role("button", name="Выйти").click()
        p.get_by_role("button", name="Войти в систему").wait_for()
        expect(p.get_by_label("Логин", exact=True)).to_have_value("")
        c.close()
    b.close()
print("ISSUES:", issues or "none")
