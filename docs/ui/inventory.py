"""Опись элементов интерфейса из кода и сверка с базовой версией. usage: inventory.py <repo> <base_sha> <out.md>"""
import re, subprocess, sys, os, json
repo, base, out = sys.argv[1:4]
SRC = "frontend/src"
def sh(*a): return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True).stdout
def files(ref):
    return [f for f in sh("ls-tree", "-r", "--name-only", ref, SRC).split("\n") if f.endswith(".tsx")]
def text(ref, f): return sh("show", f"{ref}:{f}") if ref != "WORK" else open(os.path.join(repo, f), encoding="utf8").read()
RU = r"[А-Яа-яЁё]"
def clean(x): return re.sub(r"\s+", " ", re.sub(r"\{[^{}]*\}", "…", x)).strip(" :·")
def extract(src):
    d = {}
    d["api"] = sorted(set(f"{m.group(1).upper()} {re.sub(r'\$\{[^}]*\}', '{…}', m.group(2)).split('?')[0]}" for m in re.finditer(r"api\.(get|post|put|patch|del|upload)(?:<[^(]*?>)?\(\s*[`\"]([^`\"]+)", src)))
    ths = set(clean(m.group(1)) for m in re.finditer(r"<th\b[^>]*>([^<]{1,60})</th>", src))
    ths |= set(m.group(1) for m in re.finditer(r"\bth\(\s*\"[^\"]*\",\s*\"([^\"]+)\"", src))
    d["columns"] = sorted(x for x in ths if re.search(RU, x))
    btn = set(clean(m.group(1).split(">")[-1]) for m in re.finditer(r"<button\b[^>]*>([^<]{2,90})</button>", src, re.S))
    d["buttons"] = sorted(x for x in btn if re.search(RU, x) and x != "…")
    d["params"] = sorted(set(m.group(1) for m in re.finditer(r"\.get\(\"([a-zA-Z_]+)\"\)", src)))
    d["perms"] = sorted(set(m.group(1) for m in re.finditer(r"\bcan\(me,\s*\"([a-z_]+)\"", src)) | set(m.group(1) for m in re.finditer(r"perms\?\.\[?\.?\"?([a-z_]+)\"?\]?\s*\?\?", src)) | set(m.group(1) for m in re.finditer(r"perm\(\"([a-z_]+)\"", src)))
    d["headings"] = sorted(set(clean(m.group(1)) for m in re.finditer(r"<h[123]\b[^>]*>([^<]{2,70})", src)) - {"…"})
    d["storage"] = sorted(set(m.group(1) for m in re.finditer(r"(?:localStorage\.(?:get|set)Item|load(?:<[^(]*>)?|save)\(\s*\"([A-Za-z_.0-9]+)\"", src)))
    return d
bf = set(files(base)); wf = set(f for f in files("HEAD")) | set(os.path.join(SRC, r, x).replace(repo + "/", "") for r, _, xs in [(os.path.relpath(r, os.path.join(repo, SRC)), 0, xs) for r, _, xs in os.walk(os.path.join(repo, SRC))] for x in xs if x.endswith(".tsx"))
wf = set(os.path.normpath(f) for f in wf)
inv = {f: extract(text("WORK", f)) for f in sorted(wf)}
binv = {f: extract(text(base, f)) for f in sorted(bf)}
# сверка: каждый элемент базовой версии должен остаться (в том же файле или в любом другом)
allnow = {k: set() for k in ["api", "columns", "buttons", "params", "perms", "headings", "storage"]}
for d in inv.values():
    for k in allnow: allnow[k] |= set(d[k])
alltext = "\n".join(text("WORK", f) for f in sorted(wf))
missing = []; total = 0; bytext = 0
NOTE = {"frontend/src/pages/ApprovalScreen.tsx": "изменено в основной версии (PR #199, D-098: панель решения КД, три подвкладки) — не этой работой",
        "frontend/src/components/KpRevisions.tsx": "изменено в основной версии (PR #199, D-098: свёрнутая история КП) — не этой работой"}
for f, d in binv.items():
    for k in allnow:
        for x in d[k]:
            total += 1
            if x in allnow[k]: continue
            if k in ("buttons", "headings", "columns") and x.replace("…", "").strip() and x.replace("…", "").strip() in alltext: bytext += 1; continue   # подпись на месте, но собирается иначе (например, из списка)
            missing.append((f, k, x))
