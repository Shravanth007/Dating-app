"""Vercel entrypoint (Flask preset is auto-detected). Every request is handed to our own WSGI router."""
from flask import Flask

from backend import server

app = Flask(__name__)
app.wsgi_app = server.app
