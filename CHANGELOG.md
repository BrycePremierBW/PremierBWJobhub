# JobHub secured release

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
