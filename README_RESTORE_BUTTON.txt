PB JOBHUB - SUPABASE RESTORE BUTTON VERSION

Added:
- User Access now has Restore Master Lists.
- It shows counts for Builders/Clients, Employees, Jobs and Products.
- Button: Restore Builders / Clients and Employees.
- This repopulates builders and employees from inside the app, without needing Supabase SQL Editor.
- It does NOT create starter jobs.

How to use:
1. Upload this updated package to GitHub.
2. Redeploy/reboot the Streamlit app.
3. Log in as admin.
4. Go to User Access.
5. Click Restore Builders / Clients and Employees.
6. Recheck Builders & Clients and Employees menus.

Important:
If counts restore in Supabase but still do not show in the app, check the sidebar.
It should say: Online database: Supabase.
If it says Local database, your DATABASE_URL secret is not connected correctly.
