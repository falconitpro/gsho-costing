"""Проверка антивируса после включения (deploy/clamav/install.md): версия и свежесть баз, чистый поток, тестовая сигнатура
EICAR (не вредоносный код — стандартная строка для проверки антивирусов, собирается в памяти, на диск не пишется).

    docker compose exec backend python -m scripts.scanner_check
"""
from __future__ import annotations

import io
import sys
import time


def main() -> int:
    from app.core.config import get_settings
    from app.services import file_scanner, scanner_status
    s = get_settings()
    st = scanner_status.status(s, probe=True)
    print(f"режим FILE_SCAN={s.file_scan}, clamd {s.clamd_host}:{s.clamd_port}")
    try:
        v = file_scanner.version(s.clamd_host, s.clamd_port)
    except file_scanner.ScannerError as e:
        print(f"clamd недоступен: {e.code}")
        return 1
    print(f"ClamAV {v['engine']}, базы {v['signatures']} от {v['signatures_at']}, возраст {st['age_hours']} ч" + (" — УСТАРЕЛИ" if st["stale"] else ""))
    t = time.monotonic()
    clean = file_scanner.scan_stream(io.BytesIO(b"%PDF-1.4 gsho scanner check" * 1000), s.clamd_host, s.clamd_port)
    print(f"обычный файл: {clean.status} ({time.monotonic() - t:.2f} с)")
    eicar = ("X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-" + "TEST-FILE!$H+H*").encode()
    test = file_scanner.scan_stream(io.BytesIO(eicar), s.clamd_host, s.clamd_port)
    print(f"тестовая сигнатура EICAR: {test.status} {test.signature}")
    ok = clean.status == "clean" and test.status == "infected" and v["signatures"] > 0 and not st["stale"]
    print("ИТОГ: антивирус готов" if ok else "ИТОГ: НЕ готов — см. вывод выше")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
