"""The PlanReader<->JobHub bridge must stay portable to Postgres/Supabase.

Render runs JobHub against the shared database, which uses Postgres when
DATABASE_URL is set.  We cannot spin up a real Postgres server here, so this
test statically validates every SQL statement in planreader_bridge.py after the
? -> %s / AUTOINCREMENT -> SERIAL adaptation using sqlglot's Postgres parser.
"""
import importlib.util
import inspect
import re
import sys
from pathlib import Path

import pytest

sqlglot = pytest.importorskip("sqlglot")

REPO_ROOT = Path(__file__).resolve().parent.parent
BRIDGE_PATH = REPO_ROOT / "jobhub" / "planreader_bridge.py"


def _load_bridge():
    spec = importlib.util.spec_from_file_location("planreader_bridge_pg_test", BRIDGE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _collect_statements(bridge):
    statements = list(bridge._SCHEMA_STATEMENTS)
    for name in dir(bridge):
        fn = getattr(bridge, name)
        if not callable(fn) or getattr(fn, "__module__", "") != bridge.__name__:
            continue
        try:
            body = inspect.getsource(fn)
        except (OSError, TypeError):
            continue
        for match in re.finditer(r'(?:df_query|execute|execute_many)\(\s*"""\n?(.*?)\n?"""', body, re.S):
            statements.append(match.group(1).strip())
    return statements


def test_adapt_for_postgres_rewrites_placeholders():
    bridge = _load_bridge()
    adapted = bridge._adapt_for_postgres(
        "INSERT INTO t (id, n) VALUES (?, ?) "
        "ON CONFLICT (job_id, area_location) DO UPDATE SET n = excluded.n"
    )
    assert "? " not in adapted and "?," not in adapted
    assert "%s" in adapted
    assert "excluded.n" in adapted


def test_adapt_for_postgres_rewrites_autoincrement():
    bridge = _load_bridge()
    adapted = bridge._adapt_for_postgres("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, x TEXT)")
    assert "SERIAL PRIMARY KEY" in adapted


def _placeholder_count(sql):
    """Count psycopg2 placeholders (%s), ignoring escaped %%.

    A ragged %% raises, indicating a stray literal % left unescaped.
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


def test_adapt_for_postgres_escapes_literal_percent_in_like_patterns():
    # Regression: a literal % followed by "s" (e.g. the first % of '%soffit%')
    # must be escaped to %% so psycopg2 does not mistake it for a placeholder
    # ("IndexError: tuple index out of range").
    bridge = _load_bridge()
    sql = "SELECT * FROM estimate_lines WHERE LOWER(item_description) LIKE '%soffit%' AND estimate_id=?"
    adapted = bridge._adapt_for_postgres(sql)
    assert _placeholder_count(adapted) == 1  # only the ? placeholder
    assert "%%soffit%%" in adapted


def test_adapt_for_postgres_escapes_literal_percent_in_aliases():
    bridge = _load_bridge()
    sql = "SELECT rate + 10 AS 'Rate + 10%' FROM x WHERE id=?"
    adapted = bridge._adapt_for_postgres(sql)
    assert _placeholder_count(adapted) == 1
    assert "Rate + 10%%" in adapted


def test_every_bridge_statement_parses_as_postgres():
    bridge = _load_bridge()
    statements = _collect_statements(bridge)
    assert statements, "no SQL statements collected from the bridge"
    for index, sql in enumerate(statements):
        adapted = bridge._adapt_for_postgres(sql)
        # Raises on syntax errors.
        sqlglot.transpile(adapted, read="postgres", write="postgres")
