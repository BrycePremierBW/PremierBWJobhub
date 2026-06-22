PB JOBHUB - SUPABASE QUERY FIX

Fix in this version:
- Fixed PostgreSQL/psycopg2 crash caused by column aliases containing % symbols, such as "Rate + 10%".
- Replaced pandas read_sql_query for Supabase with a direct psycopg2 fetch.
- This removes the repeated pandas warning:
  "pandas only supports SQLAlchemy connectable..."
- Keeps Supabase online database support.
- Keeps Job Photos and Restore Master Lists features if present in your previous package.

What to upload to GitHub:
- pb_jobhub_app.py
- requirements.txt
- SUPABASE_SCHEMA_MANUAL_BACKUP.sql
- .streamlit/config.toml
- README files

Then redeploy/reboot Streamlit.
