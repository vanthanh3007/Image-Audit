"""API routes for domain management, schedule config, and per-domain scan settings."""
import json
from flask import Blueprint, request, jsonify
from services import supabase_client as db
from routes.api_settings import get_scan_config

bp = Blueprint("domains", __name__, url_prefix="/api/domains")

# Default thresholds for new domains (read from global settings table)
def _get_default_thresholds():
    """Get default thresholds from global settings for new domain creation."""
    config = get_scan_config()
    return {
        "size_threshold_kb": config["size_threshold_kb"],
        "dimension_threshold_px": config["dimension_threshold_px"],
    }


@bp.route("", methods=["GET"])
def list_domains():
    """List all saved domains."""
    rows = db.select("domains", {"select": "*", "order": "created_at.desc"})
    return jsonify(rows)


@bp.route("", methods=["POST"])
def create_domain():
    """Save a new domain."""
    data = request.get_json()
    url = (data.get("url") or "").strip().rstrip("/")
    name = (data.get("name") or "").strip()

    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        defaults = _get_default_thresholds()
        rows = db.insert("domains", {
            "url": url,
            "name": name or url,
            "size_threshold_kb": defaults["size_threshold_kb"],
            "dimension_threshold_px": defaults["dimension_threshold_px"],
        })
        return jsonify(rows[0]), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/<domain_id>", methods=["DELETE"])
def delete_domain(domain_id):
    """Delete a domain and all related data."""
    from services.scheduler import remove_job
    remove_job(domain_id)
    db.delete("domains", {"id": f"eq.{domain_id}"})
    return jsonify({"ok": True})


# ─── Per-domain scan thresholds ───

@bp.route("/<domain_id>/config", methods=["GET"])
def get_domain_config(domain_id):
    """Get scan thresholds for a specific domain."""
    rows = db.select("domains", {
        "id": f"eq.{domain_id}",
        "select": "size_threshold_kb,dimension_threshold_px",
    })
    if not rows:
        return jsonify({"error": "Domain not found"}), 404
    return jsonify(rows[0])


@bp.route("/<domain_id>/config", methods=["PUT"])
def update_domain_config(domain_id):
    """Update scan thresholds for a specific domain.

    Body: { "size_threshold_kb": 500, "dimension_threshold_px": 2000 }
    """
    data = request.get_json()
    updates = {}

    for key in ("size_threshold_kb", "dimension_threshold_px"):
        if key in data:
            try:
                val = float(data[key])
                if val < 0:
                    return jsonify({"error": f"'{key}' must be >= 0"}), 400
                updates[key] = val
            except (ValueError, TypeError):
                return jsonify({"error": f"'{key}' must be a number"}), 400

    if not updates:
        return jsonify({"error": "No valid config fields provided"}), 400

    db.update("domains", {"id": f"eq.{domain_id}"}, updates)
    return jsonify({"ok": True, "updated": updates})


# ─── Schedule CRUD ───

@bp.route("/<domain_id>/schedule", methods=["GET"])
def get_schedule(domain_id):
    """Get current auto-scan schedule config for a domain."""
    rows = db.select("domains", {
        "id": f"eq.{domain_id}",
        "select": "scan_schedule",
    })
    if not rows:
        return jsonify({"error": "Domain not found"}), 404
    raw = rows[0].get("scan_schedule")
    # Parse JSON string if needed (Supabase may return string for JSONB)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raw = None
    return jsonify({"schedule": raw})


@bp.route("/<domain_id>/schedule", methods=["PUT"])
def update_schedule(domain_id):
    """Save or update auto-scan schedule for a domain.

    Body: { "mode": "daily", "time": "02:00", "crawl_method": "auto", "max_pages": 200 }
    Only daily mode is supported to prevent server overload from concurrent scans.
    """
    from services.scheduler import add_or_update_job

    data = request.get_json()
    mode = data.get("mode")

    if mode != "daily":
        return jsonify({"error": "Only 'daily' mode is supported"}), 400

    time_str = data.get("time", "02:00")
    # Validate HH:MM format
    try:
        h, m = time_str.split(":")
        assert 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except Exception:
        return jsonify({"error": "time must be HH:MM format"}), 400

    schedule = {
        "mode": "daily",
        "time": time_str,
        "crawl_method": data.get("crawl_method", "auto"),
        "max_pages": int(data.get("max_pages", 200)),
        "max_depth": int(data.get("max_depth", 2)),
    }

    # Save to DB
    db.update("domains", {"id": f"eq.{domain_id}"}, {"scan_schedule": json.dumps(schedule)})

    # Update scheduler job
    domain = {"id": domain_id, "scan_schedule": schedule}
    add_or_update_job(domain)

    return jsonify({"schedule": schedule})


@bp.route("/<domain_id>/schedule", methods=["DELETE"])
def delete_schedule(domain_id):
    """Disable auto-scan for a domain."""
    from services.scheduler import remove_job

    db.update("domains", {"id": f"eq.{domain_id}"}, {"scan_schedule": None})
    remove_job(domain_id)

    return jsonify({"ok": True})
