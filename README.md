# Premier Brushworks JobHub cleanup

## Included changes

- Clean commercial theme with no large background watermark.
- Sidebar consolidated into Home, Jobs, Site & Team, Estimating, Reports and Administration.
- Employee accounts remain limited to the Employee Portal.
- Dashboard reduced from twelve cards to four priorities: Active Jobs, Pending Timesheets, Open Variations and Overdue Claims.
- Dashboard tabs for Open Jobs, Upcoming Work and Attention.
- Control Centre's long radio list changed to a compact selector.
- Duplicate page banner removed.
- Old storage checks hidden unless `SHOW_STORAGE_CHECK=true`.
- Deprecated Streamlit sizing arguments updated.

## Saved data is not altered

The cleanup does not change Supabase/database tables, jobs, clients, staff, products, timesheets, wages, costs, equipment, photos, documents, estimates, claims, variations, users or passwords.

## Windows instructions

1. Put these three files beside your current `pb_jobhub_app.py`.
2. Double-click `TIDY_JOBHUB.bat`.
3. Confirm the window says the compile check passed.
4. Test locally with `py -m streamlit run pb_jobhub_app.py`.
5. Upload or commit the changed `pb_jobhub_app.py` to the GitHub repository used by Render.

A timestamped `.before_tidy.bak` file is created before any changes.
