PB JOBHUB - EMPLOYEE DELETE FIX

Fix:
- Employees linked to app user logins can no longer crash the app when deleted.
- If an employee has any linked records, the app marks them Inactive instead of deleting:
  - wage records
  - timesheets
  - user login
- If the employee has a linked login, that login is disabled when the employee is deactivated.

This version also keeps:
- Product restore button
- Supabase connection pool speed fix
- Estimate Working Sheet
- Timesheets linked to jobs
- Job Pack report updates

Upload/replace in GitHub:
- pb_jobhub_app.py
- requirements.txt
- SUPABASE_SCHEMA_MANUAL_BACKUP.sql
- .streamlit/config.toml
- README_EMPLOYEE_DELETE_FIX.txt

Then reboot Streamlit.
