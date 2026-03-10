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

def _parse_schedule_field(raw):
    """Parse scan_schedule which may be JSON string or dict."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    return raw


def _time_to_minutes(time_str):
    """Convert HH:MM to total minutes since midnight."""
    h, m = time_str.split(":")
    return int(h) * 60 + int(m)


def _check_time_conflict(domain_id, time_str):
    """Check if any other domain has a schedule within buffer minutes.
    Returns list of conflicting domains or empty list."""
    buffer = get_scan_config().get("schedule_buffer_minutes", 30)
    new_mins = _time_to_minutes(time_str)

    all_domains = db.select("domains", {
        "select": "id,name,url,scan_schedule",
        "scan_schedule": "not.is.null",
    })

    conflicts = []
    for d in all_domains:
        if d["id"] == domain_id:
            continue
        sched = _parse_schedule_field(d.get("scan_schedule"))
        if not sched or sched.get("mode") != "daily" or not sched.get("time"):
            continue
        existing_mins = _time_to_minutes(sched["time"])
        diff = abs(new_mins - existing_mins)
        # Handle wrap-around midnight (e.g., 23:50 vs 00:10)
        diff = min(diff, 1440 - diff)
        if diff < buffer:
            conflicts.append({
                "id": d["id"],
                "name": d.get("name") or d.get("url"),
                "time": sched["time"],
                "diff_minutes": diff,
            })
    return conflicts


@bp.route("/schedules", methods=["GET"])
def list_all_schedules():
    """Get all domains with their schedule info for the management view."""
    all_domains = db.select("domains", {
        "select": "id,name,url,scan_schedule",
        "order": "name.asc",
    })
    result = []
    for d in all_domains:
        sched = _parse_schedule_field(d.get("scan_schedule"))
        result.append({
            "id": d["id"],
            "name": d.get("name") or d.get("url"),
            "url": d.get("url"),
            "schedule": sched,
            "time": sched.get("time") if sched and sched.get("mode") == "daily" else None,
            "enabled": bool(sched and sched.get("mode") == "daily"),
        })
    return jsonify(result)

@bp.route("/<domain_id>/schedule", methods=["GET"])
def get_schedule(domain_id):
    """Get current auto-scan schedule config for a domain."""
    rows = db.select("domains", {
        "id": f"eq.{domain_id}",
        "select": "scan_schedule",
    })
    if not rows:
        return jsonify({"error": "Domain not found"}), 404
    raw = _parse_schedule_field(rows[0].get("scan_schedule"))
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

    # Check for time conflicts with other domains
    force = data.get("force", False)
    if not force:
        conflicts = _check_time_conflict(domain_id, time_str)
        if conflicts:
            buffer = get_scan_config().get("schedule_buffer_minutes", 30)
            names = ", ".join(f"{c['name']} ({c['time']})" for c in conflicts)
            return jsonify({
                "error": "conflict",
                "message": f"Trùng lịch (trong {buffer} phút) với: {names}",
                "conflicts": conflicts,
                "buffer_minutes": buffer,
            }), 409

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
