from __future__ import annotations

import importlib.util
from pathlib import Path
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "jobhub_po_upload_performance_guard_test",
    ROOT / "jobhub" / "po_upload_performance_guard.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


READY_SCHEMA = {
    "job_documents": {"id", "job_id", "file_name", "file_path"},
    "job_purchase_orders": {"id", "job_id", "po_number", "amount_ex_gst"},
}


class PoUploadPerformanceGuardTests(unittest.TestCase):
    def setUp(self):
        MODULE._SCHEMA_READY = False
        MODULE._SPLIT_CONSTRAINT_READY = False

    @staticmethod
    def fake_po(schema=None, migration=None):
        schema = schema if schema is not None else {
            table: set(columns) for table, columns in READY_SCHEMA.items()
        }
        column_calls = []

        def table_columns(table):
            column_calls.append(table)
            return set(schema.get(table, set()))

        po = types.SimpleNamespace(
            _table_columns=table_columns,
            _ensure_schema=migration or (lambda: None),
            _use_postgres=lambda: False,
            _save_uploaded_file=lambda job_id, po_number, uploaded: (
                uploaded.name,
                f"/tmp/{uploaded.name}",
            ),
        )
        return po, schema, column_calls

    def test_ready_core_tables_skip_page_render_migration(self):
        migration_calls = []
        po, _, column_calls = self.fake_po(
            migration=lambda: migration_calls.append("migration")
        )

        MODULE._patch_table_columns(po)
        MODULE._patch_schema_check(po)
        po._ensure_schema()
        po._ensure_schema()

        self.assertEqual(migration_calls, [])
        self.assertEqual(column_calls.count("job_documents"), 1)
        self.assertEqual(column_calls.count("job_purchase_orders"), 1)

    def test_missing_core_tables_run_legacy_migration_once(self):
        schema = {"job_documents": set(), "job_purchase_orders": set()}
        migration_calls = []

        def migrate():
            migration_calls.append("migration")
            schema.update(
                {table: set(columns) for table, columns in READY_SCHEMA.items()}
            )

        po, _, _ = self.fake_po(schema=schema, migration=migrate)
        MODULE._patch_table_columns(po)
        MODULE._patch_schema_check(po)

        po._ensure_schema()
        po._ensure_schema()
        self.assertEqual(migration_calls, ["migration"])

    def test_column_discovery_is_cached_for_repeated_po_page_reruns(self):
        po, _, column_calls = self.fake_po()
        MODULE._patch_table_columns(po)

        self.assertEqual(po._table_columns("job_purchase_orders"), READY_SCHEMA["job_purchase_orders"])
        self.assertEqual(po._table_columns("job_purchase_orders"), READY_SCHEMA["job_purchase_orders"])
        self.assertEqual(column_calls, ["job_purchase_orders"])

        po._table_columns.cache_clear()
        po._table_columns("job_purchase_orders")
        self.assertEqual(column_calls, ["job_purchase_orders", "job_purchase_orders"])

    def test_oversized_po_file_is_rejected_before_storage_write(self):
        save_calls = []
        po, _, _ = self.fake_po()

        def save(job_id, po_number, uploaded):
            save_calls.append((job_id, po_number))
            return uploaded.name, "/tmp/file"

        po._save_uploaded_file = save
        MODULE._patch_file_save(po)
        uploaded = types.SimpleNamespace(
            name="large-po.pdf",
            size=MODULE.MAX_PO_UPLOAD_BYTES + 1,
        )

        with self.assertRaisesRegex(ValueError, "25 MB"):
            po._save_uploaded_file(1, "PO-1", uploaded)
        self.assertEqual(save_calls, [])

    def test_split_constraint_change_runs_only_on_first_split_save(self):
        relax_calls = []
        record_calls = []
        po = types.SimpleNamespace(_use_postgres=lambda: True)

        def relax(po_module):
            relax_calls.append(po_module)

        def record(*args, **kwargs):
            record_calls.append((args, kwargs))
            return "saved"

        split = types.SimpleNamespace(
            _relax_po_number_uniqueness=relax,
            _record_po_line=record,
        )
        MODULE._patch_split_constraint(split, po)

        # These represent install-time and page-render calls in the split guard.
        split._relax_po_number_uniqueness(po)
        split._relax_po_number_uniqueness(po)
        self.assertEqual(relax_calls, [])

        self.assertEqual(split._record_po_line(po, job_id=1), "saved")
        self.assertEqual(split._record_po_line(po, job_id=1), "saved")
        self.assertEqual(len(relax_calls), 1)
        self.assertEqual(len(record_calls), 2)

    def test_performance_guard_installs_before_split_guard(self):
        init_source = (ROOT / "jobhub" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn(
            "from .po_upload_performance_guard import install_po_upload_performance_guard",
            init_source,
        )
        self.assertLess(
            init_source.index("install_po_upload_performance_guard()"),
            init_source.index("install_po_upload_split_guard()"),
        )


if __name__ == "__main__":
    unittest.main()
