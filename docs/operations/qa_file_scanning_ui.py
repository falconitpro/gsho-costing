"""Браузерная проверка «Проверка файлов» (этап 3б). Синтетическая БД; backend :8000 с FILE_SCAN=clamav и clamd, vite :5173.
    python3 docs/operations/qa_file_scanning_ui.py
Загрузка через API: обычный PDF и тестовая строка EICAR (собирается в памяти)."""
from playwright.sync_api import expect, sync_playwright

U, API, SHOTS = "http://127.0.0.1:5173", "http://127.0.0.1:8000", "docs/operations/screens"
GD, MGR = "evgeniy@dobrov.pro", "manager@example.com"
EICAR = ("X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-" + "TEST-FILE!$H+H*").encode()
issues = []

with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, locale="ru-RU", timezone_id="Europe/Moscow")
    rq = ctx.request
    H = lambda e: {"X-Dev-User": e}   # noqa: E731
    cl = rq.get(API + "/api/refs/clients", headers=H(MGR)).json()[0]
    d = rq.post(API + "/api/deals", headers=H(MGR), data={"client_id": cl["id"], "site": "qa"}).json()["id"]
    ok = rq.post(API + f"/api/deals/{d}/documents", headers=H(MGR), multipart={"file": {"name": "ТЗ.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 ok"}, "category": "client_spec"})
    bad = rq.post(API + f"/api/deals/{d}/documents", headers=H(MGR), multipart={"file": {"name": "счёт.pdf", "mimeType": "application/pdf", "buffer": b"%PDF " + EICAR}, "category": "client_spec"})
    assert ok.status == 201 and bad.status == 422, (ok.status, bad.status, bad.text())
    print("отказ:", bad.json()["detail"]["message"])
    rq.get(API + "/api/auth/me", headers=H(GD))
    p = ctx.new_page()
    p.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and issues.append(m.text[:160]))
    p.on("pageerror", lambda e: issues.append(str(e)))
    p.goto(U + "/settings?s=security_audit")
    p.get_by_role("heading", name="Журнал и предупреждения").wait_for()
    expect(p.locator(".aud-table tbody tr", has=p.get_by_text("Антивирус нашёл угрозу в файле"))).to_contain_text("критическая")
    p.screenshot(path=f"{SHOTS}/f01_alert_infected_1440.png", full_page=True)
    p.get_by_role("tab", name="Проверка файлов").click()
    expect(p.get_by_text("Антивирус работает")).to_be_visible()
    rows = p.locator(".aud-table tbody tr")
    expect(rows.filter(has_text="счёт.pdf")).to_contain_text("угроза")
    expect(rows.filter(has_text="ТЗ.pdf")).to_contain_text("чистый")
    p.screenshot(path=f"{SHOTS}/f02_scanner_1440.png", full_page=True)
    for w in (768, 390):
        p.set_viewport_size({"width": w, "height": 900}); p.wait_for_timeout(250)
        p.screenshot(path=f"{SHOTS}/f03_scanner_{w}.png", full_page=True)
        if p.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"):
            issues.append(f"horizontal page overflow at {w}")
    print("ISSUES:", issues or "none")
    b.close()
