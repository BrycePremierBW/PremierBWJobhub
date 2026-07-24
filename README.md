# Premier Brushworks JobHub

This is the secured and consolidated JobHub application package.

## Start locally

1. Create a Python virtual environment.
2. Install `requirements.txt`.
3. Copy `.env.example` values into your local environment or Streamlit secrets.
4. Set `DATA_DIR` to a writable, backed-up folder.
5. On a brand-new database only, set a strong temporary
   `JOBHUB_BOOTSTRAP_ADMIN_PASSWORD`.
6. Run:

   `streamlit run pb_jobhub_app.py`

7. Sign in, change the temporary password, then remove the bootstrap password
   from the environment.

The app applies restart-safe schema migrations automatically. Never test a new
release first against the only production database.

## Production defaults

- No fixed default accounts are created.
- External AI is disabled.
- AI personal-data context is disabled.
- App self-editing is disabled.
- Employees see only jobs they lead, are scheduled on, or are explicitly
  granted.
- Submitted timesheets do not affect wages until approval.

See `MIGRATION_AND_CUTOVER.md` before deployment and `SECURITY.md` for operating
requirements.
