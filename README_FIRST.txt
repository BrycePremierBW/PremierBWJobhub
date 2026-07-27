PREMIER BRUSHWORKS JOBHUB — COMPLETE REPLACEMENT v5.1
===================================================

LINKED PROGRESS AND SCHEDULER
-----------------------------
- JobHub is the master system for job dates, estimator quantities, progress and scheduling.
- Estimating > Job Progress Tracker creates the exact dwelling count for a selected job.
- Internal completion is weighted from floor m2 and the confirmed painting stages.
- External completion is weighted from estimator substrate m2.
- Staff Scheduler > Crew Suggestions explains recommended crews and waits for approval.
- Schedule entries marked "Keep linked to job start date" move automatically when that job date changes.
- Unticked/manual schedule dates remain fixed.

This is a complete replacement app. It does not patch your existing Python file.

WHAT IT KEEPS
-------------
The app uses the existing JobHub table names and SHA-256 password format. When
Render keeps the same DATABASE_URL, existing jobs, builders/clients, employees,
users, staff_schedule entries, timesheets, materials and variations remain in
place.

SAFE REPLACEMENT STEPS
----------------------
1. Download your existing repository or make a Git backup.
2. Replace the old pb_jobhub_app.py with the one in this folder.
3. Add pb_jobhub_visual_scheduler.py beside it.
4. Replace requirements.txt with the one in this folder.
5. Keep your templates/assets folders if you still need them for archived files.
6. On Render, keep the existing DATABASE_URL unchanged.
7. Redeploy.
8. Open Staff Scheduler from JobHub's left menu.

BRING ACROSS THE OLD STANDALONE SCHEDULER
-----------------------------------------
Inside JobHub open:
Staff Scheduler > Import old scheduler

Upload the complete .db backup downloaded from the standalone staff scheduler.
The import matches jobs by job number and staff by name, and skips duplicate
assignments so it is safe to run again.

LOCAL WINDOWS TEST
------------------
Double-click RUN_JOBHUB_WINDOWS.bat.
Local mode creates data/jobhub.db in this folder.

FIRST-TIME FALLBACK LOGIN
-------------------------
admin / admin123

Existing JobHub accounts remain available when the same database is used.
Change the fallback password immediately through User Access.

IMPORTANT SCOPE NOTE
--------------------
This clean replacement focuses on the operational JobHub core: jobs, staff,
visual scheduling, leave, timesheets, materials, variations, reports and users.
Experimental tools from older builds (such as 3D mapper and App Builder AI) are
not included in this replacement.


BUNDLED JOB IMPORT
------------------
This package includes PB_jobs.csv with 14 jobs (PB26001–PB26014). On the first
startup after deployment, JobHub imports any job numbers that are not already
in the database. Existing matching jobs are not overwritten. The import result
is shown under System.
