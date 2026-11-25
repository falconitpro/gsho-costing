"""Пакет H4/H5 мастер-ТЗ UX/UI: сквозная проверка «Нового вида» на всех ширинах (1440/1280/1024/768/390/360).

Для каждого экрана и ширины проверяет:
  * переполнение страницы по горизонтали (ширина документа больше окна) — ошибка;
  * ошибки JavaScript на странице — ошибка;
  * элементы управления без доступного имени (кнопка/ссылка без текста и aria-label/title, поле без подписи) — ошибка;
  * текст с контрастом ниже 4.5:1 (3:1 для крупного) — предупреждение;
  * на телефоне (≤390) кнопки и ссылки ниже 24 px по высоте — предупреждение;
  * видимость фокуса: у первых элементов, получающих фокус клавишей Tab, есть outline или тень — предупреждение.

Запуск на стенде разработки (APP_ENV=dev, X-Dev-User):
  python3 scripts/e2e/a11y_sweep.py <папка> [base_url] [browser] [deal_id]
Опция WIDTHS=1440,390 — только указанные ширины (для быстрой проверки в CI).
Пишет <папка>/report.json и <папка>/summary.txt; код выхода 1, если есть ошибки (не предупреждения).
"""
import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/a11y"
BASE = (sys.argv[2] if len(sys.argv) > 2 else "http://localhost:5173").rstrip("/")
BROWSER = sys.argv[3] if len(sys.argv) > 3 else None
DEAL = sys.argv[4] if len(sys.argv) > 4 else ""
ALL = [(1440, 900), (1280, 800), (1024, 768), (768, 1024), (390, 844), (360, 780)]
_only = {int(x) for x in os.environ.get("WIDTHS", "").split(",") if x.strip()}
WIDTHS = [w for w in ALL if not _only or w[0] in _only]

ROUTES = {
    "gd@example.com": [
        "/", "/deals", f"/deals/{DEAL}" if DEAL else "", "/tasks", "/assignments?scope=all&status=any", "/board", "/summary",
        "/finance", "/finance?tab=calendar", "/legal", "/legal?tab=claims", "/legal?tab=poa", "/office",
        "/catalog", "/catalog?s=equipment", "/catalog?s=parts", "/supply",
        "/settings", "/settings?s=team", "/settings?s=access", "/settings?s=admin", "/settings?s=security",
        "/settings?s=security_admin", "/settings?s=access_review", "/settings?s=security_audit", "/settings?s=backup", "/settings?s=app_errors",
        "/support", "/support/admin",
    ],
    "manager@example.com": ["/", "/deals", "/tasks", "/catalog?s=parts", "/support"],
    "logist@example.com": ["/", "/supply"],
    "office@example.com": ["/office"],
    "lawyer@example.com": ["/legal"],
}

CHECK_JS = r"""
() => {
  const W = document.documentElement.clientWidth;
  const res = { overflow: Math.max(0, document.documentElement.scrollWidth - W), unnamed: [], lowContrast: [], smallTargets: [] };
  const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none" && !el.closest("[aria-hidden='true'], [hidden], .ai-drawer:not(.open)"); };
  const label = (el) => (el.getAttribute("aria-label") || el.getAttribute("title") || "").trim();
  const desc = (el) => `${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}${el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : ""}`;
  // элементы управления без имени
  document.querySelectorAll("button, a[href], [role=button], [role=tab]").forEach((el) => {
    if (!vis(el)) return;
    const txt = (el.innerText || el.textContent || "").trim() || label(el) || (el.getAttribute("aria-labelledby") ? "x" : "") || (el.querySelector("img[alt]:not([alt=''])") ? "x" : "");
    if (!txt) res.unnamed.push(desc(el));
  });
  document.querySelectorAll("input:not([type=hidden]), select, textarea").forEach((el) => {
    if (!vis(el)) return;
    const ok = label(el) || el.getAttribute("placeholder") || el.getAttribute("aria-labelledby") || el.closest("label") || (el.id && document.querySelector(`label[for="${el.id}"]`));
    if (!ok) res.unnamed.push(desc(el) + "[" + (el.getAttribute("type") || el.tagName.toLowerCase()) + "]");
  });
  // контраст текста
  const parse = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(",").map((x) => parseFloat(x)); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const bgOf = (el) => { for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0.5) return c; if (getComputedStyle(e).backgroundImage !== "none") return null; } return { r: 255, g: 255, b: 255, a: 1 }; };
  const seen = new Set();
  document.querySelectorAll("body *").forEach((el) => {
    if (res.lowContrast.length >= 15) return;
    const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 1);
    if (!own || !vis(el) || el.closest("[disabled], .disabled, option")) return;
    const cs = getComputedStyle(el); if (parseFloat(cs.opacity) < 0.9) return;
    const fg = parse(cs.color), bg = bgOf(el); if (!fg || !bg || fg.a < 0.9) return;
    const L1 = lum(fg), L2 = lum(bg), ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const big = parseFloat(cs.fontSize) >= 24 || (parseFloat(cs.fontSize) >= 18.6 && parseInt(cs.fontWeight) >= 700);
    if (ratio < (big ? 3 : 4.5)) { const k = desc(el) + " " + cs.color + "/" + `rgb(${bg.r},${bg.g},${bg.b})`; if (!seen.has(k)) { seen.add(k); res.lowContrast.push(`${k} ${ratio.toFixed(2)} «${el.innerText.trim().slice(0, 30)}»`); } }
  });
  if (W <= 390) document.querySelectorAll("button, a[href].btn, [role=tab]").forEach((el) => {
    // столбики графиков: высота — это данные; те же суммы доступны таблицей и ссылками под графиком
    if (!vis(el) || res.smallTargets.length >= 10 || el.matches(".pf-bar")) return; const r = el.getBoundingClientRect();
    if (r.height < 24) res.smallTargets.push(`${desc(el)} ${Math.round(r.width)}×${Math.round(r.height)} «${(el.innerText || "").trim().slice(0, 20)}»`);
  });
  return res;
}
"""

