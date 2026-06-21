PB JOBHUB - SUPABASE + STREAMLIT CLOUD WITH JOB PHOTOS

Added in this version:
- New Job Photos section for admin/manager users
- Employees can upload photos from the Employee Portal
- Photos are linked to a specific job
- Photos can be categorised:
  - Before
  - During Works
  - After
  - Defect / Damage
  - Access / Safety
  - Materials
  - Equipment
  - Completion / Sign-off
  - Other
- Photos show inside Reports / Export > Job Pack by Job
- Job Pack Excel export now includes a Job Photos sheet
- General Reports now includes a Job Photos report

Storage note:
- Photos are compressed and saved into the database as JPEG/base64.
- This is simple and works with Streamlit Cloud + Supabase.
- For lots of high-volume photo storage later, the next upgrade should use Supabase Storage buckets.

Deploy:
1. Upload these files to your private GitHub repo.
2. Deploy the repo on Streamlit Community Cloud.
3. Add DATABASE_URL in Streamlit Secrets.
4. If tables do not auto-create, run SUPABASE_SCHEMA_MANUAL_BACKUP.sql in Supabase SQL Editor.

Important:
Do not upload your actual .streamlit/secrets.toml file to GitHub.
