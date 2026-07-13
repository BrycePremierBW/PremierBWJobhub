# JobHub structure map

| File or folder | Responsibility |
|---|---|
| `pb_jobhub_app.py` | Startup, login, navigation and page dispatch only |
| `jobhub/runtime.py` | Shared imports, paths and storage folders |
| `jobhub/ui.py` | Premier Brushworks branding and reusable UI helpers |
| `jobhub/database.py` | Database pool, schema, queries, cached dropdowns and seed data |
| `jobhub/security.py` | Authentication, users, employee portal and safe deletion |
| `jobhub/documents.py` | PDF imports, documents and printable forms |
| `jobhub/operations.py` | Photos and timesheets |
| `jobhub/estimating.py` | Estimates, product restoration and forecasting |
| `jobhub/control_centre.py` | Budgets, variations, claims, scheduling and approvals |
| `jobhub/takeoff.py` | Take-off, paint, labour, audit and progress calculations |
| `jobhub/mapping.py` | 3D models and drawing progress overlays |
| `jobhub/takeoff_pages.py` | Take-off and progress user interfaces |
| `jobhub/job_views.py` | Job lookup, folders and linked job information |
| `jobhub/ai_tools.py` | JobHub AI and controlled developer tools |
| `jobhub/navigation.py` | Role-aware sidebar navigation |
| `jobhub/pages/` | Dashboard, jobs, builders, employees, products, materials, wages, equipment and reports |
| `tests/smoke_test.py` | Compile, login and 22-route regression test |
