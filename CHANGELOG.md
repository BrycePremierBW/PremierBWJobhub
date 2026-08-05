# JobHub secured release

## 2026-08-05 - Smart intake accuracy test pack

- Added an accuracy test pack (`tests/test_intake_accuracy.py`) that locks the
  realistic plan, scope-of-works and colour-schedule fixtures to exact expected
  quantities, hours and litres (23 new tests).
- Fixed scope parsing so a following item (e.g. a ceiling line) can no longer
  re-classify the substrate of the item above it; the line's own wording is used
  first and the surrounding window is only a fallback.
- "8 interior doors" style item counts are now recognised when an adjective
  (interior / internal / timber / etc.) sits between the quantity and the unit.
- Material estimates for repeated substrates now accumulate instead of silently
  dropping the later item, so a 3-coat feature wall's litres are counted.
- Plan reader address detection no longer mistakes drawing text such as
  "1 FLOOR PLAN SCALE 1:100" for a street address (street suffix must end a word).

## 2026-08-05 - Missed timesheets and mobile tuning

- Employees can catch up on missed timesheets: the roster (`staff_schedule`) is
  compared against `timesheet_entries` so any scheduled shift without a timesheet
  in the last 21 days appears with the rostered job, stage, start and finish
  pre-filled. Approved leave and days that already have a timesheet are excluded.
- Management can generate missed timesheets on an employee's behalf from the
  Review Timesheets tab, using the same catch-up list.
- Mobile app navigation now has icons for Operations Hub, Upload PO and Field
  Mode in both the phone header and the desktop sidebar.
- Phone notifications can now be disabled from the same button that enables
  them (tap while enabled to opt this device out).
- PWA installability tuning: dedicated 192×192 and 512×512 icons are generated
  from the square logo, the manifest lists them, adds display_override, language
  and app shortcuts, and iOS uses the 192px apple-touch-icon.

## 2026-08-05 - Smart document intake

- Import Take-off Job Pack now has a Smart Document Intake mode that reads
  uploaded plans, scope-of-works and colour schedules instead of a Job Pack ZIP.
- Plans are read from PDF text with labelled room dimensions converted into
  computed wall and ceiling areas; scopes are scanned for m², lineal-metre,
  each and litre take-off lines plus colours; colour schedules are read from
  CSV or Excel files.
- The intake can create a brand-new job (a standard Job Pack is synthesised
  internally so the normal importer runs unchanged), attach a new estimate to an
  existing job, or merge the take-off into the job's current estimate with
  matching lines keeping the larger quantity / hours and material quantities
  accumulated.
- Source documents are written into the job folder and attached to the job. A
  stated job number protects against attaching a take-off to the wrong job.
- Materials and colours are matched to existing products where possible and
  custom rows are created otherwise.

## 2026-08-05 - Estimate production pricing

- Estimate Summary now shows a live gross-profit margin banner built from the
  production pricing model (Strong / Acceptable / Low thresholds) with editable
  labour cost per hour, material markup per cent, material allowance and access /
  subcontractor / sundries allowances saved on the Summary.
- Line Items supports inline editing of line rates and totals plus a floor-area
  quick add that builds wall / ceiling take-off lines from room dimensions.
- A rate register records the planning rate and any production-line rate
  overrides per estimate for managers and administrators.
- Estimate pricing no longer locks on stage changes and staff never see dollar
  values in the rate register.

## 2026-08-05 - PlanReader elevation progress tracker

- Rendered elevation drawing pages are now tagged so they can be used as a
  visual progress tracker. A new Progress Tracking page marks painted/unpainted
  zones per elevation with colour-coded overlays (grey/amber/orange/green by
  completion) and builds a whole-house elevation progress board.
- Zone geometry is stored as exact percentage coords and converted to pixels
  deterministically at render time; overall and per-zone progress persist with
  the job.
- A progress board is built automatically when plans are imported, and elevation
  progress is included in the Excel export pack.

## 2026-08-05 - PlanReader take-off precision

- PlanReader now extracts labelled room dimensions from plan text (e.g.
  "Lounge 5400 x 3200") and converts them into computed internal wall and
  ceiling areas using configurable ceiling height and door/window opening
  allowances, replacing the manual "to be measured" buckets for those rooms.
