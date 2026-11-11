"""Нагрузочная проверка антивируса и базы на сервере стенда (docs/operations/11 §9, 12 FILE-08).

Ничего не пишет в базу и хранилище: файлы создаются в памяти и уходят только в антивирус, запросы к базе — только чтение.
Запускать на сервере, желательно одновременно с ручной резервной копией (в другом окне: sudo systemctl start gsho-backup.service):

    docker compose exec backend python -m scripts.load_smoke
    docker compose exec backend python -m scripts.load_smoke --files 40 --mb 20 --workers 6

Печатает время проверки файлов (медиана, худшее), число ошибок и время запросов к базе во время нагрузки.
«ИТОГ: PASS» — нет ошибок антивируса, тестовая сигнатура найдена, худшее время файла в пределах таймаута.
"""
from __future__ import annotations

import argparse
import io
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor


def main(argv=None) -> int:
    from sqlalchemy import text

    from app.core.config import get_settings
    from app.db import SessionLocal
    from app.services import file_scanner

    ap = argparse.ArgumentParser(description="Нагрузочная проверка антивируса и базы")
    ap.add_argument("--files", type=int, default=24)
    ap.add_argument("--mb", type=int, default=10, help="размер каждого файла, МБ")
    ap.add_argument("--workers", type=int, default=4, help="одновременных проверок")
    a = ap.parse_args(argv)
    s = get_settings()
    try:
        v = file_scanner.version(s.clamd_host, s.clamd_port)
    except file_scanner.ScannerError as e:
        print(f"антивирус недоступен: {e.code}")
        return 2
    print(f"ClamAV {v['engine']}, базы {v['signatures']}; файлов {a.files} × {a.mb} МБ, одновременно {a.workers}")
    eicar = ("X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-" + "TEST-FILE!$H+H*").encode()
    times, errors, found = [], [], []
    lock = threading.Lock()

    def scan(i: int) -> None:
        body = os.urandom(1024 * 1024) * a.mb          # несжимаемые данные — худший случай для антивируса
        if i % 6 == 5:
            body = body[: len(body) // 2] + eicar       # каждый шестой — с тестовой сигнатурой в середине потока
        t = time.monotonic()
        res = file_scanner.scan_stream(io.BytesIO(body), s.clamd_host, s.clamd_port, timeout=float(s.clamd_timeout))
        dt = time.monotonic() - t
        with lock:
            times.append(dt)
            if res.status == "error":
                errors.append(res.error_code)
            if i % 6 == 5:
                found.append(res.status == "infected")

    db_times, stop = [], threading.Event()

    def db_probe() -> None:
        while not stop.is_set():
            t = time.monotonic()
            with SessionLocal() as db:
                db.execute(text("select count(*) from deals")).scalar()
                db.execute(text("select count(*) from tasks where done_at is null")).scalar()
            db_times.append(time.monotonic() - t)
            time.sleep(0.5)

    th = threading.Thread(target=db_probe, daemon=True)
    th.start()
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(scan, range(a.files)))
    total = time.monotonic() - t0
    stop.set()
    th.join(timeout=3)
    times.sort()
    print(f"проверка файлов: всего {total:.1f} с; на файл медиана {statistics.median(times):.2f} с, худшее {times[-1]:.2f} с; ошибок {len(errors)}"
          + (f" ({', '.join(sorted(set(errors)))})" if errors else ""))
    print(f"тестовая сигнатура: найдена в {sum(found)} из {len(found)} файлов")
    if db_times:
        db_times.sort()
        print(f"запросы к базе во время нагрузки: {len(db_times)} шт., медиана {statistics.median(db_times) * 1000:.0f} мс, худшее {db_times[-1] * 1000:.0f} мс")
    ok = not errors and all(found) and times[-1] < s.clamd_timeout and (not db_times or db_times[-1] < 5)
    print("ИТОГ: PASS" if ok else "ИТОГ: FAIL — пришлите вывод, docker stats и free -h")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
