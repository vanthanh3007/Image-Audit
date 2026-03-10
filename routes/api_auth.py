"""API routes for authentication."""
from flask import Blueprint, request, jsonify, make_response
from services.auth_service import (
    verify_login, verify_token, logout, change_password,
    create_admin, admin_exists,
)

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def get_current_token():
    """Extract token from cookie or Authorization header."""
    token = request.cookies.get("auth_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    return token


def require_auth():
    """Check auth and return error response if not authenticated. Returns None if OK."""
    token = get_current_token()
    session = verify_token(token)
    if not session:
        return jsonify({"error": "Unauthorized"}), 401
    return None


@bp.route("/login", methods=["POST"])
def login():
    """Login with username + password. Returns token in cookie + body."""
    data = request.get_json()
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    result = verify_login(username, password)
    if not result:
        return jsonify({"error": "Invalid credentials"}), 401

    # Set HttpOnly cookie + return token in body
    resp = make_response(jsonify({
        "ok": True,
        "user": result["user"],
    }))
    resp.set_cookie(
        "auth_token",
        result["token"],
        httponly=True,
        samesite="Lax",
        max_age=86400,  # 24 hours
        secure=request.is_secure,
    )
    return resp


@bp.route("/logout", methods=["POST"])
def do_logout():
    """Logout and clear session."""
    token = get_current_token()
    logout(token)
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie("auth_token")
    return resp


@bp.route("/check", methods=["GET"])
def check_session():
    """Check if current session is valid."""
    token = get_current_token()
    session = verify_token(token)
    if session:
        return jsonify({"authenticated": True})
    return jsonify({"authenticated": False}), 401


@bp.route("/setup", methods=["POST"])
def setup_admin():
    """Create first admin account (only works if no admin exists)."""
    if admin_exists():
        return jsonify({"error": "Admin already exists"}), 409

    data = request.get_json()
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    try:
        user = create_admin(username, password)
        return jsonify({"ok": True, "user": {"username": user["username"]}}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/change-password", methods=["POST"])
def do_change_password():
    """Change password for current admin."""
    auth_err = require_auth()
    if auth_err:
        return auth_err

    data = request.get_json()
    username = (data.get("username") or "").strip()
    old_pw = (data.get("old_password") or "").strip()
    new_pw = (data.get("new_password") or "").strip()

    if not username or not old_pw or not new_pw:
        return jsonify({"error": "All fields required"}), 400
    if len(new_pw) < 6:
        return jsonify({"error": "New password must be at least 6 characters"}), 400

    if change_password(username, old_pw, new_pw):
        return jsonify({"ok": True})
    return jsonify({"error": "Invalid current password"}), 401
