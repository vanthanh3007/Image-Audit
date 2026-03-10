"""Vercel serverless entry point - wraps Flask app."""
import sys
import os

# Add project root to Python path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set Vercel environment flag (disables APScheduler)
os.environ["VERCEL"] = "1"

from app import app

# Vercel expects the WSGI app to be named 'app'
app.debug = False
