# Premier Brushworks JobHub — Modular Edition

This package restructures the former single-file application into a maintainable project.

## Structure

- `pb_jobhub_app.py` — small Streamlit entry point and page dispatcher
- `jobhub/database.py` — schema, connection pool, queries and cached lookups
- `jobhub/security.py` — login, users, employee portal and safe deletion
- `jobhub/documents.py` — PDF/document imports and printable forms
- `jobhub/operations.py` — photos and timesheets
- `jobhub/estimating.py` — estimates, products and forecasting
- `jobhub/control_centre.py` — budgets, variations, claims and scheduling
- `jobhub/takeoff.py` — take-off and progress calculation services
- `jobhub/mapping.py` — drawing and 3D mapping
- `jobhub/ai_tools.py` — JobHub AI and developer tools
- `jobhub/pages/` — isolated user-facing pages that were formerly embedded in routing

## Deploy to Render

Replace the repository contents with this package while retaining any existing `assets/`,
`templates/` and Supabase environment settings. The Render start command remains:

```bash
streamlit run pb_jobhub_app.py --server.port=$PORT --server.address=0.0.0.0
```

## Local test

```powershell
$env:DATA_DIR="$PWD\data"
py -m pip install -r requirements.txt
py -m py_compile pb_jobhub_app.py
py -m streamlit run pb_jobhub_app.py
```

## Important

The database table names and saved-record structure are preserved. This is a code/project
restructure, not a database reset or migration.

## Automated smoke test

Before deployment, run:

```powershell
py .\tests\smoke_test.py
```

This compiles every module, logs in to a fresh local database and renders all major routes.

## Material order approval workflow

Employee accounts now create material-order drafts from **Employee Portal → Generate Forms**.
Employees can add multiple saved or one-off products, submit the order to admin, see returned
comments, revise the order, and download the approved PDF.

Admin accounts review requests under **Materials → Material Order Approval Queue**. Approval:

1. generates a uniquely numbered approved PDF;
2. attaches it to the selected job;
3. records approver, approval time and notes; and
4. posts approved items to the job material cost register once only.

Only admin accounts can approve, return or reject requests. Manager accounts have read-only
visibility of the queue.
