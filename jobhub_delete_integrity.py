"""Referential-integrity guards for destructive JobHub deletes.

JobHub has grown several linked operational tables outside the original main
schema.  Some of those tables point back to timesheets, estimates, materials or
jobs with restrictive foreign keys.  A permanent job delete must therefore
unlink reference-only relationships first and remove truly job-owned rows before
the parent job disappears.

The guards live at database level so every delete path gets the same behaviour,
not only the Streamlit button that originally exposed the problem.
"""
from __future__ import annotations

from typing import Any, Callable


DELETE_INTEGRITY_VERSION = "2026.08.20-job-delete-integrity-v1"


def _is_postgres_connection(conn: Any) -> bool:
    if conn.__class__.__name__ == "PostgresConnectionAdapter":
        return True
    raw = getattr(conn, "conn", None)
    module = getattr(getattr(raw, "__class__", None), "__module__", "")
    if str(module).startswith(("psycopg2", "psycopg")):
        return True
    module = getattr(conn.__class__, "__module__", "")
    return str(module).startswith(("psycopg2", "psycopg"))


def _postgres_cursor(conn: Any):
    """Use the raw psycopg cursor so PL/pgSQL is not rewritten by app SQL adapters."""
    raw = getattr(conn, "conn", None)
    if raw is not None and hasattr(raw, "cursor"):
        return raw.cursor()
    return conn.cursor()


