PB JOBHUB - RESTORE MASTER DATA BUTTON

Added back into User Access:
- Restore Master Builders/Clients & Employees
- Shows current builder/client count and employee count
- Requires typing RESTORE MASTER DATA before restoring
- Restores/updates the saved master builders/clients list
- Restores/updates the saved master employee list
- Recreates missing employee login accounts where needed
- Does not delete jobs, wages, timesheets, materials, photos or history

Also included:
- PB_JobHub_Restore_Builders_Clients_Employees.sql
Use this SQL file in Supabase/Render Postgres if you want to restore the data manually.

Upload/replace in GitHub/Render source:
- pb_jobhub_app.py
- requirements.txt
- SUPABASE_SCHEMA_MANUAL_BACKUP.sql
- PB_JobHub_Restore_Builders_Clients_Employees.sql
- .streamlit/config.toml
- README_RESTORE_MASTER_DATA_BUTTON.txt

Then redeploy/reboot the app.
