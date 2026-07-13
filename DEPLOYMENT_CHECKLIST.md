# JobHub modular deployment checklist

## Before changing GitHub

1. Download a ZIP backup of the current GitHub repository.
2. Keep the existing `assets/` folder, especially `PB_Logo_Main_PNG.png`.
3. Keep the existing `templates/` folder and its fillable PDF files.
4. Do not change or delete Render environment variables, especially `DATABASE_URL` and `DATA_DIR`.
5. Do not create a new Supabase project. The modular app uses the existing database tables.

## Uploading the modular project

Replace the old `pb_jobhub_app.py` and add the complete `jobhub/` folder, `requirements.txt`, `.streamlit/config.toml`, `README.md`, and `tests/` folder.

Merge the supplied `assets/` and `templates/` folders with the existing repository folders. The supplied folders contain only README placeholders because the actual private assets were not included in the uploaded Python file.

## Render settings

The start command remains:

```text
streamlit run pb_jobhub_app.py --server.port=$PORT --server.address=0.0.0.0
```

After committing, use **Manual Deploy → Clear build cache & deploy** once, because the project layout and Python module cache have changed.

## Verification

Before uploading, from the extracted project folder run:

```powershell
py -m pip install -r requirements.txt
py .\tests\smoke_test.py
```

The final line should say that all 22 routes passed.

After Render shows **Live**, check:

- Admin login
- Dashboard
- Job folders
- Job register
- Timesheets
- Materials and wages
- Equipment
- Photos and documents
- Estimates and take-offs
- Reports

## Rollback

If Render fails, restore the repository backup or revert the GitHub commit. The modular package does not intentionally alter or reset existing database records.

## Material-order approval migration

The first startup after deployment automatically creates `material_order_requests` and
`material_order_items` and adds approval-link fields to `material_entries`. Existing material
entries and job records are retained.

The updated requirements include `reportlab`, which generates the final approved order PDF.
Run both tests before uploading:

```powershell
py .\tests\smoke_test.py
py .\tests\material_order_workflow_test.py
```
