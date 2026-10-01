"""Vercel entry point: Vercel's Python runtime serves the `handler` class (a BaseHTTPRequestHandler)."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.server import Handler as handler  # noqa: E402,F401