removed_files = sorted(bf - wf)
GROUPS = [("Финансы", ["pages/FinancePage.tsx", "pages/FinanceQuestionPage.tsx", "components/FinanceDebt.tsx", "components/FinanceEconomy.tsx", "components/FinanceExposure.tsx", "components/FinanceIntragroup.tsx", "components/FinanceReconciliation.tsx", "components/FinanceExportButton.tsx", "components/CashflowChart.tsx"], "/finance?tab=…, /finance/questions/:id"),
 ("Юридический блок и контрагенты", ["pages/LegalPage.tsx", "components/ContractsBlock.tsx", "components/ContractScans.tsx", "components/ClaimsBlock.tsx", "components/PoaBlock.tsx", "components/LegalTemplates.tsx", "components/TemplateEditor.tsx", "components/LegalRules.tsx", "components/LegalProfileBlock.tsx", "components/LegalSaleWizard.tsx", "pages/CounterpartiesPage.tsx", "pages/ClientsPage.tsx", "pages/FactoriesPage.tsx", "components/ClientRequisites.tsx", "components/NewSupplierInline.tsx"], "/legal?tab=…"),
 ("Снабжение, склад, сервис и запчасти", ["pages/PartsSetupPage.tsx", "components/PartsPurchase.tsx", "components/PartsBatches.tsx", "components/PartsWarehouse.tsx", "components/PartsClaims.tsx", "components/PartsReports.tsx", "components/PartsFleet.tsx", "components/PartsCatalog.tsx", "components/PartsRequests.tsx", "components/PartsRequestCreate.tsx"], "/supply?tab=…, /catalog?s=parts, /deals/parts/:id")]
PLACE = {"pages/FinancePage.tsx": "прежнее; добавлен путь", "pages/LegalPage.tsx": "прежнее; добавлены путь и обзор `/legal` (новый вид)", "pages/CounterpartiesPage.tsx": "прежнее; переключение — с историей",
 "pages/ClientsPage.tsx": "прежнее; добавлена прямая ссылка `&client=<id>`", "pages/PartsSetupPage.tsx": "прежнее; добавлены путь и обзор `/supply` (новый вид)", "components/PartsRequests.tsx": "прежнее; добавлен путь"}
NAMES = {"api": "Запросы API", "columns": "Колонки таблиц", "buttons": "Кнопки", "params": "Параметры адреса", "perms": "Права", "headings": "Заголовки блоков", "storage": "Сохранённые настройки (localStorage)"}
L = ["# Матрица сохранности: поэлементная опись и сверка с базовой версией", "",
     f"Составлена автоматически из кода (`{SRC}/**/*.tsx`) скриптом сверки. Базовая версия — `{base[:7]}` (до «матрёшки»), текущая — рабочая ветка.",
     "Выборка по шаблонам: запросы `api.*`, заголовки `<th>`, подписи `<button>`, параметры адреса, права `can()/perm()`, заголовки `<h1–h3>`, ключи localStorage. Подписи, собранные из выражений, показаны с «…»; элементы без текста (значки) в опись не попадают.", "",
     "## Сверка по всему интерфейсу", "",
     f"- файлов в базовой версии: {len(bf)}, в текущей: {len(wf)}; удалённых файлов: {len(removed_files)}" + (f" ({', '.join(removed_files)})" if removed_files else ""),
     f"- элементов базовой версии проверено: {total}",
     f"- найдено по тексту подписи (элемент строится из списка, а не записан отдельным тегом): {bytext}",
     f"- не найдено в текущей версии: {len(missing)}" + (" — все объяснены ниже" if missing and all(f in NOTE for f, _, _ in missing) else ""), ""]
if missing:
    L += ["| Файл (базовая версия) | Вид | Элемент | Пояснение |", "|---|---|---|---|"] + [f"| `{f.replace(SRC+'/','')}` | {NAMES[k]} | {x} | {NOTE.get(f, 'ТРЕБУЕТ РАЗБОРА')} |" for f, k, x in missing] + [""]
for title, fs, route in GROUPS:
    L += [f"## {title}", "", f"Маршруты: `{route}`.", "", "| Компонент | Изменён относительно базовой версии | Новое место / способ открытия |", "|---|---|---|"]
    for f in fs:
        p = f"{SRC}/{f}"
        if p not in inv: L.append(f"| `{f}` | нет в текущей версии | — |"); continue
        stat = sh("diff", "--numstat", base, "--", p).split()
        ch = f"+{stat[0]} / −{stat[1]} строк" if stat else "нет, совпадает побайтно"
        L.append(f"| `{f}` | {ch} | {PLACE.get(f, 'прежнее')} |")
    L.append("")
    for f in fs:
        p = f"{SRC}/{f}"
        if p not in inv: continue
        d = inv[p]; L += [f"### `{f}`", ""]
        for k in ["headings", "columns", "buttons", "api", "params", "perms", "storage"]:
            if d[k]: L.append(f"- **{NAMES[k]}** ({len(d[k])}): " + "; ".join(d[k]))
        L.append("")
open(out, "w", encoding="utf8").write("\n".join(L))
print("base files", len(bf), "now", len(wf), "checked", total, "bytext", bytext, "missing", len(missing)); [print(" ", m) for m in missing[:40]]
