# JobHub secured release

## 2026-08-06 - Take-off rules engine, measurement validation + estimate report (plan reader)

- **Rules engine**: litres and labour hours are recalculated consistently across
  every take-off row from the measured m² (or lineal m) with configurable
  **waste %**, coats, coverage and a per-category labour rate (Walls, Ceilings,
  Exterior, Woodwork, …). The measured quantity itself is never overwritten by
  the recalculation.
- **Measurement validation**: the app now cross-checks the signals — PDF
  vector envelope, room-marker envelope and the sum of measured rooms — and
  flags a mismatch (e.g. envelope vs rooms disagreeing by >60%) or the use of
  an area-estimate fallback, so an uncalibrated take-off can't go unnoticed.
- **Professional export**: the Excel download gains a per-substrate Summary
  sheet, and a one-page PDF **estimate report** (job header, summary table and
  detail rows with rates and values) can be downloaded from the take-off page.
- **Totals and checks**: the take-off page shows live pass/fail for the
  measurement checks under the totals.

## 2026-08-06 - Exact PDF vector measurement + auto scale (plan reader)

- The plan reader now measures from the PDF's **embedded vector geometry**
  (wall lines, rectangles and curves extracted via PyMuPDF) instead of relying
  only on rendered pixels. Each page stores its auto-detected scale and wall
  lines in the job, so measurements are exact and reproducible.
- **Automatic scale detection**: a drawn scale bar (a long labelled line) or
  dimension labels such as ``3500``, ``4.8``, ``10m`` / ``8m`` are matched to
  the dimension lines they annotate, giving metres-per-PDF-point with no manual
  calibration. Elevation pages inherit this scale too, so substrate boxes are
  measured automatically when the source PDF carries dimensions.
- **Deskew**: the dominant rotation of the drawing is estimated from its
  near-axis wall lines and the wall-lines envelope is measured on the
  straightened geometry, so a crooked scan no longer inflates perimeter and
  areas.
- The external take-off now prefers a **vector-wall** footprint: the building
  envelope (and therefore perimeter, walls, soffits and fascia) is measured
  directly from the plan's wall lines. The take-off page reports the method
  used (PDF wall geometry / room markers / area estimate). Covered by
  `tests/test_planreader_vectors.py`.

## 2026-08-06 - Automatic external take-off from plan + elevations

- The app now **calculates the external take-off itself** — no manual drawing
  required. Once the plan scale is known (from room-correction markers with real
  dimensions), the building's external perimeter is solved from the plan; walls
  are `perimeter × wall height` minus the window/door areas already measured from
  elevation boxes; soffits/eaves are `perimeter × eave depth`; fascia is a
  lineal length. The "Generate external rows from plan + elevations" button on
  the take-off page adds these rows (marked as Auto external), with the wall
  height, eave depth and wall thickness adjustable as inputs.
- If no room-correction markers are positioned yet, the perimeter falls back to
  an area estimate from the measured room totals so rows can still be generated.
  Auto rows are replaced wholesale on re-generation (never duplicated), and are
  dropped if no footprint can be computed. Covered by
  `tests/test_planreader_external.py`.

## 2026-08-06 - Elevation scale calibration (measured m²)

- Elevation substrate boxes are now **measured from the drawing** instead of
  trusting a typed number. A "Calibrate scale" step in the box editor lets you
  drag one known-length reference line (a scale bar or a dimensioned feature)
  and enter its real-world length in metres. Every box's m² is then computed
  from its drawn dimensions at that scale, and re-measured as you move or resize
  it. The reference line is drawn faintly on the elevation so the calibration is
  visible and can be redone or cleared.
- A typed m² value is kept as an explicit manual override (shown without the
  "≈" that marks measured values), so nothing you already entered is lost. The
  take-off uses the same precedence (manual override → measured → stored value)
  so the elevation sheet and take-off can never disagree.
- Calibration is stored per elevation in the job file and exported in the
  Elevation Progress sheet. Covered by
  `tests/test_planreader_substrate_boxes.py` (calibration math, precedence and
  take-off integration).

## 2026-08-06 - Elevation substrate box editor

- PlanReader's Elevation progress tracker now lets you drag-and-drop boxes
  directly onto the elevation image instead of typing zone coordinates. Each box
  can be tagged with a substrate (render, cladding, soffits, fascia/trim,
  windows/doors, floors) and a progress %. Drawing is drag-to-draw, boxes can be
  moved and resized with the pointer, and edits save automatically. Positions are
  stored as exact percentages so the existing overlay renderer and progress board
  keep working with the new boxes.