def _ensure_postgres(cur: Any) -> None:
    # Reference-only links are unhooked before the referenced row is deleted.
    cur.execute(
        """
        CREATE OR REPLACE FUNCTION pb_jobhub_before_delete_unlink()
        RETURNS trigger AS $$
        BEGIN
            IF TG_TABLE_NAME = 'timesheet_entries' THEN
                IF to_regclass('public.field_clock_entries') IS NOT NULL THEN
                    EXECUTE 'UPDATE field_clock_entries SET submitted_timesheet_id = NULL WHERE submitted_timesheet_id = $1'
                    USING OLD.id;
                END IF;
            ELSIF TG_TABLE_NAME = 'estimate_line_items' THEN
                IF to_regclass('public.job_external_progress') IS NOT NULL THEN
                    EXECUTE 'UPDATE job_external_progress SET estimate_line_id = NULL WHERE estimate_line_id = $1'
                    USING OLD.id;
                END IF;
            ELSIF TG_TABLE_NAME = 'estimate_working_sheets' THEN
                IF to_regclass('public.job_progress_settings') IS NOT NULL THEN
                    EXECUTE 'UPDATE job_progress_settings SET linked_estimate_id = NULL WHERE linked_estimate_id = $1'
                    USING OLD.id;
                END IF;
            ELSIF TG_TABLE_NAME = 'material_entries' THEN
                IF to_regclass('public.purchase_order_lines') IS NOT NULL THEN
                    EXECUTE 'UPDATE purchase_order_lines SET material_entry_id = NULL WHERE material_entry_id = $1'
                    USING OLD.id;
                END IF;
            ELSIF TG_TABLE_NAME = 'purchase_order_lines' THEN
                IF to_regclass('public.supplier_invoice_lines') IS NOT NULL THEN
                    EXECUTE 'UPDATE supplier_invoice_lines SET matched_po_line_id = NULL WHERE matched_po_line_id = $1'
                    USING OLD.id;
                END IF;
            ELSIF TG_TABLE_NAME = 'purchase_orders' THEN
                IF to_regclass('public.supplier_invoices') IS NOT NULL THEN
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

    # These are rows whose lifecycle belongs to the job but which are outside
    # the original JOB_DIRECT_CHILD_TABLES list in pb_jobhub_app.py.
    cur.execute(
        """
        CREATE OR REPLACE FUNCTION pb_jobhub_cleanup_auxiliary_job_rows()
        RETURNS trigger AS $$
        BEGIN
            IF to_regclass('public.supplier_invoice_lines') IS NOT NULL
               AND to_regclass('public.supplier_invoices') IS NOT NULL THEN
                EXECUTE 'DELETE FROM supplier_invoice_lines WHERE supplier_invoice_id IN (SELECT id FROM supplier_invoices WHERE job_id = $1)'
                USING OLD.id;
            END IF;
            IF to_regclass('public.supplier_invoices') IS NOT NULL THEN
                EXECUTE 'DELETE FROM supplier_invoices WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('public.purchase_order_lines') IS NOT NULL
               AND to_regclass('public.purchase_orders') IS NOT NULL THEN
                EXECUTE 'DELETE FROM purchase_order_lines WHERE purchase_order_id IN (SELECT id FROM purchase_orders WHERE job_id = $1)'
                USING OLD.id;
            END IF;
            IF to_regclass('public.purchase_orders') IS NOT NULL THEN
                EXECUTE 'DELETE FROM purchase_orders WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('public.field_clock_entries') IS NOT NULL THEN
                EXECUTE 'DELETE FROM field_clock_entries WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('public.job_progress_snapshots') IS NOT NULL THEN
                EXECUTE 'DELETE FROM job_progress_snapshots WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('public.field_forms') IS NOT NULL THEN
                EXECUTE 'DELETE FROM field_forms WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('public.job_external_progress') IS NOT NULL THEN
                EXECUTE 'DELETE FROM job_external_progress WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('public.job_dwelling_progress') IS NOT NULL THEN
                EXECUTE 'DELETE FROM job_dwelling_progress WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('public.job_progress_settings') IS NOT NULL THEN
                EXECUTE 'DELETE FROM job_progress_settings WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('public.offline_sync_events') IS NOT NULL THEN
                EXECUTE 'DELETE FROM offline_sync_events WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('public.variation_suggestions') IS NOT NULL THEN
                EXECUTE 'DELETE FROM variation_suggestions WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('public.drawing_revisions') IS NOT NULL THEN
                EXECUTE 'DELETE FROM drawing_revisions WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('public.plan_evidence') IS NOT NULL THEN
                EXECUTE 'DELETE FROM plan_evidence WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('public.colour_approvals') IS NOT NULL THEN
                EXECUTE 'DELETE FROM colour_approvals WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('public.paint_systems') IS NOT NULL THEN
                EXECUTE 'DELETE FROM paint_systems WHERE job_id = $1' USING OLD.id;
            END IF;
            IF to_regclass('public.handover_packs') IS NOT NULL THEN
                EXECUTE 'DELETE FROM handover_packs WHERE job_id = $1' USING OLD.id;
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


def _ensure_sqlite(cur: Any) -> None:
    trigger_sql = {
        "pb_jobhub_unlink_timesheet_delete": """
            CREATE TRIGGER pb_jobhub_unlink_timesheet_delete
            BEFORE DELETE ON timesheet_entries
            BEGIN
                UPDATE field_clock_entries
                SET submitted_timesheet_id = NULL
                WHERE submitted_timesheet_id = OLD.id;
            END
        """,
        "pb_jobhub_unlink_estimate_line_delete": """
            CREATE TRIGGER pb_jobhub_unlink_estimate_line_delete
            BEFORE DELETE ON estimate_line_items
            BEGIN
                UPDATE job_external_progress
                SET estimate_line_id = NULL
                WHERE estimate_line_id = OLD.id;
            END
        """,
        "pb_jobhub_unlink_estimate_delete": """
            CREATE TRIGGER pb_jobhub_unlink_estimate_delete
            BEFORE DELETE ON estimate_working_sheets
            BEGIN
                UPDATE job_progress_settings
                SET linked_estimate_id = NULL
                WHERE linked_estimate_id = OLD.id;
            END
        """,
        "pb_jobhub_unlink_material_delete": """
            CREATE TRIGGER pb_jobhub_unlink_material_delete
            BEFORE DELETE ON material_entries
            BEGIN
                UPDATE purchase_order_lines
                SET material_entry_id = NULL
                WHERE material_entry_id = OLD.id;
            END
        """,
        "pb_jobhub_unlink_po_line_delete": """
            CREATE TRIGGER pb_jobhub_unlink_po_line_delete
            BEFORE DELETE ON purchase_order_lines
            BEGIN
                UPDATE supplier_invoice_lines
                SET matched_po_line_id = NULL
                WHERE matched_po_line_id = OLD.id;
            END
        """,
        "pb_jobhub_unlink_po_delete": """
            CREATE TRIGGER pb_jobhub_unlink_po_delete
            BEFORE DELETE ON purchase_orders
            BEGIN
                UPDATE supplier_invoices
                SET purchase_order_id = NULL
                WHERE purchase_order_id = OLD.id;
            END
        """,
        "pb_jobhub_cleanup_job_delete": """
            CREATE TRIGGER pb_jobhub_cleanup_job_delete
            BEFORE DELETE ON jobs
            BEGIN
                DELETE FROM supplier_invoice_lines
                 WHERE supplier_invoice_id IN (SELECT id FROM supplier_invoices WHERE job_id = OLD.id);
                DELETE FROM supplier_invoices WHERE job_id = OLD.id;
                DELETE FROM purchase_order_lines
                 WHERE purchase_order_id IN (SELECT id FROM purchase_orders WHERE job_id = OLD.id);
                DELETE FROM purchase_orders WHERE job_id = OLD.id;
                DELETE FROM field_clock_entries WHERE job_id = OLD.id;
                DELETE FROM job_progress_snapshots WHERE job_id = OLD.id;
                DELETE FROM field_forms WHERE job_id = OLD.id;
                DELETE FROM job_external_progress WHERE job_id = OLD.id;
                DELETE FROM job_dwelling_progress WHERE job_id = OLD.id;
                DELETE FROM job_progress_settings WHERE job_id = OLD.id;
                DELETE FROM offline_sync_events WHERE job_id = OLD.id;
                DELETE FROM variation_suggestions WHERE job_id = OLD.id;
                DELETE FROM drawing_revisions WHERE job_id = OLD.id;
                DELETE FROM plan_evidence WHERE job_id = OLD.id;
                DELETE FROM colour_approvals WHERE job_id = OLD.id;
                DELETE FROM paint_systems WHERE job_id = OLD.id;
                DELETE FROM handover_packs WHERE job_id = OLD.id;
            END
        """,
    }
    for name, sql in trigger_sql.items():
        cur.execute(f"DROP TRIGGER IF EXISTS {name}")
        cur.execute(sql)


def ensure_job_delete_integrity(connection_factory: Callable[[], Any]) -> bool:
    """Install idempotent delete guards for the current JobHub database."""
    conn = connection_factory()
    cur = None
    try:
        if _is_postgres_connection(conn):
            cur = _postgres_cursor(conn)
            _ensure_postgres(cur)
        else:
            cur = conn.cursor()
            _ensure_sqlite(cur)
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
