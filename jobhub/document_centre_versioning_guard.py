"""Revision history and legacy backfill for JobHub Document Centre.

The original Document Centre deliberately preserved the legacy ``job_documents``
contract.  This extension adds non-destructive version history around that
contract instead of renaming/deleting old files.

Rules:
* every physical upload remains immutable on disk;
* a later upload in the same document family becomes the current version;
* older versions remain downloadable and auditable;
* historical ``job_documents`` rows are backfilled into ``document_library``;
* restoring an older version only changes library metadata, never file bytes or
  legacy ``job_documents`` records.
"""

from __future__ import annotations

from datetime import datetime
from jobhub_time import jobhub_now
import hashlib
import mimetypes
from pathlib import Path
import re
from typing import Any

from . import document_centre_guard as base


PATCH_MARKER = "_pb_document_centre_versioning_guard"
_schema_ready = False


def _now() -> str:
    return jobhub_now().strftime("%Y-%m-%d %H:%M:%S")


def normalise_document_family_name(file_name: Any) -> str:
    """Return a stable family name while ignoring common revision suffixes."""
    name = Path(str(file_name or "document")).stem.strip().lower()
    name = re.sub(r"\s+", " ", name)
    # Treat common filename revision/version suffixes as metadata rather than a
    # new document identity: A101_Rev_B.pdf and A101_Rev_C.pdf are one family.
    suffix_pattern = re.compile(
        r"(?:[\s._-]+(?:rev(?:ision)?|ver(?:sion)?|v)[\s._-]*[a-z0-9.]+)$",
        flags=re.IGNORECASE,
    )
    previous = None
    while previous != name:
        previous = name
        name = suffix_pattern.sub("", name).strip(" ._-")
    name = re.sub(r"[^a-z0-9]+", " ", name).strip()
    return name or "document"


