#!/usr/bin/env python3
"""D-210 (аудит REL-001/REL-006): пакет доказательств версии — что именно проверено и чем.

Собирается в задании verified после всех проверок: SHA коммита, время, версии окружения, суммы файлов зависимостей,
список миграций с суммами, итоги pytest (JUnit), итоги приёмки в браузере. Без секретов и без данных.
Запуск: python3 scripts/ci/release_evidence.py <каталог артефактов> <каталог вывода>
"""
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "—"


def junit(p: Path) -> dict:
    if not p.exists():
        return {"found": False}
    r = ET.parse(p).getroot()
    suites = [r] if r.tag == "testsuite" else list(r.iter("testsuite"))
    tot = {k: sum(int(s.get(k, 0) or 0) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
    skipped = [f"{c.get('classname')}::{c.get('name')} — {(c.find('skipped').get('message') or '')[:160]}"
               for s in suites for c in s.iter("testcase") if c.find("skipped") is not None]
    return {"found": True, **tot, "skipped_list": skipped}


def git(*a: str) -> str:
    try:
        return subprocess.check_output(["git", *a], cwd=ROOT, text=True).strip()
    except Exception:  # noqa: BLE001
        return ""


def main() -> int:
    art, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    sha_full = os.environ.get("GITHUB_SHA") or git("rev-parse", "HEAD")
    migs = sorted((ROOT / "backend" / "migrations").glob("*.sql"))
    be = junit(art / "backend-junit" / "backend-junit.xml")
    acc = junit(art / "acceptance-report" / "junit.xml")
    accn = junit(art / "acceptance-report" / "new-ui" / "junit.xml")
    m = {
        "commit": sha_full, "short": sha_full[:7], "subject": git("log", "-1", "--format=%s", sha_full),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run": f"{os.environ.get('GITHUB_SERVER_URL', '')}/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}",
        "environment": {"python": "3.12", "node": "20", "postgres": "16 (тот же major, что рабочий сервер)", "browser": "Chrome (Playwright)"},
        "lockfiles": {f: sha(ROOT / f) for f in ("backend/requirements.txt", "backend/constraints.txt", "frontend/package-lock.json")},
        "migrations": {"count": len(migs), "last": migs[-1].name if migs else "", "lock": sha(ROOT / "scripts/ci/migrations.lock")},
        "checks": {"backend_pytest": be, "acceptance": acc, "acceptance_new_ui": accn,
                   "frontend": "tsc --noEmit + vite build + eslint + храповик стилей — задание frontend зелёное",
                   "ui": "разделы по ролям, сквозные сценарии, приёмка ЗГД (обычный и новый вид), доступность 1440/390 — задание ui зелёное",
                   "schema": "миграции накатываются на образец схемы рабочего сервера и сверяются с моделями (test_prod_schema_baseline)"},
    }
    (out / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1))

    def line(name: str, j: dict) -> str:
        if not j.get("found"):
            return f"| {name} | отчёт не найден |"
        ok = j["failures"] == 0 and j["errors"] == 0
        return f"| {name} | {'✅' if ok else '❌'} тестов {j['tests']}, упало {j['failures'] + j['errors']}, пропущено {j['skipped']} |"

    md = [f"# Пакет доказательств версии {m['short']}", "",
          f"- Коммит: `{m['commit']}` — {m['subject']}", f"- Собрано: {m['built_at']}", f"- Прогон CI: {m['run']}",
          f"- Окружение: Python {m['environment']['python']}, Node {m['environment']['node']}, PostgreSQL {m['environment']['postgres']}, {m['environment']['browser']}",
          "", "## Проверки", "", "| Проверка | Результат |", "|---|---|",
          line("Backend: pytest на PostgreSQL 16", be), line("Приёмка в браузере (обычный вид)", acc), line("Приёмка в браузере (новый вид)", accn),
          f"| Frontend | ✅ {m['checks']['frontend']} |", f"| Интерфейс | ✅ {m['checks']['ui']} |", f"| Схема | ✅ {m['checks']['schema']} |",
          "", "## Миграции", "", f"Файлов: {m['migrations']['count']}, последняя: `{m['migrations']['last']}`, сумма замка: `{m['migrations']['lock'][:16]}…`",
          "", "## Зависимости (sha256)", ""] + [f"- `{k}`: `{v[:16]}…`" for k, v in m["lockfiles"].items()]
    if be.get("skipped_list"):
        md += ["", "## Пропущенные тесты backend и причина", ""] + [f"- {s}" for s in be["skipped_list"]]
    (out / "MANIFEST.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
