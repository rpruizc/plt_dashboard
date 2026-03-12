"""
User CRUD and login code functions.
"""

import secrets
from datetime import datetime, timedelta

from db.core import (
    get_db,
    _ensure_super_admin_conn,
    _ensure_default_profile_setting_conn,
    _ensure_user_default_profile_conn,
)


def create_or_get_user(email: str) -> dict:
    """Create a user if new, or return existing. First user becomes super_admin."""
    conn = get_db()
    existing = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        _ensure_user_default_profile_conn(conn, existing["id"])
        _ensure_default_profile_setting_conn(conn)
        conn.commit()
        user = dict(existing)
        conn.close()
        return user

    count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    role = "super_admin" if count == 0 else "pending"
    conn.execute("INSERT INTO users (email, role) VALUES (?, ?)", (email, role))
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    _ensure_user_default_profile_conn(conn, user["id"])
    _ensure_default_profile_setting_conn(conn)
    conn.commit()
    conn.close()
    return dict(user)


def get_user_by_email(email: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_users() -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_user_role(email: str, role: str):
    conn = get_db()
    conn.execute("UPDATE users SET role = ? WHERE email = ?", (role, email))
    conn.commit()
    conn.close()


def delete_user(email: str):
    conn = get_db()
    conn.execute("DELETE FROM users WHERE email = ?", (email,))
    _ensure_super_admin_conn(conn)
    _ensure_default_profile_setting_conn(conn)
    conn.commit()
    conn.close()


def store_login_code(email: str, code: str, expires_at: datetime):
    conn = get_db()
    conn.execute("UPDATE login_codes SET used = 1 WHERE email = ? AND used = 0", (email,))
    conn.execute(
        "INSERT INTO login_codes (email, code, expires_at) VALUES (?, ?, ?)",
        (email, code, expires_at.isoformat()),
    )
    conn.commit()
    conn.close()


def verify_login_code(email: str, code: str) -> bool:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM login_codes WHERE email = ? AND code = ? AND used = 0 ORDER BY created_at DESC LIMIT 1",
        (email, code),
    ).fetchone()
    if not row:
        conn.close()
        return False

    expires_at = datetime.fromisoformat(row["expires_at"])
    if datetime.now() > expires_at:
        conn.close()
        return False

    conn.execute("UPDATE login_codes SET used = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return True


def generate_login_code() -> tuple[str, datetime]:
    """Generate a 6-digit code and expiry (5 minutes from now)."""
    code = f"{secrets.randbelow(1000000):06d}"
    expires_at = datetime.now() + timedelta(minutes=5)
    return code, expires_at