- Rooms are editable in the Take-off Draft page and rows can be rebuilt after
  changing room sizes, ceiling height or opening allowance.
- Lineal quantities for skirting/architrave/trim are now turned into take-off
  rows instead of being dropped.
- Paint coverage (m²/L) is configurable when recalculating litres.
- Added room detection to the overview metrics and the Excel export pack.

## 2026-08-05 - v1.0.0

- Fixed the login flow so a successful submit renders the app in the same run
  instead of calling a forced rerun from inside the form, removing the AppTest
  KeyError caused by pruned login-form widgets.
- Stopped placing the auth token in URL query parameters; tokens now persist
  only in session state and the app_users table.
- Fixed the visual scheduler grid KeyError when schedule assignments reference
  employees deactivated after being booked, and guarded the leave page when no
  active staff exist.
- Made the plan reader import PyMuPDF lazily with a clear installation error and
  added PyMuPDF to the Python dependencies.
- Removed PII CSV exports, the nested repository zip, the dead insecure
  jobhub/security.py module (unsalted hashes and default accounts), and the
  duplicate CI workflow; added the data files to .gitignore.

## 2026-07-28 - Performance and Deployment Cleanup

- Stopped estimator-linked progress from rewriting unchanged external rows.
- Batched changed progress rows and linked scheduler date moves into single
  database transactions.
- Cached idempotent progress and scheduler schema setup per server process.
- Added linked-sync indexes and enabled SQLite WAL with normal synchronous mode.
- Disabled Streamlit's production file watcher and simplified Render's build
  command so dependency caching can be reused.
- Removed the unused duplicate app template, one-off patch installers, obsolete
  audit reports, old packaging notes and the temporary GitHub connection test.
- Added focused regression tests for no-op and batched progress syncing.

## 2026-07-28 - Linked Progress and Smart Scheduling

- Added an estimator-linked Job Progress Tracker with a fixed dwelling count for each job.
- Added internal progress stages: Sealer, Spray Walls, Spray Ceilings, Spray Gloss, PC and Touch-ups.
- Added external substrate progress using estimator m2, with Preparation, Primer / Sealer, First Coat, Final Coat and Touch-ups.
- Added weighted completion, completed/remaining m2, earned value and remaining value.
- Added automatic estimator quantity refresh across linked progress records.
- Added explainable crew suggestions using job dates, estimator hours, current progress, staff roles, capacity, leave and existing allocations.
- Suggestions require approval before staff are added to the schedule.
- Linked schedule assignments now move automatically when the master job start date changes; fixed/manual dates remain unchanged.

## 2026-07-24

- Replaced unsalted password storage with PBKDF2-SHA256 and automatic legacy
  hash upgrades.
- Removed fixed default accounts and added secure one-time administrator
  bootstrap, password policy, lockouts, and forced temporary-password changes.
- Added application and login audit logs.
- Added versioned, restart-safe database migration.
- Made timesheet approval and wage posting transactional and idempotent.
- Added safe reconciliation for legacy timesheet-generated wages.
- Replaced destructive SQLite upserts on parent records with conflict updates.
- Completed job-linked deletion coverage and recoverable job-file archiving.
- Added explicit Target Gross Margin versus Markup estimating.
- Corrected committed and actual material-cost reporting.
- Added stable variation and claim numbering.
- Removed embedded customer, employee, payroll, and job starter data.
- Added protected administrator CSV imports.
- Restricted employee access to assigned jobs.
- Added upload size, image validation, PDF validation, storage-path, URL-fetch,
  redirect, and external-AI safeguards.
- Disabled production self-editing by default and made rollback cover all
  touched files.
- Added row-version conflict protection for job editing.
- Consolidated duplicate implementations and added deployment files, tests,
  templates, and operating documentation.
- Installed the approved logo and supplied Master Checklist and Paint &
  Materials Order templates.
- Added a deployment-safe generic Day Labour Sheet and JobHub generation flow
  populated from job details and non-rejected timesheets.
- Added canonical-field, widget-value, and appearance-stream verification to
  generated PDF forms.
- Made timesheet totals recalculate live from start time, finish time, and break
  minutes, including overnight shifts.
- Added review summaries and required acceptance checkboxes to timesheet entry,
  individual approval, job-filter, and bulk-approval selections.
