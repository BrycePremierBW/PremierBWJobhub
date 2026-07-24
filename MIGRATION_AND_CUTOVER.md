# Migration and cutover

## Before deployment

1. Schedule a short maintenance window.
2. Back up the PostgreSQL database or copy the SQLite database while the old
   app is stopped.
3. Back up the entire `DATA_DIR`, especially `job_files`, `photos`, and
   `exports`.
4. Keep the previous application package unchanged for rollback.
5. Add `PB Variation Form fillable.pdf` if variation-form generation is
   required. The logo, Day Labour Sheet, Master Checklist, and Paint &
   Materials Order templates are already installed.
6. Install from `requirements.txt`.
7. Keep `JOBHUB_ENABLE_SELF_EDIT=false`.
8. Keep external AI disabled unless a documented privacy approval exists.

## First start

The app automatically runs the migration named
`20260724_security_integrity_v1`. It adds authentication controls, audit logs,
employee job access, timesheet-to-wage links, row versions, storage metadata,
and supporting indexes.

Legacy wages that can be matched unambiguously to a timesheet are reconciled:

- approved/paid timesheets keep one linked wage row;
- unapproved legacy auto-created wage rows are reversed;
- ambiguous matches are left untouched and counted in the migration audit.

Review the `legacy_timesheet_wages_reconciled` entry under
User Access > Security and Change Audit after first start.

## Acceptance checks

- Sign in as an administrator and change any temporary password.
- Confirm fixed legacy default passwords no longer work.
- Create a test employee and assign one job.
- Confirm the employee cannot open any unassigned job.
- Submit a test timesheet; confirm no wage exists until approval.
- Approve it twice; confirm only one linked wage exists.
- Reject it; confirm the linked wage is removed.
- Verify estimate Target Gross Margin and Markup examples.
- Upload a valid photo and reject an oversized/invalid upload.
- Generate and inspect the Day Labour Sheet, Master Checklist, and Paint &
  Materials Order form. Also test the Variation Form if its template is added.
- Review recent audit and login events.
- Take a fresh post-migration backup.

## Rollback

Stop the new app, restore both the database backup and the matching `DATA_DIR`
backup, then redeploy the previous application package. Do not run old code
against a database already migrated by this release unless the database is
restored too.
