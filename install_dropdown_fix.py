from pathlib import Path
import py_compile
import shutil
import sys

APP = Path("pb_jobhub_app.py")
BACKUP = Path("pb_jobhub_app_before_dropdown_fix.py")
MARKER = "PB_JOBHUB_DROPDOWN_VISIBILITY_FIX"
CSS = '\n        /* PB_JOBHUB_DROPDOWN_VISIBILITY_FIX */\n        div[data-baseweb="popover"] {\n            z-index: 1000000 !important;\n            max-width: min(560px, calc(100vw - 20px)) !important;\n        }\n        div[data-baseweb="popover"] [role="listbox"],\n        div[data-baseweb="popover"] ul {\n            max-height: min(62vh, 560px) !important;\n            overflow-y: auto !important;\n            overflow-x: hidden !important;\n            overscroll-behavior: contain !important;\n            scrollbar-gutter: stable !important;\n            padding: 0.35rem !important;\n            background: #ffffff !important;\n            border-radius: 12px !important;\n        }\n        div[data-baseweb="popover"] [role="option"],\n        div[data-baseweb="popover"] li,\n        div[data-baseweb="popover"] [role="option"] *,\n        div[data-baseweb="popover"] li * {\n            min-height: 42px !important;\n            height: auto !important;\n            white-space: normal !important;\n            overflow: visible !important;\n            text-overflow: clip !important;\n            line-height: 1.3 !important;\n            color: #111111 !important;\n            -webkit-text-fill-color: #111111 !important;\n        }\n        div[data-baseweb="popover"] [role="option"],\n        div[data-baseweb="popover"] li {\n            padding: 0.65rem 0.75rem !important;\n            align-items: flex-start !important;\n            border-radius: 8px !important;\n        }\n        section[data-testid="stSidebar"] [data-testid="stSelectbox"],\n        section[data-testid="stSidebar"] [data-testid="stMultiSelect"] {\n            margin-bottom: 0.75rem !important;\n        }\n        section[data-testid="stSidebar"] [data-baseweb="select"] > div {\n            min-height: 48px !important;\n            height: auto !important;\n            border-radius: 11px !important;\n        }\n        section[data-testid="stSidebar"] [data-baseweb="select"] span,\n        section[data-testid="stSidebar"] [data-baseweb="select"] input {\n            white-space: normal !important;\n            overflow: visible !important;\n            text-overflow: clip !important;\n            line-height: 1.25 !important;\n        }\n        @media (min-width: 769px) {\n            section[data-testid="stSidebar"],\n            section[data-testid="stSidebar"] > div {\n                min-width: 330px !important;\n                width: 330px !important;\n            }\n        }\n        @media (max-width: 768px) {\n            section[data-testid="stSidebar"] {\n                width: min(92vw, 360px) !important;\n                min-width: min(92vw, 360px) !important;\n            }\n            div[data-baseweb="popover"] [role="listbox"],\n            div[data-baseweb="popover"] ul {\n                max-height: 56vh !important;\n            }\n        }\n'
ANCHOR = '        section[data-testid="stSidebar"] [data-testid="stSelectbox"] label {\n            color: #f6efe7 !important;\n            -webkit-text-fill-color: #f6efe7 !important;\n        }\n'

if not APP.exists():
    sys.exit("pb_jobhub_app.py was not found. Put this patcher in the JobHub repository root.")

text = APP.read_text(encoding="utf-8")
if MARKER in text:
    print("Dropdown visibility fix is already installed.")
    raise SystemExit(0)

if ANCHOR not in text:
    sys.exit("Could not find the expected JobHub sidebar CSS. No changes were made.")

if not BACKUP.exists():
    shutil.copy2(APP, BACKUP)

APP.write_text(text.replace(ANCHOR, ANCHOR + "\n" + CSS, 1), encoding="utf-8", newline="\n")
try:
    py_compile.compile(str(APP), doraise=True)
except Exception:
    shutil.copy2(BACKUP, APP)
    raise

print("Dropdown visibility fix installed successfully.")
print("Commit and push pb_jobhub_app.py, then Render will redeploy it.")
