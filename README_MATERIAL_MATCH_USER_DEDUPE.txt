PB JOBHUB - MATERIAL MATCH + USER DEDUPE UPDATE

Added:
- Material Costs now shows exactly what product code/name matches as soon as it is selected.
- Product match preview shows:
  - product code
  - product name
  - supplier
  - unit
  - unit price ex GST
  - full product details
- Material form still saves the selected product to the selected job.
- User Access now has Clean Up Duplicate User Accounts.
- Duplicate cleanup detects:
  - same username, ignoring case/spaces
  - multiple user accounts linked to the same employee
- Cleanup protects:
  - current logged-in user
  - last active admin account
- seed_app_users was tightened so duplicate employee logins are not recreated.

This version also keeps:
- Delete User Account button
- Product restore button
- Supabase connection pool speed fix
- Estimate Working Sheet
- Timesheets linked to jobs
- Job Pack report updates
- Employee delete/deactivate fix

Upload/replace in GitHub:
- pb_jobhub_app.py
- requirements.txt
- SUPABASE_SCHEMA_MANUAL_BACKUP.sql
- .streamlit/config.toml
- README_MATERIAL_MATCH_USER_DEDUPE.txt

Then reboot Streamlit.
