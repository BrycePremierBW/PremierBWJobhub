"""Minimal restart-safe smoke test for the lean JobHub core."""

from __future__ import annotations

import tempfile
from pathlib import Path

from jobhub_lean.db import Database
from jobhub_lean.schema import ensure_schema


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        db_path = Path(temporary) / "jobhub.db"
        db = Database("", str(db_path))
        ensure_schema(db)
        ensure_schema(db)

        builder_id = db.insert_id(
            "INSERT INTO builders_clients(type,name) VALUES (?,?)",
            ("Builder", "Lean Smoke Builder"),
        )
        employee_id = db.insert_id(
            "INSERT INTO employees(name,role,status) VALUES (?,?,?)",
            ("Lean Smoke Painter", "Painter", "Active"),
        )
        job_id = db.insert_id(
            """
            INSERT INTO jobs
            (job_no,job_name,builder_client_id,status,contract_value,row_version)
            VALUES (?,?,?,?,?,1)
            """,
            ("SMOKE-001", "Lean Smoke Job", builder_id, "Active", 1000),
        )
        user_id = db.insert_id(
            """
            INSERT INTO app_users
            (username,password_hash,role,employee_id,active)
            VALUES (?,?,?,?,1)
            """,
            ("smoke-admin", "test-only", "admin", employee_id),
        )

        assert builder_id > 0
        assert employee_id > 0
        assert job_id > 0
        assert user_id > 0
        assert int(db.scalar("SELECT COUNT(*) FROM jobs", default=0)) == 1
        assert db.table_exists("estimate_working_sheets")
        assert db.table_exists("timesheet_entries")
        assert db.table_exists("job_documents")

    print("lean schema smoke: OK")


if __name__ == "__main__":
    main()
