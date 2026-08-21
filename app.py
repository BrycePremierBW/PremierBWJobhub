# App entrypoint wrapper for hosting platforms that expect app.py
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from jobhub.persistent_login import install as install_persistent_login

install_persistent_login()

import pb_jobhub_app
