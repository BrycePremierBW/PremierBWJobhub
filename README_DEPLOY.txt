PB PlanReader Render Package

Purpose:
A clean standalone Render-ready Streamlit app for uploading architectural PDFs, extracting plan/spec information, converting pages to PNG/JPEG, creating a draft painting take-off, and exporting data.

Main file:
pb_jobhub_app.py

Render start command:
streamlit run pb_jobhub_app.py --server.port=$PORT --server.address=0.0.0.0

Files to upload to GitHub:
pb_jobhub_app.py
requirements.txt
assets/
.streamlit/
render.yaml optional

Optional environment variables:
OPENAI_API_KEY       optional, only used if you press the AI extract button
OPENAI_MODEL         optional, default is gpt-4o-mini
DATA_DIR             optional, default uses /var/data if available, otherwise local data/

Important:
This package is deliberately separate from the larger JobHub build. It is designed to work cleanly and gather plan information first, without all the older JobHub complexity.
