"""
Scoring routes — /scoring-profiles page + all /api/scoring-profiles/ + weights.
"""

from flask import Blueprint, jsonify, request, session

from database import (
    create_scoring_profile,
    delete_scoring_profile,
    get_default_scoring_profile_id,
    get_scoring_profile_shares,
    list_all_scoring_profiles,
    list_scoring_profiles_for_user,
    set_default_scoring_profile,
    share_scoring_profile,
    unshare_scoring_profile,
    update_scoring_profile,
)
from routes.auth import login_required, super_admin_api_required
from routes.accounts import get_active_scoring_profile, get_profile_for_user
from utils import sanitize, validate_weights_payload, validate_industry_scores_payload

scoring_bp = Blueprint("scoring", __name__)


def _parse_json_payload():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None
    return data


@scoring_bp.route("/api/weights", methods=["GET"])
@login_required
def api_get_weights():
    profile = get_active_scoring_profile(request.current_user)
    return jsonify(profile["weights"])


@scoring_bp.route("/api/weights", methods=["POST"])
@login_required
def api_set_weights():
    data = _parse_json_payload()
    if data is None:
        return jsonify({"error": "Invalid JSON payload"}), 400

    weights = validate_weights_payload(data)
    if weights is None:
        return jsonify({"error": "Weights must include all fields, be numeric 0-1, and sum to 1.0"}), 400

    profile = get_active_scoring_profile(request.current_user)
    if not profile["can_edit"]:
        return jsonify({"error": "Active profile is read-only. Duplicate it to edit."}), 403

    ok = update_scoring_profile(
        owner_user_id=request.current_user["id"],
        profile_id=profile["id"],
        name=profile["name"],
        weights=weights,
        industry_scores=profile["industry_scores"],
        description=profile.get("description", ""),
    )
    if not ok:
        return jsonify({"error": "Failed to update weights"}), 500

    return jsonify({"status": "ok", "weights": weights})


@scoring_bp.route("/api/industry-scores")
@login_required
def api_industry_scores():
    profile = get_active_scoring_profile(request.current_user)
    return jsonify(profile["industry_scores"])


@scoring_bp.route("/api/scoring-profiles", methods=["GET"])
@login_required
def api_list_scoring_profiles():
    user = request.current_user
    if user["role"] == "super_admin":
        profiles = list_all_scoring_profiles(current_user_id=user["id"])
    else:
        profiles = list_scoring_profiles_for_user(user["id"])
    active_profile = get_active_scoring_profile(user)
    return jsonify(
        {
            "profiles": sanitize(profiles),
            "active_profile_id": active_profile["id"],
            "default_profile_id": get_default_scoring_profile_id(),
        }
    )


@scoring_bp.route("/api/scoring-profiles", methods=["POST"])
@login_required
def api_create_scoring_profile():
    user = request.current_user
    data = _parse_json_payload() or {}

    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"error": "Profile name is required"}), 400

    description = str(data.get("description", "")).strip()
    copy_from_profile_id = data.get("copy_from_profile_id")

    if copy_from_profile_id is not None:
        try:
            copy_from_profile_id = int(copy_from_profile_id)
        except (TypeError, ValueError):
            return jsonify({"error": "copy_from_profile_id must be an integer"}), 400

        source_profile = get_profile_for_user(user, copy_from_profile_id)
        if not source_profile:
            return jsonify({"error": "Source profile not found"}), 404
        profile = create_scoring_profile(
            owner_user_id=user["id"],
            name=name,
            description=source_profile.get("description", ""),
            weights=source_profile["weights"],
            industry_scores=source_profile["industry_scores"],
        )
    else:
        active_profile = get_active_scoring_profile(user)

        raw_weights = data.get("weights", active_profile["weights"])
        weights = validate_weights_payload(raw_weights)
        if weights is None:
            return jsonify({"error": "Invalid weights payload"}), 400

        raw_industry_scores = data.get("industry_scores", active_profile["industry_scores"])
        industry_scores = validate_industry_scores_payload(raw_industry_scores)
        if industry_scores is None:
            return jsonify({"error": "Invalid industry_scores payload"}), 400

        profile = create_scoring_profile(
            owner_user_id=user["id"],
            name=name,
            description=description,
            weights=weights,
            industry_scores=industry_scores,
        )

    session["active_scoring_profile_id"] = profile["id"]
    session["active_scoring_profile_manual"] = True
    return jsonify(sanitize(profile)), 201


@scoring_bp.route("/api/scoring-profiles/<int:profile_id>", methods=["GET"])
@login_required
def api_get_scoring_profile(profile_id):
    user = request.current_user
    profile = get_profile_for_user(user, profile_id)
    if not profile:
        return jsonify({"error": "Profile not found"}), 404
    if profile["can_edit"]:
        profile["shares"] = get_scoring_profile_shares(user["id"], profile_id)
    return jsonify(sanitize(profile))


