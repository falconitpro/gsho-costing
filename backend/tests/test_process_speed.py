"""D-144: «Скорость процесса» — этапы по истории статусов, узкое место, сквозные сроки, задачи по сотрудникам."""
from datetime import datetime, timedelta, timezone

from tests.test_api_flow import client, _ok, H, MGR, CD, GD  # noqa: F401


def test_speed_report(client):
    from app.db import SessionLocal
    from app.models import AuditLog, Deal
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    did = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "Скорость"}), 201)["id"]
    t0 = datetime.now(timezone.utc) - timedelta(days=10)
    with SessionLocal() as db:   # история: черновик 2 ч → логистика 30 ч → КД 5 ч → утверждено (сейчас)
        d = db.get(Deal, did)
        d.created_at = t0
        for h, a, b in ((2, "draft", "logistics_calc"), (32, "logistics_calc", "calc_ready"), (37, "calc_ready", "approved")):
            db.add(AuditLog(at=t0 + timedelta(hours=h), deal_id=did, entity="deal", entity_id=did, action="status", before={"status": a}, after={"status": b}))
        db.commit()
    r = _ok(client.get("/api/reports/speed", headers=H(CD), params={"days": 30}))
    by = {s["status"]: s for s in r["stages"]}
    assert by["logistics_calc"]["passes"] >= 1 and by["logistics_calc"]["p80_h"] >= 0
    with SessionLocal() as db:
        from app.services.process_speed import report, segments
        segs = segments(db)[did]
        assert [s[0] for s in segs] == ["draft", "logistics_calc", "calc_ready", "approved"]
        assert round((segs[1][2] - segs[1][1]).total_seconds() / 3600) == 30 and segs[-1][2] is None
        rep = report(db, 30)
        assert any(x["deal_id"] == did for s in rep["stages"] if s["status"] == "approved" for x in s["now_longest"]) or \
            next(s for s in rep["stages"] if s["status"] == "approved")["now"] >= 1
        assert next(e for e in rep["end_to_end"] if e["key"] == "approved")["deals"] >= 1
    assert client.get("/api/reports/speed", headers=H(MGR)).status_code == 403
