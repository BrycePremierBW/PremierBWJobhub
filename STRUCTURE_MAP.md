# JobHub structure map

| File or folder | Current responsibility |
|---|---|
| `pb_jobhub_app.py` | Production Streamlit entry point, database compatibility layer, authentication, navigation and established JobHub pages |
| `pb_jobhub_visual_scheduler.py` | Linked visual staff scheduler and selectable staff × day board |
| `jobhub_enterprise.py` | Operations Hub, procurement, forecasting, backups and field workflows |
| `jobhub_progress_tracker.py` | Linked internal-dwelling and external-substrate progress |
| `jobhub_v2/` | Offline/idempotency/email-outbox foundations; delivery features disabled by default |
| `jobhub_v4/` | Painting Intelligence, revisions, evidence and handover packs |
| `jobhub/` | Staged modular components retained for controlled migration; not the production entry point |
| `pb_planreader_app.py` | Separate local PlanReader application |
| `assets/` | Brand assets required by JobHub |
| `templates/` | Fillable PDF templates required for operational forms |
| `tests/` | Source, regression, calculation and performance tests |

Render starts only `pb_jobhub_app.py`. Persistent business data belongs under
`DATA_DIR` or the configured PostgreSQL database and must never be committed to
the repository.
