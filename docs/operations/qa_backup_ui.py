"""Браузерная проверка «Настройки → Резервные копии» (12 BKP-09, 10 §8–9) на синтетическом статусе.

Запуск: backend на синтетической БД с BACKUP_STATUS_DIR=/tmp/qa-status, vite на :5173; затем
    python3 docs/operations/qa_backup_ui.py
Состояния подкладываются файлами в каталог статуса; история в БД очищается между сценариями (только синтетическая БД).
"""
import json
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone

from playwright.sync_api import sync_playwright

U = "http://127.0.0.1:5173"
SD = os.environ.get("QA_STATUS_DIR", "/tmp/qa-status")
DB = os.environ.get("QA_PSQL", "postgresql://gsho@localhost:5432/gsho")
SHOTS = os.environ.get("QA_SHOTS", "docs/operations/screens")
GD, ADMIN, MGR = "evgeniy@dobrov.pro", "admin@example.com", "manager@example.com"
WIDTHS = [1440, 1280, 768, 390]
issues: list[str] = []
os.makedirs(SHOTS, exist_ok=True)


def iso(d):
    return d.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(status="success", hours_ago=0.2, **kw):
    st = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    d = {"schema": 1, "run_key": str(uuid.uuid4()), "reason": kw.pop("reason", "daily"), "status": status, "started_at": iso(st),
         "finished_at": iso(st + timedelta(minutes=4)) if status != "running" else None,
         "snapshot_id": uuid.uuid4().hex[:16] if status == "success" else None, "git_commit": "740b25a898663fcedd3ab2e8a131830c64ac971f",
         "schema_revision": "013_backup_operations", "storage_provider": "local", "database_bytes": 48_312_118, "files_bytes": 2_734_118_912,
         "files_count": 1834, "manifest_sha256": "0" * 64, "check_status": "ok" if status == "success" else None, "checked_at": None,
         "retention_status": "ok" if status == "success" else None, "error_code": None, "error_summary": None}
    d.update(kw)
    return d


def put(last, runs):
    subprocess.run(["psql", DB, "-qc", "delete from backup_runs; delete from integration_log where system='backup'; delete from notifications where event='backup';"], check=True)
    for f in os.listdir(SD):
        p = os.path.join(SD, f)
        if os.path.isfile(p):
            os.remove(p)
    os.makedirs(os.path.join(SD, "runs"), exist_ok=True)
    for f in os.listdir(os.path.join(SD, "runs")):
        os.remove(os.path.join(SD, "runs", f))
    for r in runs:
        with open(os.path.join(SD, "runs", f"{r['started_at'].replace(':', '')}_{r['run_key']}.json"), "w") as fh:
            json.dump(r, fh)
    if last is not None:
        with open(os.path.join(SD, "last-run.json"), "w") as fh:
            json.dump(last, fh)


def page(b, email, width=1440):
    ctx = b.new_context(viewport={"width": width, "height": 900}, locale="ru-RU", timezone_id="Europe/Moscow")
    assert ctx.request.get(U + "/api/auth/me", headers={"X-Dev-User": email}).ok   # dev-вход: cookie сессии в контексте
    p = ctx.new_page()
    p.on("console", lambda m: m.type == "error" and issues.append(f"[{email}@{width}] console: {m.text[:200]}"))
    p.on("response", lambda r: (r.status >= 400 and "/api/" in r.url and not r.url.endswith("/api/auth/me")) and issues.append(f"[{email}@{width}] {r.status} {r.request.method} {r.url}"))
    p.on("pageerror", lambda e: issues.append(f"[{email}@{width}] pageerror: {e}"))
    return p


