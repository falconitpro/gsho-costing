"""D-143: отдельный процесс фоновых заданий. Запуск: python -m app.worker (docker compose: сервис worker).

Каждые 5 минут: отметка «worker жив»; цикл заданий — если с прошлого прошло 55+ минут (см. app/jobs.py).
Пока worker отмечается, процесс API задания не выполняет.
"""
from __future__ import annotations

import logging
import signal
import sys
import time

from .core.config import get_settings

log = logging.getLogger("gsho.worker")
TICK_S = 300
_stop = False


def _on_signal(*_a) -> None:
    global _stop
    _stop = True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    if s.env == "recovery":
        log.info("recovery-копия: фоновые задания не выполняются")
        return
    from .core import environment
    from .db import SessionLocal
    with SessionLocal() as db:
        environment.check(db, s)
    from . import jobs
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    log.info("worker started")
    while not _stop:
        try:
            jobs.heartbeat("worker")
            r = jobs.run_cycle("worker")
            if r == "done":
                log.info("cycle done")
        except Exception as e:   # noqa: BLE001 — база недоступна и т. п.: подождать и повторить
            log.warning("worker tick failed: %s", e)
        for _ in range(TICK_S):
            if _stop:
                break
            time.sleep(1)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
    sys.exit(0)
