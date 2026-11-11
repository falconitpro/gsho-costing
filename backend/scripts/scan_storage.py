"""Проверка уже сохранённых файлов антивирусом (docs/operations/04 §8.3, 13 «Найден заражённый файл»).

Запуск на сервере (из контейнера backend):
    docker compose exec backend python -m scripts.scan_storage            # файлы «не проверялся» из attachments
    docker compose exec backend python -m scripts.scan_storage --all      # плюс все прочие файлы хранилища
    docker compose exec backend python -m scripts.scan_storage --dry-run  # только посчитать

Чистый → scan_status=clean. Заражённый → файл переносится в карантин (QUARANTINE_DIR, вне хранилища, права 0600) под
случайным именем, attachment получает infected и перестаёт выдаваться, событие и критическое предупреждение.
Файлы не удаляются. Ошибка проверки — статус не меняется, файл остаётся на месте; повторный запуск продолжит.
Только локальное хранилище (STORAGE_PROVIDER=local).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select


def main(argv=None) -> int:
    from app.core.config import get_settings
    from app.db import SessionLocal
    from app.models import Attachment, AttachmentScan
    from app.services import file_scanner
    from app.services import security_events as sev
    from app.adapters.storage import sha256_file   # тот же расчёт, что и при загрузке

    ap = argparse.ArgumentParser(description="Проверка сохранённых файлов антивирусом")
    ap.add_argument("--all", action="store_true", help="и файлы хранилища, которых нет в attachments")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    s = get_settings()
    if s.storage_provider != "local":
        print("только для STORAGE_PROVIDER=local", file=sys.stderr)
        return 2
    if s.file_scan != "clamav" and not a.dry_run:
        print("антивирус выключен (FILE_SCAN=off) — проверка невозможна", file=sys.stderr)
        return 2
    root = Path(s.storage_local_root).resolve()
    qdir = Path(s.quarantine_dir).resolve()
    if qdir == root or root in qdir.parents:
        print("QUARANTINE_DIR не должен лежать внутри хранилища", file=sys.stderr)
        return 2
    counts = {"clean": 0, "infected": 0, "error": 0, "missing": 0}
    with SessionLocal() as db:
        atts = db.scalars(select(Attachment).where(Attachment.scan_status == "legacy_unscanned").order_by(Attachment.uploaded_at)).all()
        jobs: list[tuple[str, Attachment | None]] = [(x.storage_path, x) for x in atts]
        if a.all:
            known = {x.storage_path for x in db.scalars(select(Attachment)).all()}
            for p in sorted(root.rglob("*")):
                rel = str(p.relative_to(root))
                if p.is_file() and rel not in known:
                    jobs.append((rel, None))
        if a.limit:
            jobs = jobs[:a.limit]
        print(f"к проверке: {len(jobs)}")
        if a.dry_run:
            return 0
        qdir.mkdir(parents=True, exist_ok=True)
        os.chmod(qdir, 0o700)
        for rel, att in jobs:
            full = root / rel
            if not full.is_file():
                counts["missing"] += 1
                continue
            sha = sha256_file(str(full))
            row = AttachmentScan(source="storage", storage_path=rel[:1024], file_name=full.name[:255], status="pending", sha256=sha,
                                 size=full.stat().st_size, attachment_id=att.id if att else None)
            db.add(row)
            db.commit()
            with open(full, "rb") as fh:
                res = file_scanner.scan_stream(fh, s.clamd_host, s.clamd_port, timeout=float(s.clamd_timeout))
            row.status, row.detected_name, row.last_error_code = res.status, res.signature, res.error_code
            row.engine_version, row.signature_version, row.scanned_at = res.engine_version, res.signature_version, datetime.now(timezone.utc)
            if res.status == "clean" and att is not None:
                att.scan_status, att.scanned_at = "clean", row.scanned_at
            elif res.status == "infected":
                dest = qdir / row.quarantine_key
                shutil.move(str(full), dest)
                os.chmod(dest, 0o600)
                if att is not None:
                    att.scan_status, att.scanned_at = "infected", row.scanned_at
                sev.record(db, "file", "infected", result="denied", subject_id=att.uploaded_by if att else None, entity_type="attachment_scan",
                           entity_id=row.id, deal_id=att.deal_id if att else None, byte_size=row.size,
                           meta={"route": "storage", "threat": res.signature, "sha256_prefix": sha[:16]})
                print(f"ЗАРАЖЁН: {res.signature} — перенесён в карантин ({row.quarantine_key})")
            counts[res.status] = counts.get(res.status, 0) + 1
            db.commit()
    print("итог: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
