"""PostgreSQL referential-integrity guards for destructive JobHub deletes.

Production JobHub runs on PostgreSQL. Newer operational tables introduced links
that are outside the original permanent-job-delete order in pb_jobhub_app.py.
These restart-safe database guards unlink reference-only relationships and remove
newer job-owned rows before restrictive foreign keys can block deletion.

SQLite is deliberately left unchanged: its lightweight test/local schemas are
frequently partial, and SQLite resolves trigger table references differently.
"""
from __future__ import annotations

from typing import Any, Callable


DELETE_INTEGRITY_VERSION = "2026.08.24-job-delete-integrity-v3"


def _raw_connection(conn: Any) -> Any:
    for attr in ("conn", "_connection", "connection"):
        raw = getattr(conn, attr, None)
        if raw is not None and raw is not conn:
            return raw
    return conn


def _is_postgres_connection(conn: Any) -> bool:
    if conn.__class__.__name__ == "PostgresConnectionAdapter":
        return True
    for candidate in (conn, _raw_connection(conn)):
        module = str(getattr(getattr(candidate, "__class__", None), "__module__", ""))
        name = str(getattr(getattr(candidate, "__class__", None), "__name__", ""))
        if module.startswith(("psycopg2", "psycopg")) or "postgres" in name.lower():
            return True
    return False


def _postgres_cursor(conn: Any):
    """Use raw psycopg so PL/pgSQL is not rewritten by JobHub's SQL adapter."""
    return _raw_connection(conn).cursor()


