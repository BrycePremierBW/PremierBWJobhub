# JobHub production hardening runbook

This runbook covers the production actions that cannot be completed by a normal GitHub code change because they require Premier Brushworks' Render or GitHub account authority.

## 1. Production Python

JobHub is pinned to Python 3.13.5 in both `.python-version`, `render.yaml`, and GitHub Actions.

In Render > PremierBWJobhub > Environment, confirm `PYTHON_VERSION` is exactly `3.13.5`. Render environment variables take precedence over `.python-version`.

After the next deploy, Management > System Health > Runtime should report Python `3.13.5`.

## 2. Persistent disk

The repository Blueprint now requests a 5 GB `/var/data` persistent disk. Render permits increasing an existing disk but does not permit decreasing it.

If the service is not managed by this Blueprint, open Render > PremierBWJobhub > Disks and increase the existing `/var/data` disk to 5 GB. Do not create a second disk and do not change the mount path.

Confirm Management > System Health shows the increased capacity after Render applies the resize.

## 3. Unresolved application error

Management > System Health now includes an `Unresolved errors` tab. Review the retained error record, add a resolution note, and mark it resolved. This updates `resolved_at`, `resolved_by`, and `resolution_notes`; it does not delete the audit record.

Do not mark an error resolved if the same fault is still occurring.

## 4. PostgreSQL recovery verification

A JobHub ZIP under `/var/data/backups` is an application data archive. It is not proof that Render PostgreSQL can be restored.

For a real recovery drill:

1. In Render, open `premier-brushworks-jobhub-db` > Recovery.
2. Create either a point-in-time recovery instance or an isolated database restored from a logical export. Never restore over the live database for a test.
3. Obtain the source and isolated restore connection strings without posting them in chat, tickets, logs, or source control.
4. From a trusted machine with access to both databases, run:

   ```bash
   SOURCE_DATABASE_URL='...' RESTORE_DATABASE_URL='...' python scripts/verify_postgres_restore.py
   ```

5. The verifier is read-only and does not print the connection strings. It compares selected table presence, row counts and deterministic non-secret row digests.
6. Only after it prints `RESTORE VERIFIED`, set the Render web-service environment variable `JOBHUB_POSTGRES_RESTORE_VERIFIED_AT` to the verification timestamp, for example `2026-08-10T03:00:00+10:00`.
7. Delete the temporary recovery database after the verification is documented.

System Health reports a warning until a real restore drill has been recorded, then warns again after 90 days and becomes critical after 180 days.

## 5. PostgreSQL credential rotation

Rotate the Render PostgreSQL credential only after JobHub is stable and a recovery drill has succeeded.

1. Rotate/reset the database password in Render.
2. Copy the new Internal Database URL directly from Render.
3. Update the `DATABASE_URL` secret on `PremierBWJobhub`.
4. Update PlanReader's `JOBHUB_DATABASE_URL` to the corresponding new connection string.
5. Redeploy both services.
6. Confirm JobHub System Health reports PostgreSQL healthy and PlanReader can access the linked JobHub data.
7. Do not reuse, paste, or retain the old credential.

A `connection refused` error is an availability/connectivity event; a `password authentication failed` error means the credential update is incomplete.

## 6. GitHub main protection

Protect `main` in GitHub repository settings. Recommended minimum:

- Require a pull request before merging.
- Require the `JobHub tests / test` status check to pass.
- Require branches to be up to date before merging.
- Require conversation resolution before merging.
- Block force pushes.
- Block branch deletion.
- Apply the rule to administrators if practical for the current workflow.

The repository was observed as public during the August 2026 health review. Confirm that public visibility is intentional. If it is not intentional, change repository visibility to Private after checking any deployment integrations that depend on repository access.

## 7. Deployment verification after each hardening release

After a production deploy:

1. Open Management > System Health.
2. Confirm `Database connection = Healthy` and backend `PostgreSQL`.
3. Confirm the Render commit matches the intended GitHub merge commit.
4. Confirm Python is `3.13.5`.
5. Confirm `/var/data` is writable and has comfortable free capacity.
6. Confirm there are no unexplained unresolved errors.
7. Confirm the latest JobHub archive is recent.
8. Confirm the PostgreSQL restore-drill status is Healthy once the recovery drill has been completed.

## 8. Document Centre next phase

The next product hardening phase is Document Centre revision/version history and controlled supersedence while preserving the existing `job_documents` and PlanReader bridge contracts. Historical `job_documents` should be backfilled into `document_library` without deleting or moving the original records/files.
