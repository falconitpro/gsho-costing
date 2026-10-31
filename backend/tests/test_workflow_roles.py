from datetime import date

import pytest

from app.core.roles import Role, denied_fields, filter_payload
from app.core.workflow import (
    DealState, DealStatus, SlaDefaults, WorkflowError, block_completed, cd_submit_price, close, kp_sent,
    manager_checks, rework, send_to_calc, visa_decision, add_business_days,
)

TODAY = date(2026, 10, 30)  # пятница
SLA = SlaDefaults()


def test_business_days():
    assert add_business_days(TODAY, 2) == date(2026, 11, 3)   # сб, вс пропускаются


def test_manager_checks():
    errs = manager_checks({"client_id": None, "positions": [{"factory": "X"}], "variants": []})
    assert any("клиент" in e for e in errs) and any("модель" in e for e in errs)
    assert not manager_checks({"client_id": 1, "positions": [{"factory": "X", "model": "M", "qty": 1, "unit_price": 10, "currency": "CNY"}],
                               "client_payments": [{}], "variants": [{}], "service_flag": True, "target_hours": 10000})


def test_happy_path_with_fd_visa():
    st = DealState(status=DealStatus.DRAFT, service_flag=True)
    r = send_to_calc(st, TODAY, SLA)
    assert r.status == DealStatus.LOGISTICS_CALC and {t.role for t in r.tasks} == {Role.LOGISTICIAN, Role.SERVICE_HEAD}
    st.status = r.status
    r = block_completed(st, "service", TODAY, SLA)
    assert r.status == DealStatus.LOGISTICS_CALC          # ждём логиста
    r = block_completed(st, "logistics", TODAY, SLA)
    assert r.status == DealStatus.CALC_READY and r.tasks[0].role == Role.CD
    st.status = r.status
    r = cd_submit_price(st, {"margin_below_threshold": True}, TODAY, SLA)
    assert r.status == DealStatus.FD_VISA and r.tasks[0].role == Role.FD
    st.status = r.status
    r = visa_decision(st, Role.FD, True, {"margin_below_threshold": True}, TODAY, SLA)
    assert r.status == DealStatus.APPROVED and r.new_version
    st.status = r.status
    assert kp_sent(st).status == DealStatus.KP_SENT
    st.status = DealStatus.KP_SENT
    assert close(st, "won", "лучшая цена").status == DealStatus.WON


def test_fd_declines_cd_insists_goes_to_gd_then_approved():
    st = DealState(status=DealStatus.FD_VISA, service_flag=False)
    r = visa_decision(st, Role.FD, False, {"margin_below_threshold": True}, TODAY, SLA, cd_insists=True)
    assert r.status == DealStatus.GD_VISA
    st.status = r.status
    r = visa_decision(st, Role.GD, True, {"margin_below_threshold": True, "fd_declined": True}, TODAY, SLA)
    assert r.status == DealStatus.APPROVED


def test_two_visas_in_sequence():
    st = DealState(status=DealStatus.CALC_READY, service_flag=False)
    flags = {"margin_below_threshold": True, "deal_above_gd_limit": True}
    r = cd_submit_price(st, flags, TODAY, SLA)
    assert r.status == DealStatus.FD_VISA
    st.status = r.status
    r = visa_decision(st, Role.FD, True, flags, TODAY, SLA)
    assert r.status == DealStatus.GD_VISA


def test_no_visa_needed_direct_approve():
    st = DealState(status=DealStatus.CALC_READY, service_flag=False)
    assert cd_submit_price(st, {}, TODAY, SLA).status == DealStatus.APPROVED


def test_rework_requires_reason_and_block():
    st = DealState(status=DealStatus.CD_APPROVAL, service_flag=False)
    with pytest.raises(WorkflowError):
        rework(st, "logistics", "", TODAY, SLA)
    r = rework(st, "logistics", "пересчитать фрахт", TODAY, SLA)
    assert r.status == DealStatus.REWORK and r.new_version and r.tasks[0].role == Role.LOGISTICIAN


def test_role_filtering():
    payload = {"id": 1, "price_rub": 100, "calc_result": {"margin_pct": 5}, "logistics": {"freight": 1},
               "positions": [{"model": "X", "cost_total_rub": 1}], "service_calc": {"hours": 1}}
    m = filter_payload(payload, [Role.MANAGER])
    assert "calc_result" not in m and "logistics" not in m and "service_calc" not in m and m["price_rub"] == 100
    lg = filter_payload(payload, [Role.LOGISTICIAN])
    assert "price_rub" not in lg and "logistics" in lg and "calc_result" not in lg
    cd = filter_payload(payload, [Role.CD])
    assert cd == payload
    assert denied_fields([Role.MANAGER, Role.CD]) == set()
    assert "cost_total_rub" in denied_fields([Role.ADMIN])
