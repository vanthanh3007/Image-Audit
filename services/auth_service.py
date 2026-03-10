"""Authentication service - admin login with token-based sessions.

Uses Supabase to store admin users and session tokens.
Compatible with both local Flask sessions and Vercel serverless.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from services import supabase_client as db


def _hash_password(password):
    """Hash password with SHA-256 + salt. Simple but effective for single admin."""
    salt = "image_audit_v1"
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def create_admin(username, password):
    """Create an admin user. Returns user dict or raises on duplicate."""
    pw_hash = _hash_password(password)
    rows = db.insert("admin_users", {
        "username": username,
        "password_hash": pw_hash,
    })
    return rows[0] if rows else None


def verify_login(username, password):
    """Verify credentials and return a session token if valid."""
    pw_hash = _hash_password(password)
    users = db.select("admin_users", {
        "username": f"eq.{username}",
        "password_hash": f"eq.{pw_hash}",
        "select": "id,username",
    })
    if not users:
        return None

    user = users[0]
    # Create session token (valid 24 hours)
    token = secrets.token_urlsafe(48)
    expires = datetime.now(timezone.utc) + timedelta(hours=24)

    db.insert("auth_sessions", {
        "user_id": user["id"],
        "token": token,
        "expires_at": expires.isoformat(),
    })

    return {"token": token, "user": user, "expires_at": expires.isoformat()}


def verify_token(token):
    """Check if a session token is valid and not expired."""
    if not token:
        return None

    sessions = db.select("auth_sessions", {
        "token": f"eq.{token}",
        "select": "id,user_id,expires_at",
    })
    if not sessions:
        return None

    session = sessions[0]
    expires = datetime.fromisoformat(session["expires_at"].replace("Z", "+00:00"))
    if expires < datetime.now(timezone.utc):
        # Expired - clean up
        db.delete("auth_sessions", {"id": f"eq.{session['id']}"})
        return None

    return session


def logout(token):
    """Delete a session token."""
    if token:
        db.delete("auth_sessions", {"token": f"eq.{token}"})


def change_password(username, old_password, new_password):
    """Change admin password. Returns True if success."""
    old_hash = _hash_password(old_password)
    users = db.select("admin_users", {
        "username": f"eq.{username}",
        "password_hash": f"eq.{old_hash}",
        "select": "id",
    })
    if not users:
        return False

    new_hash = _hash_password(new_password)
    db.update("admin_users", {"id": f"eq.{users[0]['id']}"}, {
        "password_hash": new_hash,
    })
    return True


def admin_exists():
    """Check if any admin user exists."""
    users = db.select("admin_users", {"select": "id", "limit": "1"})
    return len(users) > 0
