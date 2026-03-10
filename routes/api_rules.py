"""API routes for page rule management."""
from flask import Blueprint, request, jsonify
from services import supabase_client as db

bp = Blueprint("rules", __name__, url_prefix="/api/rules")


@bp.route("/<domain_id>", methods=["GET"])
def list_rules(domain_id):
    """List all rules for a domain."""
    rows = db.select("page_rules", {
        "select": "*",
        "domain_id": f"eq.{domain_id}",
        "order": "created_at.asc",
    })
    return jsonify(rows)


@bp.route("", methods=["POST"])
def create_rule():
    """Create a new page rule."""
    data = request.get_json()
    domain_id = data.get("domain_id")
    path_pattern = (data.get("path_pattern") or "").strip()
    category_name = (data.get("category_name") or "").strip()
    use_params = bool(data.get("use_params", False))
    title_source = (data.get("title_source") or "path").strip()

    if not domain_id or not path_pattern or not category_name:
        return jsonify({"error": "domain_id, path_pattern, category_name required"}), 400

    if not path_pattern.startswith("/"):
        path_pattern = "/" + path_pattern

    rows = db.insert("page_rules", {
        "domain_id": domain_id,
        "path_pattern": path_pattern,
        "category_name": category_name,
        "use_params": use_params,
        "title_source": title_source,
    })
    return jsonify(rows[0]), 201


@bp.route("/<rule_id>", methods=["DELETE"])
def delete_rule(rule_id):
    """Delete a page rule."""
    db.delete("page_rules", {"id": f"eq.{rule_id}"})
    return jsonify({"ok": True})
