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

1. Register the staging redirect URI in the Xero developer app.
2. Connect a Xero demo organisation.
3. Map the Premier Brushworks chart-of-accounts codes and tax types.
4. Run contact, draft invoice, draft bill and payment-status smoke tests.
5. Keep production Xero writes disabled until the staging evidence is approved.
