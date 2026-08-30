# App entrypoint wrapper for hosting platforms that expect app.py
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from jobhub.database_runtime_guard import install_database_runtime_guard
from jobhub.persistent_login import install as install_persistent_login
from jobhub.timesheet_bulk_reassign import install as install_timesheet_bulk_reassign

install_database_runtime_guard()
install_persistent_login()
install_timesheet_bulk_reassign()

import pb_jobhub_app