@scoring_bp.route("/api/scoring-profiles/<int:profile_id>", methods=["PUT"])
@login_required
def api_update_scoring_profile(profile_id):
    user = request.current_user
    existing = get_profile_for_user(user, profile_id)
    if not existing:
        return jsonify({"error": "Profile not found"}), 404
    if not existing["can_edit"]:
        return jsonify({"error": "Read-only profile"}), 403

    data = _parse_json_payload()
    if data is None:
        return jsonify({"error": "Invalid JSON payload"}), 400

    name = str(data.get("name", existing["name"])).strip()
    if not name:
        return jsonify({"error": "Profile name is required"}), 400

    description = str(data.get("description", existing.get("description", ""))).strip()

    raw_weights = data.get("weights", existing["weights"])
    weights = validate_weights_payload(raw_weights)
    if weights is None:
        return jsonify({"error": "Invalid weights payload"}), 400

    raw_industry_scores = data.get("industry_scores", existing["industry_scores"])
    industry_scores = validate_industry_scores_payload(raw_industry_scores)
    if industry_scores is None:
        return jsonify({"error": "Invalid industry_scores payload"}), 400

    ok = update_scoring_profile(
        owner_user_id=user["id"],
        profile_id=profile_id,
        name=name,
        description=description,
        weights=weights,
        industry_scores=industry_scores,
    )
    if not ok:
        return jsonify({"error": "Failed to update profile"}), 500

    profile = get_profile_for_user(user, profile_id)
    profile["shares"] = get_scoring_profile_shares(user["id"], profile_id)
    return jsonify(sanitize(profile))


@scoring_bp.route("/api/scoring-profiles/<int:profile_id>", methods=["DELETE"])
@login_required
def api_delete_scoring_profile(profile_id):
    user = request.current_user
    profile = get_profile_for_user(user, profile_id)
    if not profile:
        return jsonify({"error": "Profile not found"}), 404
    if not profile["can_edit"]:
        return jsonify({"error": "Read-only profile"}), 403

    ok, reason = delete_scoring_profile(user["id"], profile_id)
    if not ok:
        if reason == "last_profile":
            return jsonify({"error": "You must keep at least one profile"}), 400
        if reason == "forbidden":
            return jsonify({"error": "Read-only profile"}), 403
        return jsonify({"error": "Failed to delete profile"}), 400

    if session.get("active_scoring_profile_id") == profile_id:
        session.pop("active_scoring_profile_id", None)
        session.pop("active_scoring_profile_manual", None)
    return jsonify({"status": "ok"})


@scoring_bp.route("/api/scoring-profiles/<int:profile_id>/select", methods=["POST"])
@login_required
def api_select_scoring_profile(profile_id):
    user = request.current_user
    profile = get_profile_for_user(user, profile_id)
    if not profile:
        return jsonify({"error": "Profile not found"}), 404
    session["active_scoring_profile_id"] = profile_id
    session["active_scoring_profile_manual"] = True
    return jsonify({"status": "ok", "active_profile_id": profile_id})


@scoring_bp.route("/api/scoring-profiles/<int:profile_id>/share", methods=["POST"])
@login_required
def api_share_scoring_profile(profile_id):
    user = request.current_user
    profile = get_profile_for_user(user, profile_id)
    if not profile:
        return jsonify({"error": "Profile not found"}), 404
    if not profile["can_edit"]:
        return jsonify({"error": "Read-only profile"}), 403

    data = _parse_json_payload()
    if data is None:
        return jsonify({"error": "Invalid JSON payload"}), 400

    email = str(data.get("email", "")).strip().lower()
    if not email:
        return jsonify({"error": "Target email is required"}), 400

    ok, reason = share_scoring_profile(user["id"], profile_id, email)
    if not ok:
        if reason == "user_not_found":
            return jsonify({"error": "User not found"}), 404
        if reason == "cannot_share_to_self":
            return jsonify({"error": "Cannot share to yourself"}), 400
        return jsonify({"error": "Failed to share profile"}), 400

    return jsonify({"status": "ok", "shares": get_scoring_profile_shares(user["id"], profile_id)})


@scoring_bp.route("/api/scoring-profiles/<int:profile_id>/share/<int:target_user_id>", methods=["DELETE"])
@login_required
def api_unshare_scoring_profile(profile_id, target_user_id):
    user = request.current_user
    profile = get_profile_for_user(user, profile_id)
    if not profile:
        return jsonify({"error": "Profile not found"}), 404
    if not profile["can_edit"]:
        return jsonify({"error": "Read-only profile"}), 403

    ok = unshare_scoring_profile(user["id"], profile_id, target_user_id)
    if not ok:
        return jsonify({"error": "Share not found"}), 404
    return jsonify({"status": "ok", "shares": get_scoring_profile_shares(user["id"], profile_id)})


@scoring_bp.route("/api/scoring-profiles/<int:profile_id>/set-default", methods=["POST"])
@login_required
@super_admin_api_required
def api_set_default_scoring_profile(profile_id):
    ok = set_default_scoring_profile(profile_id)
    if not ok:
        return jsonify({"error": "Profile not found"}), 404
    return jsonify({"status": "ok", "default_profile_id": profile_id})
