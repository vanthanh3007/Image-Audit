"""Image Audit Tool - Flask application entry point."""
import os
from flask import Flask, render_template, jsonify, request
from routes.api_domains import bp as domains_bp
from routes.api_rules import bp as rules_bp
from routes.api_scan import bp as scan_bp
from routes.api_auth import bp as auth_bp, require_auth, get_current_token
from services.auth_service import verify_token

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "image-audit-dev-key-change-in-prod")

# Register API blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(domains_bp)
app.register_blueprint(rules_bp)
app.register_blueprint(scan_bp)

# Initialize background scheduler (skip on Vercel serverless and reloader)
IS_VERCEL = os.environ.get("VERCEL")
if not IS_VERCEL and (not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true"):
    try:
        from services.scheduler import init_scheduler
        init_scheduler()
    except Exception as e:
        app.logger.warning(f"Scheduler init failed: {e}")


# ─── Auth middleware: protect all routes except login/setup/static ───
OPEN_PATHS = {"/api/auth/login", "/api/auth/setup", "/api/auth/check", "/api/cron"}


@app.before_request
def check_auth():
    """Require authentication for all routes except login and setup."""
    path = request.path

    # Allow open paths
    if path in OPEN_PATHS:
        return None

    # Allow static files
    if path.startswith("/static/"):
        return None

    # Check auth token
    token = get_current_token()
    session = verify_token(token)

    # API routes return JSON 401
    if path.startswith("/api/") and not session:
        return jsonify({"error": "Unauthorized"}), 401

    # Page routes: let frontend handle auth check
    # (SPA will call /api/auth/check and show login if needed)
    return None


@app.route("/")
def index():
    """Serve SPA for root."""
    return render_template("index.html")


@app.errorhandler(404)
def catch_all(e):
    """Serve SPA for all non-API 404s (client-side routing)."""
    if request.path.startswith("/api/"):
        return jsonify({"error": "API route not found"}), 404
    return render_template("index.html")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
