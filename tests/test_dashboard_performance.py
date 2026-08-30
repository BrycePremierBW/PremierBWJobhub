import pandas as pd

from jobhub.pages import dashboard


def test_dashboard_counts_uses_two_queries_and_combines_metrics(monkeypatch):
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        if "FROM jobs" in sql and "jobs_count" in sql:
            return pd.DataFrame([{"jobs_count": 12, "active_jobs_count": 7}])
        return pd.DataFrame([{
            "pending_timesheets": 3,
            "open_variations": 2,
            "overdue_claims": 1,
            "overdue_value": 1250.0,
        }])

    monkeypatch.setattr(dashboard, "df_query", fake_query)
    monkeypatch.setattr(dashboard, "jobhub_today", lambda: "2026-08-31")

    result = dashboard._dashboard_counts()

    assert len(calls) == 2
    assert result == {
        "jobs_count": 12,
        "active_jobs_count": 7,
        "pending_timesheets": 3,
        "open_variations": 2,
        "overdue_claims": 1,
        "overdue_value": 1250.0,
    }


def test_dashboard_counts_keeps_core_counts_if_optional_tables_are_unavailable(monkeypatch):
    calls = []

    def fake_query(sql, params=()):
        calls.append(sql)
        if len(calls) == 1:
            return pd.DataFrame([{"jobs_count": 4, "active_jobs_count": 2}])
        raise RuntimeError("legacy schema")

    monkeypatch.setattr(dashboard, "df_query", fake_query)
    monkeypatch.setattr(dashboard, "jobhub_today", lambda: "2026-08-31")

    result = dashboard._dashboard_counts()

    assert result["jobs_count"] == 4
    assert result["active_jobs_count"] == 2
    assert result["pending_timesheets"] == 0
    assert result["open_variations"] == 0
    assert result["overdue_claims"] == 0
    assert result["overdue_value"] == 0.0


def test_dashboard_detail_views_are_selected_instead_of_eager_tabs():
    source = (dashboard.__file__ and open(dashboard.__file__, encoding="utf-8").read())
    assert "st.tabs(" not in source
    assert 'key="dashboard_work_overview"' in source
    assert "_render_open_jobs()" in source
    assert "_render_upcoming_work()" in source
    assert "_render_attention(pending_timesheets, overdue_claims)" in source
