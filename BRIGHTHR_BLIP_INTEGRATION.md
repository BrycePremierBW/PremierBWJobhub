# BrightHR Blip → JobHub attendance bridge

JobHub can stage BrightHR Blip attendance and publish reviewed, job-assigned sessions into the existing JobHub Timesheets workflow.

## Safety model

- BrightHR Blip remains the clock-in / clock-out source.
- JobHub never guesses which employee or job a Blip record belongs to.
- BrightHR employees must be explicitly mapped to JobHub employees.
- Blip clockings carry no site/location field, so each session's job is assigned by a manager during review — never auto-guessed from a location.
- Open sessions cannot be published.
- Sessions without an employee mapping or a job assignment cannot be published.
- Published sessions retain the originating Blip event/hash and are deduplicated before another Timesheet can be created.
- BrightHR credentials are read from environment variables only. They are not stored in JobHub tables or displayed in the UI.

## Render environment variables

Configure these on the JobHub Render web service. Do not put credentials in GitHub or paste them into chat.

- `BRIGHTHR_CLIENT_ID` — BrightHR Customer API client ID.
- `BRIGHTHR_CLIENT_SECRET` — BrightHR Customer API client secret.
- `BRIGHTHR_TOKEN_URL` — the OAuth2 token endpoint supplied for the BrightHR Customer API (`https://login.brighthr.com/connect/token`).
- `BRIGHTHR_EMPLOYEES_URL` — the BrightHR Customer API endpoint for enumerating employees (`https://api.bright.hr/employees/v1/query`).
- `BRIGHTHR_BLIP_ATTENDANCE_URL` — the BrightHR Customer API clockings query endpoint (`https://api.bright.hr/blip/v1/clockings/query`).
- `BRIGHTHR_TOKEN_AUTH_MODE` — optional; `body` (default) or `basic`, depending on the BrightHR OAuth client configuration.
- `BRIGHTHR_SCOPE` — optional OAuth scope when required by the BrightHR client.
- `BRIGHTHR_SYNC_FROM` / `BRIGHTHR_SYNC_TO` — optional date range applied to clocking queries. Date-only values like `2026-08-01` are accepted (normalised to `2026-08-01T00:00:00Z`). The range must be at most 31 days wide and no more than 90 days in the past. Without a range, BrightHR returns only currently active clockings.

The token, employee and clocking URLs are intentionally configuration, rather than hard-coded guesses. BrightHR API tenants/versions must use the endpoint details supplied by BrightHR.

## JobHub workflow

1. Manager/admin opens **Site & Team → Blip Attendance**.
2. **Sync Blip now** fetches clockings into JobHub staging tables (employees are enumerated first, then each employee's clockings are paged through).
3. Map each discovered BrightHR employee to the correct JobHub employee.
4. Review each session and assign it to the correct JobHub job.
5. Completed rows with an employee mapping, a job assignment, and a clock-out become **Ready**.
6. Review Ready rows and publish selected rows to JobHub Timesheets.
7. Published rows are marked **Published** with the JobHub timesheet ID.

## Staging tables

The integration creates its own non-destructive tables lazily when the manager/admin page is first used:

- `blip_employee_map`
- `blip_attendance_entries`
- `blip_sync_runs`

No Blip schema work runs during normal JobHub startup, so a BrightHR outage cannot stop JobHub from booting.

## Initial sync behaviour

The first version is intentionally manual-sync and review-first. It does not run a background poller and it does not automatically approve JobHub timesheets. Once the live BrightHR payload and endpoint have been verified, scheduled syncing can be added without changing the mapping/publish safety model.
