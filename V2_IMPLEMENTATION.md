# JobHub V2 implementation

Status: reconciled with `main`.

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

## Milestone 2 — offline and notification reliability

- Restart-safe offline sync-event and critical-email outbox tables.
- Transaction-safe event processor with payload-bound idempotency keys.
- Duplicate events return the original response without repeating the handler.
- Failed events retain an error and may be retried safely.
- IndexedDB Field Mode queue, offline fallback and service-worker assets.
- Idempotent critical-email queue with exponential backoff and a terminal retry limit.
- Provider credentials remain unconfigured and delivery remains disabled in staging.

## Remaining live validation

1. Route each approved clock, timesheet, photo and field-form action through the processor.
2. Verify the PWA assets from the final JobHub application origin on Android and iPhone.
3. Select the Premier Brushworks business email provider and configure its sender identity.
4. Enable offline sync and email separately in staging only after field-device testing.
