"""Состояние антивируса для экрана и ежечасной проверки (04 §8.2): доступность clamd и свежесть баз.
Базы старше 48 ч или недоступный clamd — предупреждение безопасности (одна открытая запись)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..models import Attachment, AttachmentScan
from . import file_scanner

STALE_HOURS = 48
BYPASS_MAX_HOURS = 24


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


def bypass(db: Session) -> Optional[dict]:
    """Ручной обход (D-068): пока действует и антивирус не отвечает, файлы принимаются без проверки («не проверялся»)
    и перепроверяются, когда он снова заработает. Заражённые файлы блокируются всегда, если антивирус ответил."""
    from . import settings_service
    b = settings_service.get(db, "file_scan_bypass")
    if not b or not b.get("until"):
        return None
    try:
        until = datetime.fromisoformat(b["until"])
    except ValueError:
        return None
    return {**b, "active": until > datetime.now(timezone.utc)}


def bypass_active(db: Session) -> bool:
    b = bypass(db)
    return bool(b and b["active"])


def set_bypass(db: Session, user_id: str, hours: float, reason: str) -> dict:
    from . import settings_service
    from . import security_events as sev
    now = datetime.now(timezone.utc)
    val = {"since": now.isoformat(), "until": (now + timedelta(hours=hours)).isoformat(), "reason": reason, "by": user_id}
    settings_service.set_value(db, "file_scan_bypass", val, user_id, reason)
    sev.record(db, "scanner", "bypass_on", actor_id=user_id, subject_id=None, meta={"hours": hours})
    return val


def clear_bypass(db: Session, user_id: Optional[str], why: str = "") -> None:
    from . import settings_service
    from . import security_events as sev
    if settings_service.get(db, "file_scan_bypass"):
        settings_service.set_value(db, "file_scan_bypass", None, user_id, why or "обход выключен")
        sev.record(db, "scanner", "bypass_off", actor_id=user_id, subject_id=None, meta={"why": why[:100]})


def _auto_resolve(db: Session, why: str) -> int:
    """Антивирус снова отвечает — открытые тревоги «Проверка файлов недоступна» закрываются автоматически."""
    from ..models import SecurityAlert
    n = 0
    for a in db.scalars(select(SecurityAlert).where(SecurityAlert.rule_key == "scanner_unavailable", SecurityAlert.status != "resolved")).all():
        a.status, a.resolved_at, a.resolution_comment = "resolved", datetime.now(timezone.utc), why
        n += 1
    return n


def rescan_unscanned(db: Session, s: Settings, since: Optional[datetime]) -> dict:
    """Файлы, принятые без проверки во время обхода, — проверить, как только антивирус ответил."""
    from ..models import Attachment as _A
    if since is None:
        return {"checked": 0}
    rows = db.scalars(select(_A).where(_A.scan_status == "legacy_unscanned", _A.uploaded_at >= since)).all()
    if not rows:
        return {"checked": 0}
    from scripts.scan_storage import main as scan_main   # тот же путь, что и ручная проверка: карантин, событие, предупреждение
    rc = scan_main([])
    return {"checked": len(rows), "rc": rc}


def summary(db: Session, s: Settings) -> dict:
    by_status = dict(db.execute(select(Attachment.scan_status, func.count()).group_by(Attachment.scan_status)).all())
    recent = db.scalars(select(AttachmentScan).order_by(AttachmentScan.created_at.desc()).limit(20)).all()
    iso = lambda d: d.isoformat() if d else None   # noqa: E731
    b = bypass(db)
    if b and b.get("by"):
        from ..models import User
        u = db.get(User, b["by"])
        b = {**b, "by_name": (u.display_name or u.email) if u else ""}
    return {**status(s), "bypass": b, "bypass_max_hours": BYPASS_MAX_HOURS, "attachments": {k: int(v) for k, v in by_status.items()},
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
        return st
    if _auto_resolve(db, "Антивирус снова отвечает — закрыто автоматически."):
        db.commit()
    b = bypass(db)
    if b:
        since = datetime.fromisoformat(b["since"]) if b.get("since") else None
        if not b["active"]:
            clear_bypass(db, None, "срок обхода истёк")
            db.commit()
        st["rescan"] = rescan_unscanned(db, s, since)
    if st["stale"]:
        sev.record(db, "scanner", "stale", meta={"hours": st["age_hours"], "signatures": st["signatures"]})
        db.commit()
    return st