def no_hscroll(p, tag):
    over = p.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    if over > 1:
        issues.append(f"{tag}: горизонтальная прокрутка страницы {over}px")
    # текст не вылезает за свои блоки (кроме таблицы истории — она прокручивается внутри карточки)
    clipped = p.evaluate("""[...document.querySelectorAll('.backup-status .alert, .backup-status .kpi, .backup-cmd')]
        .filter(e => e.scrollWidth - e.clientWidth > 1).length""")
    if clipped:
        issues.append(f"{tag}: обрезан текст в {clipped} блоках")


with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])

    # 1. Не настроено
    put(None, [])
    p = page(b, ADMIN)
    p.goto(U + "/settings?s=backup")
    p.get_by_text("Копирование в Яндекс ещё не настроено").wait_for()
    assert p.get_by_text("История запусков").count() == 0
    p.screenshot(path=f"{SHOTS}/b01_not_configured.png")

    # 2. В порядке — четыре ширины
    hist = [run(hours_ago=24 * d + 0.2) for d in range(6, 0, -1)] + [run(reason="pre_deploy", hours_ago=3)]
    last = run()
    put(last, hist + [last])
    for w in WIDTHS:
        p = page(b, GD, w)
        p.goto(U + "/settings?s=backup")
        p.get_by_text("В порядке").wait_for()
        p.get_by_text("История запусков").wait_for()
        assert p.locator(".backup-table tbody tr").count() == 8
        assert p.get_by_text(last["snapshot_id"][:8]).count() >= 1
        no_hscroll(p, f"ok@{w}")
        p.screenshot(path=f"{SHOTS}/b02_ok_{w}.png", full_page=True)
    # копирование команды: кнопка реально работает (без мёртвых CTA)
    p = page(b, GD)
    p.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=U)
    p.goto(U + "/settings?s=backup")
    p.get_by_role("button", name="Скопировать команду").click()
    p.get_by_text("Скопировано").wait_for()
    assert p.evaluate("navigator.clipboard.readText()") == "sudo systemctl start gsho-backup.service"
    p.get_by_role("button", name="Обновить").click()
    p.get_by_text("В порядке").wait_for()

    # 3. Ошибка + уведомление в колокольчике ведёт на раздел
    bad = run(status="failed", error_code="restic_backup_failed", error_summary="шаг restic_backup_failed, код выхода 1")
    put(bad, hist + [bad])
    p = page(b, ADMIN)
    p.goto(U + "/settings?s=backup")
    p.get_by_text("Последняя копия не создана.").wait_for()
    p.get_by_text("Не удалось отправить копию в Yandex Object Storage").first.wait_for()
    p.screenshot(path=f"{SHOTS}/b03_failed_1440.png", full_page=True)
    p.goto(U + "/deals")
    p.locator("[aria-label*='ведомлен'], .bell, button:has-text('🔔')").first.click()
    p.get_by_text("Резервная копия не создана").first.click()
    p.wait_for_url("**/settings?s=backup")
    p.get_by_text("Последняя копия не создана.").wait_for()
    p = page(b, ADMIN, 390)
    p.goto(U + "/settings?s=backup")
    p.get_by_text("Последняя копия не создана.").wait_for()
    no_hscroll(p, "failed@390")
    p.screenshot(path=f"{SHOTS}/b03_failed_390.png", full_page=True)

    # 4. Нет свежей копии
    old = run(hours_ago=31)
    put(old, [old])
    p = page(b, GD, 768)
    p.goto(U + "/settings?s=backup")
    p.get_by_text("Нет успешной копии дольше 26 ч.").wait_for()
    p.screenshot(path=f"{SHOTS}/b04_stale_768.png", full_page=True)

    # 5. Роль без права: раздела нет, запросов к API статуса нет
    p = page(b, MGR)
    p.goto(U + "/settings?s=backup")
    p.get_by_role("heading", name="Настройки").wait_for()
    p.wait_for_timeout(800)
    assert p.get_by_role("button", name="Резервные копии").count() == 0
    assert p.get_by_text("Зашифрованные копии базы").count() == 0
    b.close()

print("ISSUES:", issues or "none")
