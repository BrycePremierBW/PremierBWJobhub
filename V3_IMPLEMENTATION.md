# JobHub V3 implementation

Branch: `operations-v3` (stacked on `operations-v2`)

## Milestone 1 — Xero-safe accounting foundation

- Standard Xero OAuth 2.0 authorisation-code client.
- Required offline access plus Xero's post-March-2026 granular connection,
  contacts, invoices and payments scopes.
- Refresh-token rotation returned to the caller for immediate secure storage.
- Signed, expiring OAuth state protection.
- Encrypted access and refresh-token persistence.
- Xero tenant selection support.
- Draft sales-invoice and purchase-bill mappings.
- Restart-safe connection, sync-event and commercial-event tables.
- Feature flag and secret placeholders; no credentials committed.

## Safety rules

- Xero is disabled until `XERO_ENABLED=true`.
- Never store or log Xero credentials in source code.
- Store tokens encrypted and replace the stored refresh token after every refresh.
- Create sales invoices and supplier bills as `DRAFT` until approved in JobHub.
- Use idempotency keys and the sync-event register to prevent duplicates.
- Test against a Xero demo organisation before enabling production.

## Next milestone

1. Add encrypted token persistence and the admin connection screen.
2. Connect approved builders/clients and suppliers to Xero contacts.
3. Push approved JobHub claims as draft sales invoices.
4. Push approved supplier invoices as draft bills.
5. Pull payment status back into JobHub.
6. Add retention, progress-claim and extension-of-time workflows.
