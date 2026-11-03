from datetime import date

import pytest

from app.core.roles import Role, denied_fields, filter_payload
from app.core.workflow import (
    DealState, DealStatus, SlaDefaults, WorkflowError, block_completed, cd_checks, cd_submit_price, close, close_execution,
    contract_terms_changed, kp_sent, manager_checks, rework, send_to_calc, start_execution, visa_decision, add_business_days,
)

TODAY = date(2026, 10, 30)  # пятница
SLA = SlaDefaults()


def test_business_days():
    assert add_business_days(TODAY, 2) == date(2026, 11, 3)   # сб, вс пропускаются


def test_manager_checks():
    errs = manager_checks({"client_id": None, "positions": [{"factory": "X"}], "variants": []})
    assert any("клиент" in e for e in errs) and any("модель" in e for e in errs)
    pos = {"factory": "X", "model": "M", "qty": 1, "unit_price": 10, "currency": "CNY"}
    # задачи 1, 5: условия оплаты и варианты менеджер не обязан задавать
    assert not manager_checks({"client_id": 1, "positions": [pos], "client_payments": [], "variants": [], "service_flag": True, "target_hours": 10000})
    # задача 5: при альтернативах нужно минимум два предложения
    errs = manager_checks({"client_id": 1, "positions": [pos], "has_alternatives": True, "variants": [{"name": "A"}]})
    assert any("минимум два" in e for e in errs)
    # задача 4: обязательные технические характеристики без значения
    errs = manager_checks({"client_id": 1, "positions": [{**pos, "tech_param_rows": [{"name_ru": "Мощность", "required": True, "value_ru": ""}]}]})
    assert any("обязательные технические характеристики" in e for e in errs)
    assert not manager_checks({"client_id": 1, "positions": [{**pos, "tech_param_rows": [{"name_ru": "Мощность", "required": True, "value_ru": "", "local_ru": True}]}]})
    # задача 1/6: проверки КД перед утверждением
    errs = cd_checks({"payment_terms_set": False, "positions": [{"model": "M", "factory_payments": [], "local_options": [{"name": "Кондиционер", "cost": 0, "supplier_lead_days": 0}]}]})
    assert len(errs) == 3
    assert not cd_checks({"payment_terms_set": True, "positions": [{"model": "M", "factory_payments": [{}], "local_options": [{"name": "К", "cost": 1, "supplier_lead_days": 10}]}]})


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
    # задача 2: выигрыш сразу переводит в стадию заключения контракта
    r = close(st, "won", "лучшая цена")
    assert r.status == DealStatus.CONTRACT and r.tasks[0].role == Role.MANAGER
    st.status = DealStatus.CONTRACT
    with pytest.raises(WorkflowError):
        start_execution(st, False, TODAY)
    assert start_execution(st, True, TODAY).status == DealStatus.EXECUTION
    st.status = DealStatus.EXECUTION
    r = contract_terms_changed(st, "цена изменена", TODAY, SLA)
    assert r.status == DealStatus.CD_APPROVAL and r.new_version and r.tasks[0].role == Role.CD
    st.status, st.in_execution = DealStatus.CALC_READY, True
    assert cd_submit_price(st, {}, TODAY, SLA).status == DealStatus.CONTRACT   # повторная виза возвращает на стадию контракта
    st.status = DealStatus.EXECUTION
    assert close_execution(st, "исполнено").status == DealStatus.CLOSED


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


def test_access_matrix_overrides_and_gd_inherits_cd():
    from app.core.roles import effective_perms, EDIT, READ, NONE
    cd = effective_perms([Role.CD])
    gd = effective_perms([Role.GD])
    assert all(gd[g] >= lvl for g, lvl in cd.items())            # ЗГД ⊇ КД
    mgr = effective_perms([Role.MANAGER])
    assert mgr["client_price_history"] == NONE and mgr["factory_price_history"] == READ and mgr["payment_terms"] == READ
    # правило из БД и исключение сотрудника
    rules = {"manager": {"client_price_history": READ}}
    assert effective_perms([Role.MANAGER], rules)["client_price_history"] == READ
    assert effective_perms([Role.MANAGER], rules, {"client_price_history": NONE})["client_price_history"] == NONE
    # защита: право на уровни доступа у ЗГД нельзя снять
    assert effective_perms([Role.GD], {"gd": {"access_admin": NONE}})["access_admin"] == EDIT
    # фильтрация по правам: открыли менеджеру историю КП — ключи проходят
    payload = {"kp_history": [1], "calc_result": {}, "price_rub": 1}
    assert "kp_history" not in filter_payload(payload, [Role.MANAGER])
    assert "kp_history" in filter_payload(payload, effective_perms([Role.MANAGER], rules))


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
