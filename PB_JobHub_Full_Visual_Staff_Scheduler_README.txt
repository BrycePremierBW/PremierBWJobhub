PREMIER BRUSHWORKS JOBHUB
FULL VISUAL STAFF SCHEDULER RESTORE

RESTORED LOCATION
JobHub > Site Operations > Staff Scheduler

MANAGER / ADMIN VIEWS
- Scheduler dashboard
- Crew timeline graph
- Weekly schedule grid
- Workload and remaining-capacity graph
- Visual Schedule Board
- Add one assignment
- Allocate crews over a date range
- Edit and delete assignments
- Copy the displayed first week into the next week
- Jobs → Crew view
- Staff → Jobs view
- Open jobs without assigned crew
- Unassigned staff
- Leave requests and approvals
- Overlap, approved-leave and excessive-hours warnings
- Staff / Job sync information
- CSV export

EMPLOYEE VIEWS
- My Schedule
- My Leave

DATA SAFETY
- JobHub remains the master system.
- The restored scheduler reads the existing jobs and employees.
- It uses the existing staff_schedule records.
- It uses the same PostgreSQL DATABASE_URL as JobHub.
- Local mode uses the same jobhub.db.
- The current app is patched instead of replaced.
- A timestamped pb_jobhub_app.py backup is created automatically.

INSTALLATION
1. Extract the ZIP.
2. Copy ALL extracted files into the folder containing pb_jobhub_app.py.
3. Double-click RUN_INSTALL_FULL_VISUAL_STAFF_SCHEDULER.bat.
4. Confirm it says the full visual scheduler was restored successfully.
5. Commit/upload:
   - pb_jobhub_app.py
   - pb_jobhub_visual_scheduler.py
   - requirements.txt, when changed
6. In Render choose Manual Deploy > Deploy latest commit.
7. Open Site Operations > Staff Scheduler.

The old small Control Centre scheduling board is retained as a basic entry screen.
The restored visual scheduler is the full scheduling page.

The installer adds plotly>=5.18,<7 to requirements.txt when present.
If no requirements file is found, copy the line in PB_SCHEDULER_REQUIREMENTS.txt
into the requirements file used by Render.
