# JobHub V2 implementation

Branch: `operations-v2`

## Milestone 1 — test and staging foundation

- Isolated runtime configuration for development, staging, production and tests.
- Staging Render blueprint with a separate, manually supplied database URL.
- GitHub Actions compile and test workflow.
- Pure, tested idempotency helpers for queued Field Mode events.
- Email and offline-sync feature flags default to off.

## Safety rules

- The staging service must never use the production `DATABASE_URL`.
- Offline events must include an idempotency key before server processing.
- A duplicate idempotency key must return the original result without repeating a write.
- Email delivery remains disabled until a provider and sender address are configured.

## Next milestone

1. Add the server-side sync event table and transaction-safe event processor.
2. Connect clock, timesheet, photo and field-form handlers.
3. Add the installable Field Mode PWA with IndexedDB queueing.
4. Add provider-backed critical email delivery and retry logging.
