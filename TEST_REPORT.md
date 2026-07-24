# Verification report

Date: 2026-07-24

## Passed

- Python syntax compilation for all three application modules.
- Seventeen automated unit, source-structure, and asset tests covering salted
  password hashes, legacy password migration, fixed-default rejection, password
  strength, Target Gross Margin, Markup, SSRF/private-address blocking, stable
  numbering, timesheet calculations, PDF contracts, and release hygiene.
- Streamlit isolated startup against a new SQLite database.
- Automatic schema migration creation and required-column checks.
- SQLite foreign-key integrity check.
- Legacy timesheet/wage migration integration test:
  - one approved legacy timesheet linked to one wage;
  - one unapproved legacy auto-wage reversed;
  - one ambiguous pair left unchanged and reported for review.
- Duplicate top-level implementation cleanup.
- Structural inspection of the supplied Master Checklist and Paint & Materials
  Order PDFs: canonical fields, page widgets, and appearance streams agree.
- Visual rendering review of all supplied PDF pages.
- Generic Day Labour Sheet: 77 canonical fields, 77 matching widgets, complete
  appearance streams, and no fixed project or address data.
- Timesheet calculation tests for same-day shifts, overnight shifts, break
  deductions, excessive breaks, and invalid time values.
- Existing material-order workflow integration test: submitted, approved,
  converted to a single material-cost entry, attached to the job, and rendered
  to PDF without duplicate posting.
- Full Streamlit smoke test: secure administrator bootstrap/login and all 22
  JobHub routes rendered without an application exception.
- Existing Render production topology verified before cutover:
  - `PremierBWJobhub` web service on the Starter plan in Singapore;
  - PostgreSQL 18 `Basic-256mb` database in Singapore;
  - seven-day point-in-time database recovery available;
  - 1 GB persistent disk mounted at `/var/data`;
  - current live health endpoint returned HTTP 200 and `ok`.

## Post-deployment acceptance

- Confirm the new Render deploy reaches `Live` and the health endpoint returns
  HTTP 200.
- Sign in with an existing administrator account and confirm the dashboard,
  timesheets, review/accept controls, and supplied PDFs.
- Complete role-based user acceptance testing with representative staff.
- Confirm the organisation's external-AI privacy decision before enabling it.