def document_family_key(
    *,
    category: Any,
    document_type: Any,
    entity_type: Any,
    entity_id: Any,
    job_id: Any,
    file_name: Any,
) -> str:
    identity = "|".join(
        [
            str(category or "").strip().lower(),
            str(document_type or "").strip().lower(),
            str(entity_type or "").strip().lower(),
            str(entity_id if entity_id not in (None, "") else ""),
            str(job_id if job_id not in (None, "") else ""),
            normalise_document_family_name(file_name),
        ]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _column_names(table: str) -> set[str]:
    if base._use_postgres():
        rows = base._rows(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = ?
            """,
            (table,),
        )
        return {str(row.get("column_name") or "") for row in rows}
    rows = base._rows(f"PRAGMA table_info({table})")
    names: set[str] = set()
    for row in rows:
        names.add(str(row.get("name") or row.get("1") or ""))
    return names


def _add_column_if_missing(table: str, column: str, definition: str) -> None:
    if column in _column_names(table):
        return
    base._execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _log_event(
    document_id: int | None,
    event_type: str,
    actor: str,
    detail: str = "",
    related_document_id: int | None = None,
) -> None:
    if not document_id:
        return
    base._execute(
        """
        INSERT INTO document_library_events(
            document_id, event_type, actor, detail, related_document_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            int(document_id),
            str(event_type or ""),
            str(actor or "JobHub"),
            str(detail or "")[:2000],
            related_document_id,
            _now(),
        ),
    )


def _ensure_version_columns() -> None:
    _add_column_if_missing("document_library", "document_key", "TEXT")
    _add_column_if_missing("document_library", "version_number", "INTEGER")
    _add_column_if_missing("document_library", "supersedes_document_id", "INTEGER")
    _add_column_if_missing("document_library", "is_current", "INTEGER")
    _add_column_if_missing("document_library", "superseded_at", "TEXT")
    _add_column_if_missing("document_library", "superseded_by", "TEXT")
    _add_column_if_missing("document_library", "lifecycle_status", "TEXT")
    base._execute(
        "CREATE INDEX IF NOT EXISTS idx_document_library_key ON document_library(document_key, version_number)"
    )
    base._execute(
        "CREATE INDEX IF NOT EXISTS idx_document_library_current ON document_library(document_key, is_current)"
    )

    pk = "SERIAL PRIMARY KEY" if base._use_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    base._execute(
        f"""
        CREATE TABLE IF NOT EXISTS document_library_events (
            id {pk},
            document_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            actor TEXT,
            detail TEXT,
            related_document_id INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )
    base._execute(
        "CREATE INDEX IF NOT EXISTS idx_document_library_events_document ON document_library_events(document_id, created_at)"
    )


def _backfill_legacy_job_documents() -> int:
    rows = base._rows(
        """
        SELECT jd.id AS job_document_id, jd.job_id,
               COALESCE(jd.document_type, 'Other Job Document') AS document_type,
               COALESCE(jd.file_name, '') AS file_name,
               COALESCE(jd.file_path, '') AS file_path,
               COALESCE(jd.created_at, '') AS created_at,
               COALESCE(jd.notes, '') AS notes,
               COALESCE(j.job_no, '') AS job_no,
               COALESCE(j.job_name, '') AS job_name
        FROM job_documents jd
        LEFT JOIN document_library dl ON dl.job_document_id = jd.id
        LEFT JOIN jobs j ON j.id = jd.job_id
        WHERE dl.id IS NULL
        ORDER BY jd.id
        """
    )
    inserted = 0
    for row in rows:
        file_name = str(row.get("file_name") or "document")
        file_path = str(row.get("file_path") or "")
        entity_label = " — ".join(
            part for part in (str(row.get("job_no") or "").strip(), str(row.get("job_name") or "").strip()) if part
        ) or f"Job #{row.get('job_id')}"
        mime_type = mimetypes.guess_type(file_name)[0] or ""
        base._execute(
            """
            INSERT INTO document_library(
                library_category, document_type, entity_type, entity_id, entity_label,
                job_id, job_document_id, file_name, file_path, mime_type, file_sha256,
                revision, document_date, notes, uploaded_by, source_app, created_at
            ) VALUES (?, ?, 'job', ?, ?, ?, ?, ?, ?, ?, '', '', '', ?, 'Historical backfill', 'JobHub legacy', ?)
            """,
            (
                "Job Specific",
                str(row.get("document_type") or "Other Job Document"),
                row.get("job_id"),
                entity_label,
                row.get("job_id"),
                row.get("job_document_id"),
                file_name,
                file_path,
                mime_type,
                str(row.get("notes") or ""),
                str(row.get("created_at") or _now()),
            ),
        )
        linked = base._rows(
            "SELECT id FROM document_library WHERE job_document_id=? ORDER BY id DESC LIMIT 1",
            (row.get("job_document_id"),),
        )
        if linked:
            _log_event(
                int(linked[0]["id"]),
                "historical_backfill",
                "JobHub",
                "Imported from legacy job_documents without deleting or moving the original record/file.",
            )
        inserted += 1
    return inserted


def _backfill_version_metadata() -> int:
    rows = base._rows(
        """
        SELECT id, library_category, document_type, entity_type, entity_id, job_id,
               file_name, document_key, version_number, is_current
        FROM document_library
        ORDER BY id
        """
    )
    touched_keys: set[str] = set()
    for row in rows:
        key = str(row.get("document_key") or "").strip()
        if not key:
            key = document_family_key(
                category=row.get("library_category"),
                document_type=row.get("document_type"),
                entity_type=row.get("entity_type"),
                entity_id=row.get("entity_id"),
                job_id=row.get("job_id"),
                file_name=row.get("file_name"),
            )
            base._execute(
                "UPDATE document_library SET document_key=? WHERE id=?",
                (key, int(row["id"])),
            )
        if row.get("version_number") in (None, "") or row.get("is_current") in (None, ""):
            touched_keys.add(key)

    updated = 0
    for key in touched_keys:
        family = base._rows(
            "SELECT id FROM document_library WHERE document_key=? ORDER BY id",
            (key,),
        )
        previous_id: int | None = None
        for index, row in enumerate(family, start=1):
            document_id = int(row["id"])
            is_current = 1 if index == len(family) else 0
            lifecycle = "Current" if is_current else "Superseded"
            base._execute(
                """
                UPDATE document_library
                SET version_number=?, supersedes_document_id=?, is_current=?,
                    lifecycle_status=?,
                    superseded_at=CASE WHEN ?=0 AND COALESCE(superseded_at,'')='' THEN ? ELSE superseded_at END,
                    superseded_by=CASE WHEN ?=0 AND COALESCE(superseded_by,'')='' THEN 'Historical backfill' ELSE superseded_by END
                WHERE id=?
                """,
                (
                    index,
                    previous_id,
                    is_current,
                    lifecycle,
                    is_current,
                    _now(),
                    is_current,
                    document_id,
                ),
            )
            previous_id = document_id
            updated += 1
    return updated


def _ensure_version_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    _ensure_version_columns()
    _backfill_legacy_job_documents()
    _backfill_version_metadata()
    _schema_ready = True


def _wrap_ensure_schema(original):
    def wrapped() -> None:
        original()
        _ensure_version_schema()

    setattr(wrapped, PATCH_MARKER, True)
    wrapped._pb_original_ensure_schema = original
    return wrapped


def _latest_family_row(key: str) -> dict[str, Any] | None:
    rows = base._rows(
        """
        SELECT id, version_number, file_sha256, revision
        FROM document_library
        WHERE document_key=? AND COALESCE(is_current,0)=1
        ORDER BY COALESCE(version_number,0) DESC, id DESC
        LIMIT 1
        """,
        (key,),
    )
    return rows[0] if rows else None


def _wrap_store_one(original):
    def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        # Support the current keyword-heavy call and retain positional compatibility.
        bound = {
            "upload": kwargs.get("upload", args[0] if len(args) > 0 else None),
            "category": kwargs.get("category", args[1] if len(args) > 1 else ""),
            "document_type": kwargs.get("document_type", args[2] if len(args) > 2 else ""),
            "entity_type": kwargs.get("entity_type", args[3] if len(args) > 3 else ""),
            "entity_id": kwargs.get("entity_id", args[4] if len(args) > 4 else None),
            "job_id": kwargs.get("job_id", args[6] if len(args) > 6 else None),
        }
        upload_name = str(getattr(bound["upload"], "name", "document") or "document")
        key = document_family_key(
            category=bound["category"],
            document_type=bound["document_type"],
            entity_type=bound["entity_type"],
            entity_id=bound["entity_id"],
            job_id=bound["job_id"],
            file_name=upload_name,
        )
        previous = _latest_family_row(key)
        result = original(*args, **kwargs)
        inserted = base._rows(
            "SELECT id, file_sha256, revision FROM document_library WHERE file_path=? ORDER BY id DESC LIMIT 1",
            (str(result.get("file_path") or ""),),
        )
        if not inserted:
            return result

        document_id = int(inserted[0]["id"])
        previous_id = int(previous["id"]) if previous else None
        previous_version = int(previous.get("version_number") or 0) if previous else 0
        actor = base._current_username()
        if previous_id:
            base._execute(
                """
                UPDATE document_library
                SET is_current=0, lifecycle_status='Superseded', superseded_at=?, superseded_by=?
                WHERE document_key=? AND id<>? AND COALESCE(is_current,0)=1
                """,
                (_now(), actor, key, document_id),
            )
            _log_event(
                previous_id,
                "superseded",
                actor,
                f"Superseded by document #{document_id}.",
                related_document_id=document_id,
            )

        version_number = max(1, previous_version + 1)
        base._execute(
            """
            UPDATE document_library
            SET document_key=?, version_number=?, supersedes_document_id=?,
                is_current=1, lifecycle_status='Current', superseded_at=NULL, superseded_by=NULL
            WHERE id=?
            """,
            (key, version_number, previous_id, document_id),
        )
        _log_event(
            document_id,
            "uploaded_version",
            actor,
            f"Recorded as version {version_number}." + (
                f" Supersedes document #{previous_id}." if previous_id else ""
            ),
            related_document_id=previous_id,
        )
        result["document_library_id"] = document_id
        result["version_number"] = version_number
        result["supersedes_document_id"] = previous_id
        return result

    setattr(wrapped, PATCH_MARKER, True)
    wrapped._pb_original_store_one = original
    return wrapped


def _recent_documents_v2(limit: int = 500) -> list[dict[str, Any]]:
    limit = max(10, min(int(limit), 1000))
    return base._rows(
        f"""
        SELECT id, library_category, document_type, entity_type, entity_id,
               entity_label, job_id, job_document_id, file_name, file_path,
               revision, document_date, notes, uploaded_by, source_app, created_at,
               document_key, version_number, supersedes_document_id, is_current,
               superseded_at, superseded_by, lifecycle_status
        FROM document_library
        ORDER BY id DESC
        LIMIT {limit}
        """
    )


def _restore_as_current(document_id: int, document_key: str) -> None:
    actor = base._current_username()
    base._execute(
        """
        UPDATE document_library
        SET is_current=0, lifecycle_status='Superseded',
            superseded_at=CASE WHEN id<>? THEN ? ELSE superseded_at END,
            superseded_by=CASE WHEN id<>? THEN ? ELSE superseded_by END
        WHERE document_key=?
        """,
        (document_id, _now(), document_id, actor, document_key),
    )
    base._execute(
        """
        UPDATE document_library
        SET is_current=1, lifecycle_status='Current', superseded_at=NULL, superseded_by=NULL
        WHERE id=?
        """,
        (document_id,),
    )
    _log_event(document_id, "restored_current", actor, "An older stored version was restored as the current library version.")


def _render_register_v2(st: Any) -> None:
    st.subheader("Document register")
    st.caption(
        "Uploading the same document family again creates a new immutable version. Older versions stay in history and can be restored as current without deleting files."
    )
    rows = _recent_documents_v2()
    if not rows:
        st.info("No documents have been added through the Document Centre yet.")
        return

    categories = ["All"] + list(base.CATEGORY_TYPES)
    c1, c2, c3 = st.columns([1, 2, 1])
    selected_category = c1.selectbox("Filter category", categories, key="document_register_category")
    search_text = c2.text_input(
        "Search documents",
        placeholder="file name, job, employee, builder, document type, notes...",
        key="document_register_search",
    ).strip().lower()
    show_superseded = c3.checkbox("Show superseded", value=False, key="document_register_show_superseded")

    filtered: list[dict[str, Any]] = []
    for row in rows:
        if selected_category != "All" and str(row.get("library_category")) != selected_category:
            continue
        if not show_superseded and not bool(int(row.get("is_current") or 0)):
            continue
        if search_text:
            haystack = " ".join(
                str(row.get(key) or "")
                for key in (
                    "file_name", "library_category", "document_type", "entity_label",
                    "revision", "notes", "uploaded_by", "document_date", "created_at",
                    "lifecycle_status", "version_number",
                )
            ).lower()
            if search_text not in haystack:
                continue
        filtered.append(row)

    if not filtered:
        st.info("No documents match those filters.")
        return

    display_rows = [
        {
            "ID": row.get("id"),
            "Category": row.get("library_category"),
            "Type": row.get("document_type"),
            "Linked to": row.get("entity_label"),
            "File": row.get("file_name"),
            "Version": row.get("version_number"),
            "Revision": row.get("revision"),
            "Status": row.get("lifecycle_status") or ("Current" if row.get("is_current") else "Superseded"),
            "Document date": row.get("document_date"),
            "Uploaded by": row.get("uploaded_by"),
            "Uploaded": row.get("created_at"),
        }
        for row in filtered
    ]
    st.dataframe(display_rows, width="stretch", hide_index=True)

    options = {
        f"#{row.get('id')} · v{row.get('version_number') or 1} · {row.get('file_name')} · {row.get('document_type')}": row
        for row in filtered
    }
    selected_label = st.selectbox("Download / inspect", list(options), key="document_register_download_choice")
    selected = options[selected_label]
    selected_path = Path(str(selected.get("file_path") or ""))
    if selected_path.exists() and selected_path.is_file():
        st.download_button(
            "Download selected document",
            data=selected_path.read_bytes(),
            file_name=str(selected.get("file_name") or selected_path.name),
            mime="application/octet-stream",
            key=f"document_register_download_{selected.get('id')}",
        )
    else:
        st.warning("The database record exists, but the stored file is not available on this server.")

    document_key = str(selected.get("document_key") or "")
    if document_key:
        st.markdown("#### Version history")
        history = base._rows(
            """
            SELECT id, version_number, revision, lifecycle_status, is_current,
                   file_name, document_date, uploaded_by, created_at,
                   superseded_at, superseded_by
            FROM document_library
            WHERE document_key=?
            ORDER BY COALESCE(version_number,0) DESC, id DESC
            """,
            (document_key,),
        )
        st.dataframe(
            [
                {
                    "ID": row.get("id"),
                    "Version": row.get("version_number"),
                    "Revision": row.get("revision"),
                    "Status": row.get("lifecycle_status"),
                    "File": row.get("file_name"),
                    "Document date": row.get("document_date"),
                    "Uploaded by": row.get("uploaded_by"),
                    "Uploaded": row.get("created_at"),
                    "Superseded": row.get("superseded_at"),
                    "Superseded by": row.get("superseded_by"),
                }
                for row in history
            ],
            width="stretch",
            hide_index=True,
        )

        if not bool(int(selected.get("is_current") or 0)):
            if st.button(
                "Restore selected stored version as current",
                key=f"document_restore_current_{selected.get('id')}",
            ):
                _restore_as_current(int(selected["id"]), document_key)
                st.success("Selected version is now current. No files or audit records were deleted.")
                rerun = base._app_attr("pb_rerun") or base._app_attr("refresh") or getattr(st, "rerun", None)
                if callable(rerun):
                    rerun()

        events = base._rows(
            """
            SELECT e.created_at, e.event_type, e.actor, e.detail, e.related_document_id
            FROM document_library_events e
            JOIN document_library d ON d.id=e.document_id
            WHERE d.document_key=?
            ORDER BY e.id DESC
            LIMIT 100
            """,
            (document_key,),
        )
        if events:
            st.markdown("#### Audit history")
            st.dataframe(events, width="stretch", hide_index=True)


def install_document_centre_versioning_guard() -> bool:
    if getattr(base, PATCH_MARKER, False):
        return False
    original_ensure = getattr(base, "_ensure_schema", None)
    original_store = getattr(base, "_store_one", None)
    if not callable(original_ensure) or not callable(original_store):
        return False
    base._ensure_schema = _wrap_ensure_schema(original_ensure)
    base._store_one = _wrap_store_one(original_store)
    base._recent_documents = _recent_documents_v2
    base._render_register = _render_register_v2
    setattr(base, PATCH_MARKER, True)
    return True
