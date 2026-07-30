# JobHub V3 implementation

Status: rebuilt as a clean V3 delta on the reconciled V2 foundation.

## Milestone 1 — Xero-safe accounting foundation

- Standard Xero OAuth 2.0 authorisation-code client.
- Required offline access plus the granular contacts, invoices and payments
  scopes documented by Xero.
- Refresh-token rotation returned to the caller for immediate secure storage.
- Signed, expiring OAuth state protection.
- Encrypted access and refresh-token persistence.
- Xero tenant selection support.
- Draft sales-invoice and purchase-bill mappings.
- Restart-safe connection, sync-event and commercial-event tables.
- Feature flag and secret placeholders; no credentials committed.

## Safety rules

- Xero is disabled until `XERO_ENABLED=true`.
- Only JobHub administrators can open the Xero integration page.
- Never store or log Xero credentials in source code.
- Store tokens encrypted and replace the stored refresh token after every refresh.
- Create sales invoices and supplier bills as `DRAFT` until approved in JobHub.
- Send Xero's `Idempotency-Key` header as well as using JobHub's sync-event
  register to prevent duplicates during retries.
- Test against a Xero demo organisation before enabling production.

## Milestone 2 — accounting sync and commercial workflows

- Encrypted token persistence and administrator connection screen.
- Refresh-token rotation before live API requests.
- Idempotent builder/client and supplier contact synchronisation.
- Approved JobHub claims mapped to draft Xero sales invoices.
- Approved supplier invoices mapped to draft Xero bills.
- Normalised payment-status retrieval.
- Restart-safe contact, invoice, claim, bill, retention and EOT tables.
- Progress-claim and retention-cap calculations.
- Explicit progress-claim, supplier-bill and extension-of-time state transitions.

## Remaining live validation

1. Register the exact HTTPS staging application root as the redirect URI in
   the Xero developer app.
2. Connect a Xero demo organisation.
3. Map the Premier Brushworks chart-of-accounts codes and tax types.
4. Run contact, draft invoice, draft bill and payment-status smoke tests.
5. Keep production Xero writes disabled until the staging evidence is approved.
