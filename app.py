"""Image Audit Tool - Flask application entry point."""
import os
from flask import Flask, render_template, jsonify, request
from routes.api_domains import bp as domains_bp
from routes.api_rules import bp as rules_bp
from routes.api_scan import bp as scan_bp

app = Flask(__name__)

# Register API blueprints
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
