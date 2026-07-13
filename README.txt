PREMIER BRUSHWORKS JOBHUB - TIDY CLEANUP
========================================

WHAT IT CHANGES
- Removes the oversized repeating logo background.
- Uses a clean commercial card and page style.
- Groups the sidebar into Home, Jobs, Site & Team, Estimating, Reports and Administration.
- Reduces the dashboard from 12 cards to four priorities.
- Adds Open Jobs, Upcoming Work and Attention tabs.
- Changes the long Control Centre radio list to a compact dropdown.
- Removes the duplicate JobHub banner displayed above every page.

WHAT IT DOES NOT CHANGE
- Database tables or Supabase data.
- Existing jobs, builders, clients, employees or products.
- Wages, timesheets, material costs, equipment, photos or documents.
- Estimates, claims, variations, passwords or uploaded files.

HOW TO USE ON WINDOWS
1. Extract this ZIP.
2. Copy tidy_jobhub.py and TIDY_JOBHUB.bat into the same folder as pb_jobhub_app.py.
3. Double-click TIDY_JOBHUB.bat.
4. Confirm it says the cleaned app passed Python compile checking.
5. Test locally with:
      py -m streamlit run pb_jobhub_app.py
6. Commit/upload the changed pb_jobhub_app.py to the GitHub repository used by Render.

SAFETY
A timestamped .before_tidy.bak backup is created first. If compile checking fails,
the original app is restored automatically.
