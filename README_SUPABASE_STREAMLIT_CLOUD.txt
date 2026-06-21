PB JOBHUB - SUPABASE + STREAMLIT CLOUD PACKAGE

This version is for free online access using:
- Streamlit Community Cloud for the app
- Supabase for the online PostgreSQL database

WHAT IS DIFFERENT
- The app still works locally with pb_jobhub.db if no DATABASE_URL is provided.
- Online, it uses Supabase/PostgreSQL when DATABASE_URL is added to Streamlit Secrets.
- The app should create the required Supabase tables on first run.
- Starter/demo jobs are disabled. Add real jobs manually.

FILES TO UPLOAD TO GITHUB
Upload these to your GitHub repo root:
- pb_jobhub_app.py
- requirements.txt
- .streamlit/config.toml
- SUPABASE_SCHEMA_MANUAL_BACKUP.sql
- README_SUPABASE_STREAMLIT_CLOUD.txt

DO NOT UPLOAD:
- .streamlit/secrets.toml
- Supabase passwords
- private database backups

SUPABASE STEPS
1. Go to supabase.com and create a new project.
2. Save the database password somewhere safe.
3. Open Project Settings / Connect or Database connection.
4. Copy the PostgreSQL URI / connection string.
5. It will look similar to:
   postgresql://postgres.xxxxx:YOUR-PASSWORD@aws-0-ap-southeast-2.pooler.supabase.com:6543/postgres

STREAMLIT CLOUD STEPS
1. Push/upload this package to a private GitHub repository.
2. Go to Streamlit Community Cloud.
3. Create a new app from that GitHub repo.
4. Main file path:
   pb_jobhub_app.py
5. Open Advanced settings / Secrets.
6. Paste:
   DATABASE_URL = "your Supabase connection string here"
7. Deploy.

FIRST LOGIN
Default:
- admin / admin123
- manager / manager123
- employee default password: changeme123

IMPORTANT
Change the admin and manager passwords immediately after first login.

IF TABLES DO NOT CREATE AUTOMATICALLY
1. In Supabase, open SQL Editor.
2. Open SUPABASE_SCHEMA_MANUAL_BACKUP.sql from this package.
3. Paste it into SQL Editor and run it.
4. Restart/redeploy the Streamlit app.

PHONE USE
Once Streamlit gives you the app link, staff open that link on iPhone/Android.
They do not open any app files on the phone.

SECURITY NOTE
This is a practical free setup. It uses app-level logins inside JobHub and a server-side Supabase database connection through Streamlit secrets.
Do not share the DATABASE_URL with staff.