def _ensure_postgres(cur: Any) -> None:
    cur.execute(
        """
        CREATE OR REPLACE FUNCTION pb_jobhub_before_delete_unlink()
        RETURNS trigger AS $$
        BEGIN
            IF TG_TABLE_NAME = 'timesheet_entries' THEN
                IF to_regclass('field_clock_entries') IS NOT NULL THEN
                    EXECUTE 'UPDATE field_clock_entries SET submitted_timesheet_id = NULL WHERE submitted_timesheet_id = $1'
                    USING OLD.id;
                END IF;
            ELSIF TG_TABLE_NAME = 'estimate_line_items' THEN
                IF to_regclass('job_external_progress') IS NOT NULL THEN
                    EXECUTE 'UPDATE job_external_progress SET estimate_line_id = NULL WHERE estimate_line_id = $1'
                    USING OLD.id;
                END IF;
            ELSIF TG_TABLE_NAME = 'estimate_working_sheets' THEN
                IF to_regclass('job_progress_settings') IS NOT NULL THEN
                    EXECUTE 'UPDATE job_progress_settings SET linked_estimate_id = NULL WHERE linked_estimate_id = $1'
                    USING OLD.id;
                END IF;
            ELSIF TG_TABLE_NAME = 'material_entries' THEN
                IF to_regclass('purchase_order_lines') IS NOT NULL THEN
                    EXECUTE 'UPDATE purchase_order_lines SET material_entry_id = NULL WHERE material_entry_id = $1'
                    USING OLD.id;
                END IF;
            ELSIF TG_TABLE_NAME = 'purchase_order_lines' THEN
                IF to_regclass('supplier_invoice_lines') IS NOT NULL THEN
                    EXECUTE 'UPDATE supplier_invoice_lines SET matched_po_line_id = NULL WHERE matched_po_line_id = $1'
                    USING OLD.id;
                END IF;
            ELSIF TG_TABLE_NAME = 'purchase_orders' THEN
                IF to_regclass('supplier_invoices') IS NOT NULL THEN
                    EXECUTE 'UPDATE supplier_invoices SET purchase_order_id = NULL WHERE purchase_order_id = $1'
                    USING OLD.id;
                END IF;
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
        """
    )

    trigger_sources = (
        ("timesheet_entries", "pb_jobhub_unlink_timesheet_delete"),
        ("estimate_line_items", "pb_jobhub_unlink_estimate_line_delete"),
        ("estimate_working_sheets", "pb_jobhub_unlink_estimate_delete"),
        ("material_entries", "pb_jobhub_unlink_material_delete"),
        ("purchase_order_lines", "pb_jobhub_unlink_po_line_delete"),
        ("purchase_orders", "pb_jobhub_unlink_po_delete"),
    )
    for table, trigger in trigger_sources:
        cur.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        cur.execute(
            f"CREATE TRIGGER {trigger} BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION pb_jobhub_before_delete_unlink()"
        )

    cur.execute(
        """
        CREATE OR REPLACE FUNCTION pb_jobhub_cleanup_auxiliary_job_rows()
        RETURNS trigger AS $$
        BEGIN
            IF to_regclass('supplier_invoice_lines') IS NOT NULL
               AND to_regclass('supplier_invoices') IS NOT NULL THEN
                EXECUTE 'DELETE FROM supplier_invoice_lines WHERE supplier_invoice_id IN (SELECT id FROM supplier_invoices WHERE job_id = $1)'
                USING OLD.id;
            END IF;
            IF to_regclass('supplier_invoices') IS NOT NULL THEN
                EXECUTE 'DELETE FROM supplier_invoices WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('purchase_order_lines') IS NOT NULL
               AND to_regclass('purchase_orders') IS NOT NULL THEN
                EXECUTE 'DELETE FROM purchase_order_lines WHERE purchase_order_id IN (SELECT id FROM purchase_orders WHERE job_id = $1)'
                USING OLD.id;
            END IF;
            IF to_regclass('purchase_orders') IS NOT NULL THEN
                EXECUTE 'DELETE FROM purchase_orders WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('field_clock_entries') IS NOT NULL THEN
                EXECUTE 'DELETE FROM field_clock_entries WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('job_progress_snapshots') IS NOT NULL THEN
                EXECUTE 'DELETE FROM job_progress_snapshots WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('field_forms') IS NOT NULL THEN
                EXECUTE 'DELETE FROM field_forms WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('job_external_progress') IS NOT NULL THEN
                EXECUTE 'DELETE FROM job_external_progress WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('job_dwelling_progress') IS NOT NULL THEN
                EXECUTE 'DELETE FROM job_dwelling_progress WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('job_progress_settings') IS NOT NULL THEN
                EXECUTE 'DELETE FROM job_progress_settings WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('offline_sync_events') IS NOT NULL THEN
                EXECUTE 'DELETE FROM offline_sync_events WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('variation_suggestions') IS NOT NULL THEN
                EXECUTE 'DELETE FROM variation_suggestions WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('drawing_revisions') IS NOT NULL THEN
                EXECUTE 'DELETE FROM drawing_revisions WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('plan_evidence') IS NOT NULL THEN
                EXECUTE 'DELETE FROM plan_evidence WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('colour_approvals') IS NOT NULL THEN
                EXECUTE 'DELETE FROM colour_approvals WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('paint_systems') IS NOT NULL THEN
                EXECUTE 'DELETE FROM paint_systems WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('handover_packs') IS NOT NULL THEN
                EXECUTE 'DELETE FROM handover_packs WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('takeoff_pack_imports') IS NOT NULL THEN
                EXECUTE 'DELETE FROM takeoff_pack_imports WHERE job_id = $1' USING OLD.id;
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    cur.execute("DROP TRIGGER IF EXISTS pb_jobhub_cleanup_job_delete ON jobs")
    cur.execute(
        """
        CREATE TRIGGER pb_jobhub_cleanup_job_delete
        BEFORE DELETE ON jobs
        FOR EACH ROW EXECUTE FUNCTION pb_jobhub_cleanup_auxiliary_job_rows()
        """
    )


def ensure_job_delete_integrity(connection_factory: Callable[[], Any]) -> bool:
    """Install idempotent guards on PostgreSQL; leave SQLite/local schemas alone."""
    conn = connection_factory()
    cur = None
    try:
        if not _is_postgres_connection(conn):
            return True
        cur = _postgres_cursor(conn)
        _ensure_postgres(cur)
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        conn.close()
