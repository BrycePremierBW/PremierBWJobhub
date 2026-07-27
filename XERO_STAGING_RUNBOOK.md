# Xero staging connection and acceptance runbook

Use this runbook only against:

- JobHub staging: `https://premier-brushworks-jobhub-staging.onrender.com`
- Render service: `premier-brushworks-jobhub-staging`
- Xero demo organisation

Do not connect the production JobHub service during this run.

## 1. Secure the staging administrator

1. Complete JobHub's required permanent-password screen.
2. Confirm the new password works in a fresh private session.
3. Remove `JOBHUB_BOOTSTRAP_ADMIN_PASSWORD` from the Render staging service.
4. Redeploy and confirm the administrator still signs in.

## 2. Finalise the Xero developer app

1. Sign in to the Xero developer portal.
2. Add this exact redirect URI:
   `https://premier-brushworks-jobhub-staging.onrender.com/xero/callback`
3. Keep the production redirect URI registered separately:
   `https://premierbwjobhub.onrender.com/xero/callback`
4. Rotate the current Xero client secret.
5. Replace `XERO_CLIENT_SECRET` in Render staging with the new secret.
6. Confirm these staging variables are present without revealing their values:
   - `XERO_CLIENT_ID`
   - `XERO_CLIENT_SECRET`
   - `XERO_REDIRECT_URI`
   - `XERO_TOKEN_ENCRYPTION_KEY`
   - `XERO_OAUTH_STATE_SECRET`
   - `XERO_ENABLED=true`

## 3. Connect a demo organisation

1. Sign in to JobHub staging as an administrator.
2. Open **Management → Xero Integration**.
3. Select **Connect Xero organisation**.
4. Authorise only a Xero demo organisation.
5. Confirm JobHub shows the demo tenant name and a successful connection time.
6. Confirm no access or refresh token is visible in the UI or application logs.

## 4. Acceptance tests

Run one controlled example of each operation:

1. Builder/client contact → Xero contact.
2. Supplier → Xero contact.
3. Approved progress claim → Xero `DRAFT` sales invoice.
4. Approved supplier invoice → Xero `DRAFT` bill.
5. Pull invoice payment status back into JobHub.
6. Repeat each push with the same payload and confirm no duplicate Xero record.
7. Force one token refresh and confirm the rotated refresh token remains usable.

## 5. Evidence to record

- JobHub audit-event IDs.
- JobHub Xero sync-event IDs and statuses.
- Xero contact and invoice IDs.
- Screenshots showing `DRAFT` status only.
- Duplicate retry result.
- Payment-status result.
- Any validation or provider error, without secrets.

## 6. Pass criteria

- OAuth state is accepted once and rejected if replayed.
- Tokens remain encrypted at rest.
- Contacts are idempotent.
- Claims and bills arrive as `DRAFT`.
- Duplicate retries do not create duplicate Xero records.
- Payment status returns to the correct JobHub record.
- Production remains disconnected and unchanged.

## 7. Failure and rollback

If any pass criterion fails:

1. Set `XERO_ENABLED=false` in Render staging.
2. Do not retry a write until its sync-event status and Xero record are checked.
3. Revoke the staging Xero connection if token handling is in doubt.
4. Rotate the Xero client secret and JobHub encryption/state keys if exposure is suspected.
5. Keep all V2–V4 pull requests in draft.

## Staging expiry note

The free Render PostgreSQL staging database is currently scheduled to expire on
26 August 2026 unless upgraded. Export or upgrade it before that date if testing
continues.
