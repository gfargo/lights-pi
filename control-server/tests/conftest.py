"""Pytest fixtures + sys.path setup for the control-server test suite.

The control server is a single-file Flask app (control-server/app.py) — to
import it from tests/, the parent directory needs to be on sys.path.
"""
import sys
from pathlib import Path

# Add the control-server directory to sys.path so `import app` works
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