- Each box can optionally carry an m² quantity, which is merged into the
  take-off table as an External row (substrate mapped to its labour category) and
  is preserved when room corrections or the room-dimension rows are rebuilt. New
  custom component `planreader_substrate_component`; pure helpers covered by
  `tests/test_planreader_substrate_boxes.py`.

## 2026-08-05 - Plan corrections, build stamps and timesheet review fix

- PlanReader gains a "Verify & Correct" page: the user taps directly on the
  rendered plan page to pin and name rooms, types their two dimensions, and the
  take-off is rebuilt from the corrected rooms (new rooms are added, matching
  detected rooms are overridden). Corrections persist per job and are re-applied
  on re-import, so the app learns from each review. New custom component
  `planreader_marker_component`; pure helpers covered by
  `tests/test_planreader_markers.py`.
- JobHub and PlanReader show a visible build stamp in the sidebar (from
  `RENDER_GIT_COMMIT`), so staff on a stale cached build can be identified.
- Fixed production crash on the timesheet pages and job-pack import: the
  `review_acceptance_checkbox` helper was dropped during the earlier app restore
  while five pages still called it, causing a `NameError`. The helper is restored.

## 2026-08-05 - Smart intake accuracy test pack

- Added an accuracy test pack (`tests/test_intake_accuracy.py`) that locks the
  realistic plan, scope-of-works and colour-schedule fixtures to exact expected
  quantities, hours and litres (23 new tests).
- Fixed scope parsing so a following item (e.g. a ceiling line) can no longer
  re-classify the substrate of the item above it; the line's own wording is used
  first and the surrounding window is only a fallback.
- "8 interior doors" style item counts are now recognised when an adjective
  (interior / internal / timber / etc.) sits between the quantity and the unit.
- Material estimates for repeated substrates now accumulate instead of silently
  dropping the later item, so a 3-coat feature wall's litres are counted.
- Plan reader address detection no longer mistakes drawing text such as
  "1 FLOOR PLAN SCALE 1:100" for a street address (street suffix must end a word).

## 2026-08-05 - Missed timesheets and mobile tuning

- Employees can catch up on missed timesheets: the roster (`staff_schedule`) is
  compared against `timesheet_entries` so any scheduled shift without a timesheet
  in the last 21 days appears with the rostered job, stage, start and finish
  pre-filled. Approved leave and days that already have a timesheet are excluded.
- Management can generate missed timesheets on an employee's behalf from the
  Review Timesheets tab, using the same catch-up list.
- Mobile app navigation now has icons for Operations Hub, Upload PO and Field
  Mode in both the phone header and the desktop sidebar.
- Phone notifications can now be disabled from the same button that enables
  them (tap while enabled to opt this device out).
- PWA installability tuning: dedicated 192×192 and 512×512 icons are generated
  from the square logo, the manifest lists them, adds display_override, language
  and app shortcuts, and iOS uses the 192px apple-touch-icon.

## 2026-08-05 - Smart document intake

- Import Take-off Job Pack now has a Smart Document Intake mode that reads
  uploaded plans, scope-of-works and colour schedules instead of a Job Pack ZIP.
- Plans are read from PDF text with labelled room dimensions converted into
  computed wall and ceiling areas; scopes are scanned for m², lineal-metre,
  each and litre take-off lines plus colours; colour schedules are read from
  CSV or Excel files.
- The intake can create a brand-new job (a standard Job Pack is synthesised
  internally so the normal importer runs unchanged), attach a new estimate to an
  existing job, or merge the take-off into the job's current estimate with
  matching lines keeping the larger quantity / hours and material quantities
  accumulated.
- Source documents are written into the job folder and attached to the job. A
  stated job number protects against attaching a take-off to the wrong job.
- Materials and colours are matched to existing products where possible and
  custom rows are created otherwise.

## 2026-08-05 - Estimate production pricing

- Estimate Summary now shows a live gross-profit margin banner built from the
  production pricing model (Strong / Acceptable / Low thresholds) with editable
  labour cost per hour, material markup per cent, material allowance and access /
  subcontractor / sundries allowances saved on the Summary.
- Line Items supports inline editing of line rates and totals plus a floor-area
  quick add that builds wall / ceiling take-off lines from room dimensions.
- A rate register records the planning rate and any production-line rate
  overrides per estimate for managers and administrators.
- Estimate pricing no longer locks on stage changes and staff never see dollar
  values in the rate register.

## 2026-08-05 - PlanReader elevation progress tracker

- Rendered elevation drawing pages are now tagged so they can be used as a
  visual progress tracker. A new Progress Tracking page marks painted/unpainted
  zones per elevation with colour-coded overlays (grey/amber/orange/green by
  completion) and builds a whole-house elevation progress board.
- Zone geometry is stored as exact percentage coords and converted to pixels
  deterministically at render time; overall and per-zone progress persist with
  the job.
