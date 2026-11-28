"""D-210: атомарная выдача следующего номера (аудит: SRV-003 — гонка «наибольший + 1»).

Одна команда INSERT … ON CONFLICT DO UPDATE … RETURNING под блокировкой строки счётчика: два одновременных запроса
получают разные номера. При первом обращении к счётчику он продолжает уже выданные номера (start = наибольший + 1);
GREATEST защищает от отката, если записи с номерами создавались до появления счётчика.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def next_seq(db: Session, key: str, year: int, start: int) -> int:
    return int(db.execute(text(
        "INSERT INTO number_counters (key, year, seq) VALUES (:k, :y, :s) "
        "ON CONFLICT (key, year) DO UPDATE SET seq = GREATEST(number_counters.seq + 1, :s) RETURNING seq"),
        {"k": key, "y": year, "s": max(1, start)}).scalar_one())