FOCUS_JS = r"""
() => { const el = document.activeElement; if (!el || el === document.body) return null;
  const cs = getComputedStyle(el); const has = (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0) || (cs.boxShadow && cs.boxShadow !== "none");
  return has ? "" : `${el.tagName.toLowerCase()}.${(typeof el.className === "string" ? el.className : "").trim().split(/\s+/).slice(0, 2).join(".")} «${(el.innerText || el.getAttribute("aria-label") || "").trim().slice(0, 20)}»`; }
"""


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    report, errors, warnings = [], 0, 0
    t_start = time.time()
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=BROWSER, args=["--no-sandbox"])
        for user, routes in ROUTES.items():
            for w, h in WIDTHS:
                ctx = b.new_context(viewport={"width": w, "height": h}, extra_http_headers={"X-Dev-User": user}, timezone_id="Europe/Moscow", locale="ru-RU")
                ctx.add_init_script("localStorage.setItem('gsho.newUi', '1')")
                pg = ctx.new_page()
                errs: list[str] = []
                pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
                for path in routes:
                    if not path:
                        continue
                    errs.clear()
                    pg.goto(BASE + path)
                    try:
                        pg.wait_for_load_state("networkidle", timeout=20000)
                    except Exception:  # noqa: BLE001
                        pass
                    pg.wait_for_timeout(400)
                    r = pg.evaluate(CHECK_JS)
                    nofocus = []
                    if w in (1440, 390):
                        pg.mouse.click(1, 1)
                        for _ in range(8):
                            pg.keyboard.press("Tab")
                            f = pg.evaluate(FOCUS_JS)
                            if f:
                                nofocus.append(f)
                    row = {"user": user, "path": path, "width": w, "overflow": r["overflow"], "page_errors": list(errs), "unnamed": r["unnamed"][:10],
                           "low_contrast": r["lowContrast"], "small_targets": r["smallTargets"], "no_focus": sorted(set(nofocus))}
                    bad = (r["overflow"] > 1) + bool(errs) + bool(r["unnamed"])
                    warn = bool(r["lowContrast"]) + bool(r["smallTargets"]) + bool(nofocus)
                    errors += bad
                    warnings += warn
                    report.append(row)
                ctx.close()
        b.close()
    json.dump(report, open(os.path.join(OUT, "report.json"), "w"), ensure_ascii=False, indent=1)
    lines = [f"экранов×ширин: {len(report)}, ошибок: {errors}, предупреждений: {warnings}, время: {time.time() - t_start:.0f} с"]
    for r in report:
        parts = []
        if r["overflow"] > 1:
            parts.append(f"ПЕРЕПОЛНЕНИЕ +{r['overflow']}px")
        if r["page_errors"]:
            parts.append(f"ОШИБКИ JS {r['page_errors'][:2]}")
        if r["unnamed"]:
            parts.append(f"БЕЗ ИМЕНИ {r['unnamed'][:5]}")
        if r["low_contrast"]:
            parts.append(f"контраст {r['low_contrast'][:3]}")
        if r["small_targets"]:
            parts.append(f"мелкие цели {r['small_targets'][:3]}")
        if r["no_focus"]:
            parts.append(f"фокус не виден {r['no_focus'][:3]}")
        if parts:
            lines.append(f"{r['width']:>4} {r['user'].split('@')[0]:>8} {r['path']}: " + "; ".join(parts))
    open(os.path.join(OUT, "summary.txt"), "w").write("\n".join(lines) + "\n")
    print(lines[0])
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
