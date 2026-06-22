PB JOBHUB - LINKED EMPLOYEE / USER DELETE + INACTIVE WORKER FILTER

Added:
- Employees > Employee List now has a Show inactive workers checkbox.
- By default, inactive workers are hidden.
- Tick Show inactive workers to view inactive employees.

Linked delete behaviour:
- Deleting an employee also deletes their linked user login where safe.
- Deleting a user login also deletes the linked employee where safe.
- If the employee has wage/timesheet history, the user login is deleted but the employee is marked Inactive instead.
- This protects job costing and timesheet history.
- Current logged-in account and last active admin account are protected.

This version also keeps:
- Bulk employee delete/deactivate
- Bulk user delete
- Duplicate user cleanup SQL
- Material product matching preview
- Delete User Account button
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
- README_LINKED_EMPLOYEE_USER_DELETE_AND_INACTIVE_FILTER.txt

Then reboot Streamlit.
