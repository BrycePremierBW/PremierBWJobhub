# JobHub V4 implementation

Branch: `operations-v4` (stacked on `operations-v3`)

## Painting-specific advantage

- Coating-system quantity calculation from area, coat count, product coverage and waste.
- Lowest-purchase-cost 4 L / 10 L / 15 L pack optimisation using warehouse stock first.
- Colour approval gate that blocks ordering until status, approver and date are recorded.
- Plan/revision/location-linked progress, defect and close-out evidence.
- Drawing/specification text comparison with auditable draft-variation risk scoring.
- Builder close-out checklist and one-button ZIP handover pack.
- Restart-safe portable schema and pure unit tests.

## Safety rules

- Pack plans must cover the calculated requirement.
- Price optimisation never silently creates a material order.
- Colour approval is a hard gate; an `approved` label alone is insufficient.
- Revision comparison creates only a draft suggestion for human review.
- Handover packs explicitly list incomplete evidence and may remain in draft.
- Production `main` remains unchanged until the stacked pull requests are reviewed.

## Deployment path

1. Validate V2, V3 and V4 tests.
2. Review and merge the stacked pull requests in order.
3. Exercise colour, revision and handover flows on staging.
4. Connect a Xero demo organisation and validate draft-only accounting sync.
5. Promote the reviewed build to production.
