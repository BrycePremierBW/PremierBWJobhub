# BrightHR Blip → JobHub attendance bridge

JobHub can stage BrightHR Blip attendance and publish reviewed, mapped sessions into the existing JobHub Timesheets workflow.

## Safety model

- BrightHR Blip remains the clock-in / clock-out source.
- JobHub never guesses which employee or job a Blip record belongs to.
- BrightHR employees must be explicitly mapped to JobHub employees.
- BrightHR Blip locations must be explicitly mapped to JobHub jobs.
- Open sessions cannot be published.
- Unmapped sessions cannot be published.
- Published sessions retain the originating Blip event/hash and are deduplicated before another Timesheet can be created.
- BrightHR credentials are read from environment variables only. They are not stored in JobHub tables or displayed in the UI.

## Render environment variables

Configure these on the JobHub Render web service. Do not put credentials in GitHub or paste them into chat.

- `BRIGHTHR_CLIENT_ID` — BrightHR Customer API client ID.
- `BRIGHTHR_CLIENT_SECRET` — BrightHR Customer API client secret.
- `BRIGHTHR_TOKEN_URL` — the OAuth2 token endpoint supplied for the BrightHR Customer API.
- `BRIGHTHR_BLIP_ATTENDANCE_URL` — the BrightHR Customer API endpoint that returns the required Blip attendance records for this tenant.
- `BRIGHTHR_TOKEN_AUTH_MODE` — optional; `body` (default) or `basic`, depending on the BrightHR OAuth client configuration.
- `BRIGHTHR_SCOPE` — optional OAuth scope when required by the BrightHR client.

The token and attendance URLs are intentionally configuration, rather than hard-coded guesses. BrightHR API tenants/versions must use the endpoint details supplied by BrightHR.

## JobHub workflow

1. Manager/admin opens **Site & Team → Blip Attendance**.
2. **Sync Blip now** fetches attendance into JobHub staging tables.
3. Map each discovered BrightHR employee to the correct JobHub employee.
4. Map each discovered Blip location/site to the correct JobHub job.
5. Completed rows with both mappings become **Ready**.
6. Review Ready rows and publish selected rows to JobHub Timesheets.
7. Published rows are marked **Published** with the JobHub timesheet ID.

## Staging tables

The integration creates its own non-destructive tables lazily when the manager/admin page is first used:

- `blip_employee_map`
- `blip_location_job_map`
- `blip_attendance_entries`
- `blip_sync_runs`

No Blip schema work runs during normal JobHub startup, so a BrightHR outage cannot stop JobHub from booting.

## Initial sync behaviour

The first version is intentionally manual-sync and review-first. It does not run a background poller and it does not automatically approve JobHub timesheets. Once the live BrightHR payload and endpoint have been verified, scheduled syncing can be added without changing the mapping/publish safety model.
