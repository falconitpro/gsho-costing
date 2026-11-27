"""Узлы и агрегаты (D-200, J-c): общий справочник узлов и перенос текстов «узел» из применимости запчастей.
По умолчанию — пробный прогон: показывает, что будет заведено и сколько связей получит ссылку, ничего не меняя.
Текст «узел» в применимости не удаляется. Повторный запуск безопасен.

  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend python -m scripts.assemblies_migrate          # пробный
  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend python -m scripts.assemblies_migrate --yes    # перенос
"""
from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    from app.db import SessionLocal
    from app.services.assemblies import migrate
    apply = "--yes" in argv
    with SessionLocal() as db:
        r = migrate(db, apply)
        print(f"Общий справочник узлов: новых {r['general_new']}")
        print(f"Связей применимости с текстом узла: {r['rows']}; получат ссылку на узел: {r['linked']}")
        from collections import Counter
        grouped = Counter(r["created"])
        print(f"Новых узлов из текстов: {len(r['created'])}" + (" — " + "; ".join(f"{k}{f' ×{v}' if v > 1 else ''}" for k, v in grouped.most_common(40)) if grouped else ""))
        if apply:
            db.commit()
            print("Готово. Тексты «узел» сохранены, добавлены ссылки.")
        else:
            print("Пробный прогон: ничего не изменено. Для переноса — с ключом --yes.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
