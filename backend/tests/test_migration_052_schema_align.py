"""D-178: migrations/052 выравнивает схему рабочей базы с кодом и пропускает «грязные» места, ничего не ломая."""
from pathlib import Path

from tests.test_api_flow import client  # noqa: F401  — поднимает базу теста

SQL = Path(__file__).resolve().parents[1] / "migrations" / "052_schema_align_prod.sql"

ORPHAN = """
DO $$ DECLARE cols text:=''; vals text:=''; r record; BEGIN
 FOR r IN SELECT column_name, data_type FROM information_schema.columns
          WHERE table_schema=current_schema() AND table_name='factories' AND is_nullable='NO' AND column_default IS NULL LOOP
  cols:=cols||quote_ident(r.column_name)||',';
  vals:=vals||CASE WHEN r.column_name='id' THEN '''f-orphan-052'''
       WHEN r.data_type IN ('json','jsonb','ARRAY') THEN '''{}'''
       WHEN r.data_type LIKE 'timestamp%' THEN 'now()'
       WHEN r.data_type IN ('integer','numeric','double precision','bigint','smallint','real') THEN '0'
       WHEN r.data_type='boolean' THEN 'false' ELSE '''x''' END||',';
 END LOOP;
 EXECUTE format('INSERT INTO factories (%s legal_entity_id) VALUES (%s ''no-such-entity'')', cols, vals);
END $$;
"""


def _exec(sql: str) -> list[str]:
    from app.db import engine
    proxy = engine.raw_connection()
    try:
        raw = proxy.driver_connection
        raw.rollback()
        raw.autocommit = True
        notices: list[str] = []
        def h(d):
            notices.append(d.message_primary or "")
        raw.add_notice_handler(h)
        try:
            raw.execute(sql)
        finally:
            raw.remove_notice_handler(h)
            raw.autocommit = False
        return notices
    finally:
        proxy.close()


def _check() -> dict:
    from scripts.schema_check import collect
    return collect()


def test_052_restores_and_is_idempotent(client):  # noqa: F811
    assert not _check()["missing_fks"]
    _exec("""
      ALTER TABLE equipment_models DROP CONSTRAINT IF EXISTS equipment_models_approved_by_fkey;
      DROP INDEX IF EXISTS ix_po_lines_batch_id;
      ALTER TABLE deals ALTER COLUMN number DROP NOT NULL;
    """)
    r = _check()
    assert "equipment_models(approved_by) → users" in r["missing_fks"]
    assert any("deals.number" in x for x in r["null_diffs"])
    _exec(SQL.read_text())
    r = _check()
    assert not r["missing_fks"] and not r["missing_indexes"] and not r["null_diffs"]
    _exec(SQL.read_text())  # повторный прогон ничего не ломает
    assert not _check()["missing_fks"]


def test_052_skips_dirty_data_and_keeps_rows(client):  # noqa: F811
    _exec("ALTER TABLE factories DROP CONSTRAINT IF EXISTS factories_legal_entity_id_fkey;"
          "ALTER TABLE factories ALTER COLUMN name_zh DROP NOT NULL;" + ORPHAN +
          "UPDATE factories SET name_zh=NULL WHERE id='f-orphan-052';")
    try:
        notices = " | ".join(_exec(SQL.read_text()))
        assert "factories.legal_entity_id → legal_entities пропущен" in notices
        assert "factories.name_zh пропущен" in notices
        r = _check()
        assert "factories(legal_entity_id) → legal_entities" in r["missing_fks"]
        assert any("factories.name_zh" in x for x in r["null_diffs"])
        # строка не удалена и не изменена
        from app.db import engine
        from sqlalchemy import text
        with engine.connect() as c:
            row = c.execute(text("select legal_entity_id, name_zh from factories where id='f-orphan-052'")).one()
        assert row == ("no-such-entity", None)
    finally:
        _exec("DELETE FROM factories WHERE id='f-orphan-052';")
    _exec(SQL.read_text())  # данные почищены — теперь всё применяется
    r = _check()
    assert not r["missing_fks"] and not r["null_diffs"]
