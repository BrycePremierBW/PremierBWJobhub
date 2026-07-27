"""Restart-safe portable schema for JobHub V4 painting workflows."""

from __future__ import annotations


V4_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS paint_systems (
        id TEXT PRIMARY KEY,
        job_id INTEGER NOT NULL,
        area_name TEXT NOT NULL,
        substrate TEXT,
        product_name TEXT NOT NULL,
        colour_name TEXT,
        area_sqm NUMERIC NOT NULL,
        coat_count INTEGER NOT NULL,
        coverage_sqm_per_litre NUMERIC NOT NULL,
        waste_percent NUMERIC NOT NULL DEFAULT 10,
        required_litres NUMERIC NOT NULL,
        pack_plan_json TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS colour_approvals (
        id TEXT PRIMARY KEY,
        job_id INTEGER NOT NULL,
        area_name TEXT NOT NULL,
        colour_name TEXT NOT NULL,
        colour_code TEXT,
        product_name TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        requested_by TEXT,
        requested_at TEXT,
        approved_by TEXT,
        approved_at TEXT,
        approval_reference TEXT,
        notes TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS plan_evidence (
        id TEXT PRIMARY KEY,
        job_id INTEGER NOT NULL,
        plan_reference TEXT NOT NULL,
        revision TEXT,
        location_reference TEXT,
        evidence_type TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        photo_id INTEGER,
        document_id INTEGER,
        status TEXT NOT NULL DEFAULT 'active',
        captured_by TEXT,
        captured_at TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS drawing_revisions (
        id TEXT PRIMARY KEY,
        job_id INTEGER NOT NULL,
        document_name TEXT NOT NULL,
        previous_revision TEXT,
        current_revision TEXT,
        similarity_percent NUMERIC,
        variation_risk_score INTEGER NOT NULL DEFAULT 0,
        comparison_json TEXT NOT NULL,
        compared_by TEXT,
        compared_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS variation_suggestions (
        id TEXT PRIMARY KEY,
        job_id INTEGER NOT NULL,
        drawing_revision_id TEXT,
        title TEXT NOT NULL,
        reason TEXT NOT NULL,
        risk_score INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'draft',
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS handover_packs (
        id TEXT PRIMARY KEY,
        job_id INTEGER NOT NULL,
        status TEXT NOT NULL,
        manifest_json TEXT NOT NULL,
        generated_by TEXT,
        generated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_paint_systems_job ON paint_systems(job_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_colour_approvals_job ON colour_approvals(job_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_plan_evidence_job ON plan_evidence(job_id, evidence_type)",
    "CREATE INDEX IF NOT EXISTS idx_drawing_revisions_job ON drawing_revisions(job_id, compared_at)",
    "CREATE INDEX IF NOT EXISTS idx_variation_suggestions_job ON variation_suggestions(job_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_handover_packs_job ON handover_packs(job_id, generated_at)",
)


def ensure_v4_schema(connection_factory) -> None:
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        for statement in V4_SCHEMA_STATEMENTS:
            cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()
