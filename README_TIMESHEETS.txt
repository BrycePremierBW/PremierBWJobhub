PB JOBHUB - TIMESHEETS UPDATE

Added:
- Timesheets section for admin/manager users.
- Employees can submit timesheets from the Employee Portal.
- Each timesheet is linked to a specific job and employee.
- Timesheet fields:
  - Job
  - Employee
  - Date
  - Start time
  - Finish time
  - Break minutes
  - Total hours
  - Work type
  - Notes
- Timesheets show in Reports / Export > Job Pack by Job.
- Job Pack Excel export includes a Timesheets sheet.
- General Reports includes a Timesheets report.

Notes:
- Timesheet entries also create a wage entry so existing wage/job cost reports keep working.
- Upload pb_jobhub_app.py, requirements.txt, SUPABASE_SCHEMA_MANUAL_BACKUP.sql and .streamlit/config.toml to GitHub, then reboot Streamlit.
