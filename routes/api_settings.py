"""API routes for scan settings management."""
from flask import Blueprint, request, jsonify
from services import supabase_client as db

bp = Blueprint("settings", __name__, url_prefix="/api/settings")

# Default values if not in DB
DEFAULTS = {
    "size_threshold_kb": "1000",
    "dimension_threshold_px": "3000",
    "scan_limit_per_day": "1",
    "max_pages_per_scan": "50",
    "schedule_buffer_minutes": "30",
}


@bp.route("", methods=["GET"])
def get_settings():
    """Get all settings."""
    try:
        rows = db.select("settings", {"select": "*"})
        settings = {r["key"]: r for r in rows}

        # Fill in defaults for missing keys
        for key, default_val in DEFAULTS.items():
            if key not in settings:
                settings[key] = {
                    "key": key,
                    "value": default_val,
                    "label": key,
                }

        return jsonify(list(settings.values()))
    except Exception as e:
        # Table might not exist yet, return defaults
        return jsonify([
            {"key": k, "value": v, "label": k}
            for k, v in DEFAULTS.items()
        ])


@bp.route("", methods=["PUT"])
def update_settings():
    """Update one or more settings. Body: { "settings": { "key": "value", ... } }"""
    data = request.get_json()
    updates = data.get("settings", {})

    if not updates:
        return jsonify({"error": "No settings provided"}), 400

    results = []
    for key, value in updates.items():
        # Validate numeric values
        try:
            num = float(value)
            if num < 0:
                return jsonify({"error": f"'{key}' must be >= 0"}), 400
        except ValueError:
            return jsonify({"error": f"'{key}' must be a number"}), 400

        # Upsert setting
        try:
            existing = db.select("settings", {"key": f"eq.{key}", "select": "key"})
            if existing:
                db.update("settings", {"key": f"eq.{key}"}, {"value": str(value)})
            else:
                db.insert("settings", {"key": key, "value": str(value), "label": key})
            results.append({"key": key, "value": str(value)})
        except Exception as e:
            return jsonify({"error": f"Failed to update '{key}': {str(e)}"}), 500

    return jsonify({"ok": True, "updated": results})


def get_scan_config():
    """Helper: get current scan thresholds as a dict of numeric values."""
    try:
        rows = db.select("settings", {"select": "key,value"})
        config = {r["key"]: r["value"] for r in rows}
    except Exception:
        config = {}

    return {
        "size_threshold_kb": float(config.get("size_threshold_kb", DEFAULTS["size_threshold_kb"])),
        "dimension_threshold_px": float(config.get("dimension_threshold_px", DEFAULTS["dimension_threshold_px"])),
        "scan_limit_per_day": int(config.get("scan_limit_per_day", DEFAULTS["scan_limit_per_day"])),
        "max_pages_per_scan": int(config.get("max_pages_per_scan", DEFAULTS["max_pages_per_scan"])),
        "schedule_buffer_minutes": int(config.get("schedule_buffer_minutes", DEFAULTS["schedule_buffer_minutes"])),
    }
