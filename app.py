# App entrypoint wrapper for hosting platforms that expect app.py
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pb_jobhub_app
