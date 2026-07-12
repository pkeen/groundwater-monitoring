"""Vercel Python runtime entrypoint - just re-exports the FastAPI app so
`vercel.json` can route all requests to it. See ../app/main.py for the
actual routes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402,F401
