"""Состояние антивируса для экрана и ежечасной проверки (04 §8.2): доступность clamd и свежесть баз.
Базы старше 48 ч или недоступный clamd — предупреждение безопасности (одна открытая запись)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..models import Attachment, AttachmentScan
from . import file_scanner

STALE_HOURS = 48


def status(s: Settings, probe: bool = False) -> dict:
    """probe=True — опросить clamd даже при FILE_SCAN=off (проверка перед включением)."""
    out = {"mode": s.file_scan, "available": None, "engine": "", "signatures": None, "signatures_at": None, "age_hours": None, "stale": False, "error": ""}
    if s.file_scan != "clamav" and not probe:
        return out
    try:
        v = file_scanner.version(s.clamd_host, s.clamd_port, timeout=5.0)
    except file_scanner.ScannerError as e:
        return {**out, "available": False, "error": e.code}
    age = None
    if v["signatures_at"]:
        age = round((datetime.now(timezone.utc) - v["signatures_at"]).total_seconds() / 3600, 1)
    return {**out, "available": True, "engine": v["engine"], "signatures": v["signatures"],
            "signatures_at": v["signatures_at"].isoformat() if v["signatures_at"] else None, "age_hours": age, "stale": bool(age is not None and age > STALE_HOURS)}


def summary(db: Session, s: Settings) -> dict:
    by_status = dict(db.execute(select(Attachment.scan_status, func.count()).group_by(Attachment.scan_status)).all())
    recent = db.scalars(select(AttachmentScan).order_by(AttachmentScan.created_at.desc()).limit(20)).all()
    iso = lambda d: d.isoformat() if d else None   # noqa: E731
    return {**status(s), "attachments": {k: int(v) for k, v in by_status.items()},
            "recent": [{"id": r.id, "at": iso(r.scanned_at or r.created_at), "source": r.source, "file_name": r.file_name, "status": r.status,
                        "detected_name": r.detected_name, "error": r.last_error_code, "size": r.size, "signature_version": r.signature_version} for r in recent]}


def hourly(db: Session, s: Settings) -> dict:
    from . import security_events as sev
    st = status(s)
    if s.file_scan != "clamav":
        return st
    if st["available"] is False:
        sev.record(db, "scanner", "down", meta={"error": st["error"]})
        db.commit()
    elif st["stale"]:
        sev.record(db, "scanner", "stale", meta={"hours": st["age_hours"], "signatures": st["signatures"]})
        db.commit()
    return st
