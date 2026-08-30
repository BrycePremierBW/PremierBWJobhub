from jobhub import organization_schema_guard as guard


def test_slugify_organization_name_is_stable_and_url_safe():
    assert guard.slugify_organization_name("Coastal Coatings Pty Ltd") == "coastal-coatings-pty-ltd"
    assert guard.slugify_organization_name("  Nick & Bryce Painting!  ") == "nick-bryce-painting"
    assert guard.slugify_organization_name("") == ""


def test_schema_creation_is_non_destructive_and_seeds_pb(monkeypatch):
    calls = []
    monkeypatch.setattr(guard, "_use_postgres", lambda: False)
    monkeypatch.setattr(guard, "_execute", lambda sql, params=(): calls.append((sql, params)))

    assert guard.ensure_organization_schema() is True
    sql = "\n".join(statement for statement, _ in calls)
    assert "CREATE TABLE IF NOT EXISTS organizations" in sql
    assert "CREATE TABLE IF NOT EXISTS organization_settings" in sql
    assert "CREATE TABLE IF NOT EXISTS organization_integrations" in sql
    assert "ALTER TABLE" not in sql
    assert "DROP TABLE" not in sql
    assert any(
        params == (guard.DEFAULT_ORGANIZATION_SLUG, guard.DEFAULT_ORGANIZATION_NAME, guard.DEFAULT_ORGANIZATION_NAME)
        for _, params in calls
    )


def test_postgres_uses_serial_primary_keys(monkeypatch):
    calls = []
    monkeypatch.setattr(guard, "_use_postgres", lambda: True)
    monkeypatch.setattr(guard, "_execute", lambda sql, params=(): calls.append((sql, params)))
    guard.ensure_organization_schema()
    assert "SERIAL PRIMARY KEY" in calls[0][0]


def test_get_organization_id_returns_existing_id(monkeypatch):
    class FakeFrame:
        empty = False
        iloc = None

    import pandas as pd

    monkeypatch.setattr(guard, "_df_query", lambda sql, params=(): pd.DataFrame([{"id": 17}]))
    assert guard.get_organization_id("coastal-coatings") == 17


def test_install_guard_has_no_import_time_database_side_effects():
    assert guard.install_organization_schema_guard() is True
