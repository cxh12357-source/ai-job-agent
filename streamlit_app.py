"""Streamlit Community Cloud safe entrypoint.

The hosted edition never launches a browser or submits an application. The
desktop entrypoint remains ``app.py`` and keeps its existing local behavior.
"""

from __future__ import annotations

import os
import runpy
from pathlib import Path


os.environ["CLOUD_DEPLOYMENT"] = "true"
os.environ["DEMO_MODE"] = "true"
os.environ["AUTO_SUBMIT"] = "false"

# Streamlit reruns this entrypoint on every interaction. A normal import would
# cache app.py and render a blank page after the first successful script run.
runpy.run_path(str(Path(__file__).with_name("app.py")), run_name="__main__")
