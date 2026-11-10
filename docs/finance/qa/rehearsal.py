"""Миграционная репетиция «Финансов» (docs/finance/06, этап 7; D-146).

Запуск — из backend/ на КОПИИ базы (никогда не на рабочей), DATABASE_URL указывает на копию:

    python ../docs/finance/qa/rehearsal.py before  > before.json     # контрольные цифры до миграции
    psql ... -f migrations/002_finance_core.sql … 007_finance_questions.sql
    python ../docs/finance/qa/rehearsal.py schema                    # схема = модели (только чтение)
    python ../docs/finance/qa/rehearsal.py rebuild before.json > report.json   # dry-run → сборка → повтор; сверка «до/после»

Скрипт ничего не удаляет; рабочие таблицы CRM только читает. Пишет — только в таблицы finance_* (пересборка) и журнал действий.
Без секретов: в отчёт попадают количества, суммы и отпечатки, не содержимое.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

from sqlalchemy import create_engine, inspect, text

URL = os.environ["DATABASE_URL"]
engine = create_engine(URL)

# что не должно меняться: утверждённые расчёты, подписанные условия, отметки исполнения, план/факт, задачи, файлы
GUARDED = {
    "deal_versions": "id, version_no, calc_input::text, calc_result::text, excel_path, kp_path",
    "deals": "id, status::text, approved_version_id, won_variant_id, plan_fact_closed",
    "contracts": "id, status, amount, currency, payment_terms::text, performance_terms::text, penalties::text, security::text, signed_on, facts::text, files::text",
    "execution_stages": "id, code, amount, currency, planned_on, actual_on, actual_amount",
    "plan_fact_entries": "id, kind, cost_item_code, amount, currency, fx_rate, \"on\", ref",
    "contract_claims": "id, status, lines::text",
    "tasks": "id, block, due, done_at",
}
SUMS = {"contracts.amount": "select coalesce(sum(amount),0) from contracts", "plan_fact_entries.amount": "select coalesce(sum(amount),0) from plan_fact_entries",
        "execution_stages.actual_amount": "select coalesce(sum(actual_amount),0) from execution_stages"}


def _hash(conn, table: str, cols: str) -> dict:
    rows = conn.execute(text(f"select {cols} from {table} order by id")).all()
    h = hashlib.sha256()
    for r in rows:
        h.update(json.dumps([str(x) for x in r], ensure_ascii=False).encode())
    return {"count": len(rows), "sha256": h.hexdigest()}


def snapshot() -> dict:
    insp = inspect(engine)
    out = {"tables": {}, "guarded": {}, "sums": {}}
    with engine.connect() as conn:
        for t in sorted(insp.get_table_names()):
            if not t.startswith("finance_"):
                out["tables"][t] = conn.execute(text(f'select count(*) from "{t}"')).scalar()
        for t, cols in GUARDED.items():
            if insp.has_table(t):
                out["guarded"][t] = _hash(conn, t, cols)
        for k, q in SUMS.items():
            out["sums"][k] = str(conn.execute(text(q)).scalar())
        out["finance_tables"] = sorted(t for t in insp.get_table_names() if t.startswith("finance_"))
    return out


def schema() -> dict:
    """Схема БД против моделей — только чтение (в отличие от add_missing_columns)."""
    sys.path.insert(0, os.getcwd())
    import app.models  # noqa: F401
    from app.db import Base
    insp = inspect(engine)
    missing_tables, missing_cols = [], []
    for t in Base.metadata.sorted_tables:
        if not insp.has_table(t.name):
            missing_tables.append(t.name)
            continue
        have = {c["name"] for c in insp.get_columns(t.name)}
        missing_cols += [f"{t.name}.{c.name}" for c in t.columns if c.name not in have]
    fin = [t for t in Base.metadata.sorted_tables if t.name.startswith("finance_")]
    idx = {t.name: sorted(i["name"] for i in insp.get_indexes(t.name)) for t in fin if insp.has_table(t.name)}
    return {"missing_tables": missing_tables, "missing_columns": missing_cols, "finance_indexes": idx, "ok": not missing_tables and not missing_cols}


def _finance_counts(conn) -> dict:
    q = {
        "obligations_by_source": "select source_type, is_active, count(*), coalesce(sum(amount),0) from finance_obligations group by 1,2 order by 1,2",
        "obligations_by_sync": "select sync_status, count(*) from finance_obligations group by 1 order by 1",
        "transactions_by_status_origin": "select status, origin_type, count(*), coalesce(sum(amount),0) from finance_transactions group by 1,2 order by 1,2",
        "allocations_by_status": "select status, count(*) from finance_allocations group by 1 order by 1",
        "recognition_by_source": "select source_type, kind, status, count(*), coalesce(sum(amount_rub),0) from finance_recognition_events group by 1,2,3 order by 1,2,3",
    }
    return {k: [[str(x) for x in r] for r in conn.execute(text(v)).all()] for k, v in q.items()}


def rebuild(before: dict) -> dict:
    sys.path.insert(0, os.getcwd())
    os.environ.setdefault("APP_ENV", "dev")
    os.environ.setdefault("AUTH_PROVIDER", "dev")
    schema_before_app = schema()
    from fastapi.testclient import TestClient
    from app.main import app
    H = {"X-Dev-User": os.environ.get("REHEARSAL_USER", "fd@example.com")}
    rep: dict = {"schema_after_sql": schema_before_app}
    with TestClient(app) as c:
        rep["schema_after_app_start"] = schema()   # init_db() в dev не должен ничего добавлять после SQL
        for step, dry in (("dry_run", True), ("build", False), ("repeat_dry_run", True), ("repeat_build", False)):
            r = c.post(f"/api/finance/sync?dry_run={'true' if dry else 'false'}", headers=H)
            assert r.status_code == 200, r.text
            rep[step] = r.json()
            with engine.connect() as conn:
                rep[f"{step}_finance"] = _finance_counts(conn)
        rec = c.get("/api/finance/reconciliation", headers=H).json()
        rep["reconciliation_queues"] = {q["code"]: q["count"] for q in rec["queues"]}
        ov = c.get("/api/finance/overview", headers=H).json()
        rep["overview"] = {"unallocated": ov["unallocated"], "reconciliation": ov["reconciliation"], "no_date": ov["no_date_count"]}
    after = snapshot()
    rep["guarded_changed"] = [t for t, v in before["guarded"].items() if after["guarded"].get(t) != v]
    rep["sums_changed"] = {k: [before["sums"][k], after["sums"][k]] for k in before["sums"] if before["sums"][k] != after["sums"][k]}
    rep["table_counts_changed"] = {t: [n, after["tables"].get(t)] for t, n in before["tables"].items() if after["tables"].get(t) != n}
    rep["idempotent"] = (rep["repeat_build"]["created"] == 0 and rep["repeat_build"]["updated"] == 0 and rep["repeat_build"].get("recognition_migrated") == 0
                         and rep["repeat_build_finance"] == rep["build_finance"])
    return rep


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "before":
        print(json.dumps(snapshot(), ensure_ascii=False, indent=1))
    elif cmd == "schema":
        print(json.dumps(schema(), ensure_ascii=False, indent=1))
    elif cmd == "rebuild":
        print(json.dumps(rebuild(json.load(open(sys.argv[2]))), ensure_ascii=False, indent=1, default=str))
