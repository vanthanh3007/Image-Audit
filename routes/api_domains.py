"""API routes for domain management and schedule config."""
import json
from flask import Blueprint, request, jsonify
from services import supabase_client as db

bp = Blueprint("domains", __name__, url_prefix="/api/domains")


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
        rows = db.insert("domains", {"url": url, "name": name or url})
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
    or:   { "mode": "interval", "hours": 6, "crawl_method": "auto", "max_pages": 200 }
    """
    from services.scheduler import add_or_update_job

    data = request.get_json()
    mode = data.get("mode")

    if mode not in ("daily", "interval"):
        return jsonify({"error": "mode must be 'daily' or 'interval'"}), 400

    schedule = {
        "mode": mode,
        "crawl_method": data.get("crawl_method", "auto"),
        "max_pages": int(data.get("max_pages", 200)),
        "max_depth": int(data.get("max_depth", 2)),
    }

    if mode == "daily":
        time_str = data.get("time", "02:00")
        # Validate HH:MM format
        try:
            h, m = time_str.split(":")
            assert 0 <= int(h) <= 23 and 0 <= int(m) <= 59
        except Exception:
            return jsonify({"error": "time must be HH:MM format"}), 400
        schedule["time"] = time_str
    elif mode == "interval":
        hours = max(1, int(data.get("hours", 6)))
        schedule["hours"] = hours

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
