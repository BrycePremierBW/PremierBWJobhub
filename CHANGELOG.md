# JobHub secured release

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
