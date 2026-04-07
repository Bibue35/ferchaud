"""Vercel serverless entrypoint"""
import sys, os

# Add project root to path
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root not in sys.path:
    sys.path.insert(0, root)

os.environ.setdefault("PYTHONPATH", root)

from web.server import app  # noqa: F401 — Vercel discovers `app`
