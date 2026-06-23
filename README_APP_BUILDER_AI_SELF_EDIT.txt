PB JOBHUB - APP BUILDER AI SELF-EDIT UPDATE

Added:
- App Builder AI > Self-Edit Code section.
- The AI can generate exact code replacement JSON.
- Admin can review and apply code changes inside the running Streamlit app.
- Safety checks:
  - only approved files can be changed
  - exact find/replace only
  - backup created before applying
  - pb_jobhub_app.py is compile-checked after applying
  - if compile fails, the app attempts rollback
- Download buttons let you download the modified app files and upload them to GitHub.

Important:
- Streamlit Cloud file changes may not persist through redeploy.
- To make changes permanent, download the updated file and upload it to GitHub.
- This does not give the AI access to your secrets.
- Keep OPENAI_API_KEY in Streamlit Secrets only.

Streamlit Secrets:
OPENAI_API_KEY = "sk-..."
OPENAI_MODEL = "gpt-5.5"
