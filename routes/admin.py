"""
Admin routes — /admin/users + approve/deny/make-admin.
"""

from flask import Blueprint, redirect, render_template, request, url_for

from database import get_all_users, update_user_role, delete_user
from routes.auth import admin_required

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/admin/users")
@admin_required
def admin_users():
    users = get_all_users()
    return render_template("admin.html", users=users, current_user=request.current_user)


@admin_bp.route("/admin/users/approve", methods=["POST"])
@admin_required
def admin_approve():
    email = request.form.get("email", "")
    update_user_role(email, "user")
    return redirect(url_for("admin.admin_users"))


@admin_bp.route("/admin/users/make-admin", methods=["POST"])
@admin_required
def admin_make_admin():
    email = request.form.get("email", "")
    update_user_role(email, "admin")
    return redirect(url_for("admin.admin_users"))


@admin_bp.route("/admin/users/deny", methods=["POST"])
@admin_required
def admin_deny():
    email = request.form.get("email", "")
    delete_user(email)
    return redirect(url_for("admin.admin_users"))
