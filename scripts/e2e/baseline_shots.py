"""Этап 0 UX/UI (мастер-ТЗ A–H, §2.1, §7): снимки «как есть» основных экранов на 1440/1280/1024/768/390/360 и метрики переполнения.

Запуск на стенде разработки (APP_ENV=dev, X-Dev-User):
  python3 scripts/e2e/baseline_shots.py <папка> [base_url] [browser] [deal_id]
Пишет: <папка>/<роль>/<экран>-<ширина>.png и <папка>/metrics.json — ширина документа против окна, ошибки страницы, время загрузки.
Опция UI=new — снимки нового вида (переключатель «Новый вид», localStorage gsho.newUi=1) для сравнения «было / стало».
"""
import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/baseline"
BASE = (sys.argv[2] if len(sys.argv) > 2 else "http://localhost:5173").rstrip("/")
BROWSER = sys.argv[3] if len(sys.argv) > 3 else None
DEAL = sys.argv[4] if len(sys.argv) > 4 else ""
NEW = os.environ.get("UI") == "new"
WIDTHS = [(1440, 900), (1280, 800), (1024, 768), (768, 1024), (390, 844), (360, 780)]

SCREENS = {   # роль → [(код, путь)]
    "gd@example.com": [("workplace", "/"), ("deals", "/deals"), ("deal", f"/deals/{DEAL}" if DEAL else ""), ("tasks", "/tasks"),
                       ("assignments", "/assignments?scope=all&status=any"), ("finance", "/finance"), ("board", "/board"), ("summary", "/summary"),
                       ("legal", "/legal"), ("office", "/office"), ("catalog", "/catalog"), ("supply", "/supply"), ("settings", "/settings"),
                       ("support", "/support")],
    "manager@example.com": [("workplace", "/"), ("deals", "/deals"), ("tasks", "/tasks")],
    "logist@example.com": [("workplace", "/")],
    "fd@example.com": [("finance", "/finance")],
    "lawyer@example.com": [("legal", "/legal")],
    "office@example.com": [("office", "/office")],
    "observer@example.com": [("workplace", "/")],
}
ROLE = {"gd@example.com": "zgd", "manager@example.com": "manager", "logist@example.com": "logist", "fd@example.com": "fd",
        "lawyer@example.com": "lawyer", "office@example.com": "clerk", "observer@example.com": "observer"}
ALL_WIDTHS_FOR = {"gd@example.com", "manager@example.com"}


def main():
    metrics = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=BROWSER, args=["--no-sandbox"])
        for user, screens in SCREENS.items():
            widths = WIDTHS if user in ALL_WIDTHS_FOR else [WIDTHS[0], WIDTHS[4]]
            for w, h in widths:
                ctx = b.new_context(viewport={"width": w, "height": h}, extra_http_headers={"X-Dev-User": user}, timezone_id="Europe/Moscow", locale="ru-RU")
                pg = ctx.new_page()
                errs: list[str] = []
                pg.on("pageerror", lambda e: errs.append(str(e)))
                pg.goto(BASE + "/")
                pg.evaluate(f"localStorage.setItem('gsho.newUi', '{'1' if NEW else '0'}'); localStorage.setItem('gsho.gdView', 'classic')")
                for code, path in screens:
                    if not path:
                        continue
                    errs.clear()
                    t0 = time.time()
                    pg.goto(BASE + path)
                    try:
                        pg.wait_for_load_state("networkidle", timeout=20000)
                    except Exception:  # noqa: BLE001
                        pass
                    pg.wait_for_timeout(600)
                    sw, iw = pg.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
                    d = os.path.join(OUT, ROLE[user])
                    os.makedirs(d, exist_ok=True)
                    f = f"{code}-{w}.png"
                    pg.screenshot(path=os.path.join(d, f))
                    metrics.append({"role": ROLE[user], "screen": code, "path": path.split("?")[0], "width": w, "doc_width": sw,
                                    "overflow_px": max(0, sw - iw), "load_s": round(time.time() - t0, 2), "page_errors": errs[:3],
                                    "file": f"{ROLE[user]}/{f}"})
                    print(f"{ROLE[user]:9} {code:12} {w:5} doc={sw:5} {'ПЕРЕПОЛНЕНИЕ +' + str(sw - iw) if sw > iw else ''}", flush=True)
                ctx.close()
        b.close()
    json.dump(metrics, open(os.path.join(OUT, "metrics.json"), "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
