"""Порог маржи по стоимости сделки (D-067)."""
import os

import pytest

from app.calc.types import OverheadStep, Thresholds


def test_margin_for_scale():
    t = Thresholds(margin_threshold_pct=3.0)
    assert t.margin_for(1e9) == 3.0                               # шкалы нет — общий порог
    t = Thresholds(margin_threshold_pct=3.0, margin_scale=[OverheadStep(up_to_rub=10e6, pct=12), OverheadStep(up_to_rub=50e6, pct=8),
                                                           OverheadStep(up_to_rub=None, pct=5)])
    assert (t.margin_for(5e6), t.margin_for(10e6), t.margin_for(30e6), t.margin_for(80e6)) == (12, 12, 8, 5)
    assert t.margin_for(None) == 3.0


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="нужна БД")
def test_scale_drives_fd_visa_flag():
    from tests.test_api_flow import _ok, H, CD, FD
    from tests.test_competitors import _deal
    from fastapi.testclient import TestClient
    from app.main import app
    from scripts.seed import run as seed
    seed()
    with TestClient(app) as client:
        did, vid = _deal(client, "Шкала маржи")
        _ok(client.post(f"/api/deals/{did}/price", headers=H(CD), json={"variant_id": vid, "mode": "margin", "value": 6.0}))
        calc = _ok(client.get(f"/api/deals/{did}/calc", headers=H(CD)))
        price = calc["result"]["variants"][vid]["base"]["price_rub"]
        assert not calc["approval_flags"]["margin_below_threshold"] and calc["threshold_by_variant"][vid] == 3.0
        # шкала: сделки такой стоимости — порог 10 % → маржа 6 % требует визы ФД
        assert client.put("/api/admin/settings/margin_threshold_scale", headers=H(CD), json={"value": []}).status_code == 403
        r = client.put("/api/admin/settings/margin_threshold_scale", headers=H(FD), json={"value": [{"up_to_rub": None, "pct": 4}, {"up_to_rub": price * 2, "pct": 10}]})
        assert _ok(r)["value"] == [{"up_to_rub": price * 2, "pct": 10.0}, {"up_to_rub": None, "pct": 4.0}]          # «свыше» — последней
        calc = _ok(client.get(f"/api/deals/{did}/calc", headers=H(CD)))
        assert calc["threshold_by_variant"][vid] == 10.0 and calc["approval_flags"]["margin_below_threshold"]
        assert calc["result"]["variants"][vid]["base"]["margin_threshold_pct"] == 10.0
        assert any("ниже порога 10 %" in x for x in calc["result"]["approval"]["reasons"])
        _ok(client.put("/api/admin/settings/margin_threshold_scale", headers=H(FD), json={"value": []}))
