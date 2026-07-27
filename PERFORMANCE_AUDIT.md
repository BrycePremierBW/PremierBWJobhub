# JobHub full performance and cleanup audit

Date: 2026-07-28

## Scope checked

- 60 tracked Python files and more than 50,000 lines of Python
- production entry point and every supporting Python module
- Streamlit startup, rerun, database, scheduler and progress-sync paths
- PostgreSQL pooling and SQLite settings
- schema setup and query indexes
- all tracked deployment, documentation, template and test files
- secrets patterns, duplicate top-level runtime symbols and Python syntax

## Runtime improvements

- Unchanged estimator-linked external progress rows now cause zero writes.
- Changed/new linked progress rows are written in batches rather than one
  connection and transaction per row.
- Linked scheduler date moves are also written in one batch.
- Progress and scheduler schema checks run once per server process.
- Added indexes for linked estimates, external estimate lines and linked
  scheduler jobs.
- SQLite uses WAL mode and normal synchronous mode to reduce reader/writer
  blocking.
- Production file watching and run-on-save are disabled.

## Deployment cleanup

Removed:

- unused duplicate `templates/pb_jobhub_app.py`
- obsolete one-off source patch and tidy installers
- temporary GitHub connection test
- superseded audit/test reports and package-specific README fragments

Retained:

- all database restore and migration SQL
- all seed/import CSV and Excel sources
- operational PDF templates and brand assets
- PlanReader and staged modular JobHub code
- all live database, job document, photo, export and backup paths

## Validation

- all remaining Python files compile
- 47 automated tests pass
- no duplicate top-level runtime definitions were found
- no embedded API key was found
- the focused performance tests confirm unchanged rows do not write and
  changed/new rows batch correctly

## Safety boundary

The repository cleanup does not delete persistent Render data. Database rows,
uploaded documents, photos and backup ZIP files must only be removed from an
authenticated administrative inventory with explicit targets.