- A progress board is built automatically when plans are imported, and elevation
  progress is included in the Excel export pack.

## 2026-08-05 - PlanReader take-off precision

- PlanReader now extracts labelled room dimensions from plan text (e.g.
  "Lounge 5400 x 3200") and converts them into computed internal wall and
  ceiling areas using configurable ceiling height and door/window opening
  allowances, replacing the manual "to be measured" buckets for those rooms.
- Rooms are editable in the Take-off Draft page and rows can be rebuilt after
  changing room sizes, ceiling height or opening allowance.
- Lineal quantities for skirting/architrave/trim are now turned into take-off
  rows instead of being dropped.
- Paint coverage (m²/L) is configurable when recalculating litres.
- Added room detection to the overview metrics and the Excel export pack.

## 2026-08-05 - v1.0.0

- Fixed the login flow so a successful submit renders the app in the same run
  instead of calling a forced rerun from inside the form, removing the AppTest
  KeyError caused by pruned login-form widgets.
- Stopped placing the auth token in URL query parameters; tokens now persist
  only in session state and the app_users table.
- Fixed the visual scheduler grid KeyError when schedule assignments reference
  employees deactivated after being booked, and guarded the leave page when no
  active staff exist.
- Made the plan reader import PyMuPDF lazily with a clear installation error and
  added PyMuPDF to the Python dependencies.
- Removed PII CSV exports, the nested repository zip, the dead insecure
  jobhub/security.py module (unsalted hashes and default accounts), and the
  duplicate CI workflow; added the data files to .gitignore.

## 2026-07-28 - Performance and Deployment Cleanup

- Stopped estimator-linked progress from rewriting unchanged external rows.
- Batched changed progress rows and linked scheduler date moves into single
  database transactions.
- Cached idempotent progress and scheduler schema setup per server process.
- Added linked-sync indexes and enabled SQLite WAL with normal synchronous mode.
- Disabled Streamlit's production file watcher and simplified Render's build
  command so dependency caching can be reused.
- Removed the unused duplicate app template, one-off patch installers, obsolete
  audit reports, old packaging notes and the temporary GitHub connection test.
- Added focused regression tests for no-op and batched progress syncing.

## 2026-07-28 - Linked Progress and Smart Scheduling

- Added an estimator-linked Job Progress Tracker with a fixed dwelling count for each job.
- Added internal progress stages: Sealer, Spray Walls, Spray Ceilings, Spray Gloss, PC and Touch-ups.
- Added external substrate progress using estimator m2, with Preparation, Primer / Sealer, First Coat, Final Coat and Touch-ups.
- Added weighted completion, completed/remaining m2, earned value and remaining value.
- Added automatic estimator quantity refresh across linked progress records.
- Added explainable crew suggestions using job dates, estimator hours, current progress, staff roles, capacity, leave and existing allocations.
- Suggestions require approval before staff are added to the schedule.
- Linked schedule assignments now move automatically when the master job start date changes; fixed/manual dates remain unchanged.

## 2026-07-24

- Replaced unsalted password storage with PBKDF2-SHA256 and automatic legacy
  hash upgrades.
- Removed fixed default accounts and added secure one-time administrator
  bootstrap, password policy, lockouts, and forced temporary-password changes.
- Added application and login audit logs.
- Added versioned, restart-safe database migration.
- Made timesheet approval and wage posting transactional and idempotent.
- Added safe reconciliation for legacy timesheet-generated wages.
- Replaced destructive SQLite upserts on parent records with conflict updates.
- Completed job-linked deletion coverage and recoverable job-file archiving.
- Added explicit Target Gross Margin versus Markup estimating.
- Corrected committed and actual material-cost reporting.
- Added stable variation and claim numbering.
- Removed embedded customer, employee, payroll, and job starter data.
- Added protected administrator CSV imports.
- Restricted employee access to assigned jobs.
- Added upload size, image validation, PDF validation, storage-path, URL-fetch,
  redirect, and external-AI safeguards.
- Disabled production self-editing by default and made rollback cover all
  touched files.
- Added row-version conflict protection for job editing.
- Consolidated duplicate implementations and added deployment files, tests,
  templates, and operating documentation.
- Installed the approved logo and supplied Master Checklist and Paint &
  Materials Order templates.
- Added a deployment-safe generic Day Labour Sheet and JobHub generation flow
  populated from job details and non-rejected timesheets.
- Added canonical-field, widget-value, and appearance-stream verification to
  generated PDF forms.
- Made timesheet totals recalculate live from start time, finish time, and break
  minutes, including overnight shifts.
- Added review summaries and required acceptance checkboxes to timesheet entry,
  individual approval, job-filter, and bulk-approval selections.
