"""Streamlit Community Cloud safe entrypoint.

The hosted edition never launches a browser or submits an application. The
desktop entrypoint remains ``app.py`` and keeps its existing local behavior.
"""

from __future__ import annotations

import os


os.environ["CLOUD_DEPLOYMENT"] = "true"
os.environ["DEMO_MODE"] = "true"
os.environ["AUTO_SUBMIT"] = "false"

# Importing app renders the Streamlit application.
from app import *  # noqa: E402,F401,F403
