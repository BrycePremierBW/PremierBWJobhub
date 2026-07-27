# JobHub Linked Progress and Smart Scheduler

Build: `2026.07.28-linked-progress-smart-scheduler-v1`

## Job Progress Tracker

Open **Estimating > Job Progress Tracker**.

1. Select a JobHub job.
2. Set the number of dwellings/units and total internal floor m2.
3. Link the approved or latest Estimate Working Sheet.
4. Save setup. JobHub creates exactly the requested dwelling rows.
5. Update Sealer, Spray Walls, Spray Ceilings, Spray Gloss, PC and Touch-ups.
6. External estimator m2 is grouped into progress rows by substrate.

JobHub calculates:

- internal completed and remaining floor m2;
- external completed and remaining substrate m2;
- weighted internal, external and overall percentage;
- earned and remaining value from the linked estimate or contract;
- downloadable progress workbook.

Linked external estimator quantities refresh automatically whenever JobHub
reruns. There is no duplicate estimator copy to maintain.

## Staff Scheduler

Open **Site Operations > Staff Scheduler**.

- **Crew Suggestions** compares job dates, estimator labour hours, progress,
  staff roles, capacity, approved leave and existing assignments.
- Every suggestion explains its basis.
- Suggestions never alter the schedule until an admin or manager approves.
- New single and bulk allocations can be kept linked to the job start date.
- When a linked job start date changes, linked assignments move by the same
  offset automatically.
- Unticked/manual assignments stay fixed.

## Safety

All schema changes are idempotent `CREATE TABLE IF NOT EXISTS` or guarded
column additions. Existing jobs, estimates, schedules and staff data are
preserved. Keep the existing production `DATABASE_URL` and `/var/data` disk.
