import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def db_adapter():
    import jobhub.database as db
    original = db.USE_POSTGRES
    db.USE_POSTGRES = True
    try:
        yield db
    finally:
        db.USE_POSTGRES = original


def _placeholder_count(sql):
    """Count psycopg2 placeholders (%s), ignoring escaped %%.

    Ragged %% would raise; that indicates a stray placeholder.
    """
    count = 0
    i = 0
    while i < len(sql):
        if sql[i] == "%":
            if i + 1 < len(sql) and sql[i + 1] == "%":
                i += 2
                continue
            if i + 1 < len(sql) and sql[i + 1] == "s":
                count += 1
                i += 2
                continue
            raise AssertionError("stray literal % in adapted SQL: " + sql)
        i += 1
    return count


def test_adapter_escapes_literal_percent_followed_by_s(db_adapter):
    sql = "SELECT * FROM estimate_lines WHERE LOWER(item_description) LIKE '%soffit%' AND estimate_id=?"
    adapted = db_adapter.adapt_sql_for_postgres(sql)
    assert _placeholder_count(adapted) == 1
    assert "%%soffit%%" in adapted


def test_adapter_escapes_literal_percent_in_alias(db_adapter):
    sql = "SELECT rate + 10 AS 'Rate + 10%' FROM x WHERE id=?"
    adapted = db_adapter.adapt_sql_for_postgres(sql)
    assert _placeholder_count(adapted) == 1
    assert "Rate + 10%%" in adapted


def test_adapter_leaves_multiple_placeholders_intact(db_adapter):
    sql = "SELECT * FROM t WHERE a=? AND b=? AND c LIKE '%sq m%'"
    adapted = db_adapter.adapt_sql_for_postgres(sql)
    assert _placeholder_count(adapted) == 2


def test_adapter_pass_through_when_not_postgres(db_adapter):
    db_adapter.USE_POSTGRES = False
    sql = "SELECT * FROM t WHERE a=? AND b LIKE '%x%'"
    assert db_adapter.adapt_sql_for_postgres(sql) == sql
