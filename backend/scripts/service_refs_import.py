"""Черновик справочников сервиса (D-191, I-0): виды работ с нормативами, допуски, категории сроков реакции.
По умолчанию — пробный прогон: показывает, какие коды будут добавлены, и ничего не меняет. Уже заведённые коды
не меняются (правки руководителя сервиса не затираются), поэтому повторный запуск безопасен.

Запуск на рабочем сервере (только по согласованию владельца):
  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend python -m scripts.service_refs_import          # пробный
  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend python -m scripts.service_refs_import --yes    # загрузка
"""
from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    from app.db import SessionLocal
    from app.services import service_refs
    apply = "--yes" in argv
    with SessionLocal() as db:
        todo = service_refs.load_drafts(db) if apply else service_refs.plan(db)
        for k, title in (("permits", "Допуски"), ("sla", "Сроки реакции"), ("work_types", "Виды работ")):
            print(f"{title}: {len(todo[k])} новых{(' — ' + ', '.join(todo[k])) if todo[k] else ''}")
        if apply:
            db.commit()
            print("Загружено как черновик: проверьте и утвердите в справочниках сервиса.")
        else:
            print("Пробный прогон: ничего не изменено. Для загрузки — с ключом --yes.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
