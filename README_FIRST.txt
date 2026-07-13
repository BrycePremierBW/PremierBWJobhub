JobHub V22 - Restored JobHub, Import-Only Takeoff/Model Workflow

Use this as the main JobHub app.

What changed:
- Restored the full JobHub sidebar/workflow: Dashboard, Control Centre, Jobs, Job Folders, Site Operations, Reports, Management, AI Assistant.
- Removed the heavy in-app Painting Take-off Generator from the visible JobHub navigation.
- Removed the in-app 3D/building/plan mapper generator from the visible JobHub navigation.
- Added Estimating > Import Take-off / Model File for files exported from PB MeasureTakeoff Studio.
- Kept Estimating > Progress / Billing Model for updating completed work and claim values.
- Added Estimating > 3D Model Viewer as a view-only page for imported model/progress sections.
- Job Folders now show Import Take-off / Model, Progress / Billing and 3D Model tabs instead of the heavy generator pages.

Separate app:
Use PB MeasureTakeoff Studio for reading plans, measurements, take-off, mapper/model creation and JobHub export ZIP files.

Render start command:
streamlit run pb_jobhub_app.py --server.port=$PORT --server.address=0.0.0.0
