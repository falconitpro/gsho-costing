#!/usr/bin/env python3
"""D-210 (аудит ARCH-004): контрольные суммы SQL-миграций.

Выпущенная миграция не меняется: если файл из migrations.lock изменён или удалён — CI падает.
Новые файлы добавляются в замок командой `python3 scripts/ci/migrations_lock.py --update` (в том же PR).
Номера идут по порядку без повторов.
"""
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIG = ROOT / "backend" / "migrations"
LOCK = Path(__file__).with_name("migrations.lock")


def current() -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(MIG.glob("*.sql"))}


def read_lock() -> dict[str, str]:
    if not LOCK.exists():
        return {}
    out = {}
    for line in LOCK.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            h, name = line.split(maxsplit=1)
            out[name] = h
    return out


def main() -> int:
    cur, lock = current(), read_lock()
    errors = []
    nums = [int(m.group(1)) for n in cur if (m := re.match(r"^(\d{3})_", n))]
    dups = sorted({n for n in nums if nums.count(n) > 1})
    if dups:
        errors.append(f"повторяются номера миграций: {dups}")
    for name, h in lock.items():
        if name not in cur:
            errors.append(f"выпущенная миграция удалена: {name}")
        elif cur[name] != h:
            errors.append(f"выпущенная миграция изменена: {name} — исправление делается новой миграцией")
    new = [n for n in cur if n not in lock]
    if "--update" in sys.argv:
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 1
        LOCK.write_text("# sha256  файл — выпущенные миграции не меняются (scripts/ci/migrations_lock.py)\n"
                        + "".join(f"{h}  {n}\n" for n, h in cur.items()))
        print(f"замок обновлён: +{len(new)}")
        return 0
    if new:
        errors.append(f"новые миграции не внесены в замок (запустите с --update): {', '.join(new)}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"миграции: {len(cur)} файлов, суммы совпадают")
    return 0


if __name__ == "__main__":
    sys.exit(main())
