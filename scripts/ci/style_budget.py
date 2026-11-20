"""Храповик стилей интерфейса (D-117): встроенных стилей и «сырых» цветов в коде экранов не становится больше.

Считает в frontend/src (*.tsx, *.ts, без styles.css и login.css):
- inline — число `style={{` (встроенные стили вместо классов из styles.css);
- colors — число разных цветов #rgb / #rrggbb в коде экранов (вместо переменных палитры var(--…)).
Предел — scripts/ci/style_budget.json. Больше предела — CI падает. Меньше — подсказка снизить предел в том же PR,
чтобы достигнутое не откатилось. Запуск: python3 scripts/ci/style_budget.py [--update]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "frontend" / "src"
BUDGET = Path(__file__).with_name("style_budget.json")
HEX = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")


def measure() -> dict:
    inline, colors = 0, set()
    for f in list(SRC.rglob("*.tsx")) + list(SRC.rglob("*.ts")):
        t = f.read_text(encoding="utf-8")
        inline += t.count("style={{")
        colors.update(c.lower() for c in HEX.findall(t))
    return {"inline": inline, "colors": len(colors)}


def main() -> int:
    now = measure()
    if "--update" in sys.argv:
        BUDGET.write_text(json.dumps(now, indent=2) + "\n", encoding="utf-8")
        print("предел обновлён:", now)
        return 0
    limit = json.loads(BUDGET.read_text(encoding="utf-8"))
    bad = [k for k in now if now[k] > limit.get(k, 0)]
    for k, title in (("inline", "встроенных стилей style={{…}}"), ("colors", "разных цветов #… в коде экранов")):
        print(f"{title}: {now[k]} (предел {limit.get(k)})")
    if bad:
        print("\nСтало больше, чем было. Используйте классы из styles.css и переменные палитры var(--accent), var(--line), var(--muted)…;"
              " если без этого никак — объясните в PR и поднимите предел: python3 scripts/ci/style_budget.py --update", file=sys.stderr)
        return 1
    if any(now[k] < limit.get(k, 0) for k in now):
        print("\nСтало меньше — зафиксируйте новый предел в этом же PR: python3 scripts/ci/style_budget.py --update")
    return 0


if __name__ == "__main__":
    sys.exit(main())
