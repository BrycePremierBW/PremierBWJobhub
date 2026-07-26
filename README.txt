Premier Brushworks JobHub - Take-off Job Pack Importer

INCLUDED CHANGES
- Adds Estimating > Import Take-off Job Pack.
- Keeps the earlier left-sidebar menu fix: submenu choices are always visible.
- Safely previews and imports PB_JobHub_Takeoff_Job_Pack ZIP files.
- Creates a linked draft Estimate Working Sheet.
- Stores estimated labour hours and material allowance against each take-off item.
- Updates the Job Budget with total labour hours/cost, materials, access, subcontractors and sundries.
- Imports preliminary materials and colour/finish schedule rows.
- Files plans, marked-up plans, specifications/scope, colour schedules, purchase orders, take-off reports and internal job sheets under the selected Job Folder.
- Blocks duplicate Pack ID + Revision imports for the same job.
- Adds correct MIME types for job-document downloads.
- Includes a downloadable template pack inside JobHub and in this ZIP.

INSTALL
1. In GitHub, open BrycePremierBW/PremierBWJobhub.
2. Replace the existing root pb_jobhub_app.py with the supplied pb_jobhub_app.py.
3. Commit directly to main.
4. Render should redeploy automatically.
5. Refresh JobHub with Ctrl+F5.
6. Open Estimating > Import Take-off Job Pack.

VALIDATION COMPLETED
- Python AST parse: passed.
- Python bytecode compilation: passed.
- No duplicate top-level function/class definitions: passed.
- Template ZIP parser smoke test: passed.
- Full SQLite importer smoke test: passed, including estimate, item hours, job budget, materials/colours, document attachment and import tracking.

IMPORTANT
The ChatGPT GitHub connector remains read-only, so this package has not been pushed to GitHub or deployed to Render.
