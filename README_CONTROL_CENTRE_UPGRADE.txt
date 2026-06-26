PB JOBHUB - CONTROL CENTRE UPGRADE

Added a new menu: Control Centre

Sections included:
1. Daily Dashboard
   - Active jobs
   - Red/orange jobs
   - Pending timesheets
   - Overdue claims
   - Jobs starting/finishing this week

2. Job Health Score
   - Green / Orange / Red status
   - GP %, cost-to-date %, remaining labour hours
   - Risk notes per job

3. Job Budget Lock-In
   - Accepted quote labour/material/access/subcontractor/sundries budget
   - Target GP %
   - Locked by / locked date

4. Variations Register
   - Draft / Sent / Approved / Rejected variations
   - Approved variations feed into adjusted contract value

5. Invoice / Claim Tracker
   - Claims/invoices by job
   - Due dates, paid dates and status
   - Claimed / paid / unpaid tracking

6. Staff Scheduling Board
   - Allocate employees to jobs by date
   - Site role, start/finish time, notes

7. Timesheet Approval
   - Approve, reject or process submitted timesheets

8. AI Job Review
   - Uses your JobHub AI / local Ollama setup
   - Reviews margin, labour, materials and schedule risk

9. Export Control Centre
   - Exports job health, variations, claims and staff schedule to Excel

Included SQL:
- PB_JobHub_Control_Centre_Tables.sql
Run this in Supabase SQL Editor if the app does not create the new tables automatically.

Upload/replace in GitHub:
- pb_jobhub_app.py
- requirements.txt
- SUPABASE_SCHEMA_MANUAL_BACKUP.sql
- PB_JobHub_Control_Centre_Tables.sql
- .streamlit/config.toml
- README_CONTROL_CENTRE_UPGRADE.txt

Then reboot Streamlit.

For local free AI:
- Keep using Ollama
- Run JobHub locally with the included local launcher
