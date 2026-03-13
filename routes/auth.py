"""
Authentication routes — login, verify, logout, pending + decorators.
"""

import resend
from functools import wraps

from flask import Blueprint, redirect, render_template, request, session, url_for, jsonify

from database import (
    create_or_get_user,
    generate_login_code,
    get_user_by_email,
    store_login_code,
    verify_login_code,
)

auth_bp = Blueprint("auth", __name__)

ALLOWED_DOMAIN = "epiuse.com"


def _get_resend_from():
    import os
    return os.environ.get("RESEND_FROM", "PLT Dashboard <onboarding@resend.dev>")


def send_code_email(email, code):
    """Send login code via Resend."""
    resend.Emails.send(
        {
            "from": _get_resend_from(),
            "to": email,
            "subject": f"PLT Dashboard — Your login code: {code}",
            "html": (
                f"<div style='font-family:sans-serif;max-width:400px;margin:0 auto;padding:20px'>"
                f"<h2 style='color:#6366f1'>PLT Dashboard</h2>"
                f"<p>Your verification code is:</p>"
                f"<p style='font-size:32px;font-weight:bold;letter-spacing:8px;color:#6366f1'>{code}</p>"
                f"<p style='color:#888'>This code expires in 5 minutes.</p>"
                f"</div>"
            ),
        }
    )


# --- Auth decorators (importable by other blueprints) ---


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        email = session.get("user_email")
        if not email:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("auth.login"))
        user = get_user_by_email(email)
        if not user:
            session.clear()
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("auth.login"))
        if user["role"] == "pending":
            if request.path.startswith("/api/"):
                return jsonify({"error": "Access pending approval"}), 403
            return redirect(url_for("auth.pending"))
        request.current_user = user
        return f(*args, **kwargs)

    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        email = session.get("user_email")
        if not email:
            return redirect(url_for("auth.login"))
        user = get_user_by_email(email)
        if not user or user["role"] not in ("admin", "super_admin"):
            return redirect(url_for("accounts.index"))
        request.current_user = user
        return f(*args, **kwargs)

    return decorated


def super_admin_api_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = request.current_user
        if user["role"] != "super_admin":
            return jsonify({"error": "Super admin required"}), 403
        return f(*args, **kwargs)

    return decorated


# --- Auth routes ---


@auth_bp.route("/login", methods=["GET"])
def login():
    if session.get("user_email"):
        user = get_user_by_email(session["user_email"])
        if user and user["role"] in ("user", "admin", "super_admin"):
            return redirect(url_for("accounts.index"))
    return render_template("login.html")


@auth_bp.route("/login", methods=["POST"])
def login_submit():
    email = request.form.get("email", "").strip().lower()
    if not email.endswith(f"@{ALLOWED_DOMAIN}"):
        return render_template("login.html", error=f"Only @{ALLOWED_DOMAIN} emails are allowed.", email=email)

    code, expires_at = generate_login_code()
    store_login_code(email, code, expires_at)

    try:
        send_code_email(email, code)
    except Exception as e:
        return render_template("login.html", error=f"Failed to send code: {e}", email=email)

    return render_template("login.html", step="verify", email=email)


@auth_bp.route("/verify", methods=["POST"])
def verify():
    email = request.form.get("email", "").strip().lower()
    code = request.form.get("code", "").strip()

    if not verify_login_code(email, code):
        return render_template(
            "login.html",
            step="verify",
            email=email,
            error="Invalid or expired code. Please try again.",
        )

    user = create_or_get_user(email)
    session["user_email"] = email
    session.permanent = True
    session.pop("active_scoring_profile_id", None)
    session.pop("active_scoring_profile_manual", None)

    if user["role"] == "pending":
        return redirect(url_for("auth.pending"))
    return redirect(url_for("accounts.index"))


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/pending")
def pending():
    email = session.get("user_email")
    if not email:
        return redirect(url_for("auth.login"))
    user = get_user_by_email(email)
    if user and user["role"] in ("user", "admin", "super_admin"):
        return redirect(url_for("accounts.index"))
    return render_template("pending.html", email=email)
