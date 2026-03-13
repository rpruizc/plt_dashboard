"""
CRM routes — /companies/<bp_id> page + all /api/companies/ CRUD.
"""

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from database import (
    list_company_contacts,
    create_company_contact,
    update_company_contact,
    delete_company_contact,
    list_company_touchpoints,
    create_company_touchpoint,
    update_company_touchpoint,
    delete_company_touchpoint,
    list_company_next_actions,
    create_company_next_action,
    update_company_next_action,
    delete_company_next_action,
)
from state import ACCOUNTS
from utils import (
    sanitize,
    normalize_text,
    normalize_optional_int,
    normalize_optional_date,
    normalize_optional_confidence,
)
from routes.auth import login_required

crm_bp = Blueprint("crm", __name__)

CRM_TOUCHPOINT_TYPES = ("call", "email", "meeting", "linkedin", "whatsapp", "event", "note")
CRM_ACTION_PRIORITIES = ("low", "medium", "high")
CRM_ACTION_STATUSES = ("open", "in_progress", "done")


def _parse_json_payload():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None
    return data


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _utc_today_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).date().isoformat()


def _contact_belongs_to_company(bp_id: int, contact_id: int) -> bool:
    return any(contact["id"] == contact_id for contact in list_company_contacts(bp_id))


# --- Company page ---


@crm_bp.route("/companies/<int:bp_id>")
@login_required
def company_page(bp_id):
    account = ACCOUNTS.get(bp_id)
    if not account:
        return redirect(url_for("accounts.index"))
    from routes.accounts import get_active_scoring_profile, score_account_with_profile
    profile = get_active_scoring_profile(request.current_user)
    scored = score_account_with_profile(account, profile)
    return render_template("company.html", current_user=request.current_user, account=scored)


# --- CRM API ---


@crm_bp.route("/api/companies/<int:bp_id>/crm")
@login_required
def api_company_crm(bp_id):
    account = ACCOUNTS.get(bp_id)
    if not account:
        return jsonify({"error": "Company not found"}), 404
    from routes.accounts import get_active_scoring_profile, score_account_with_profile
    profile = get_active_scoring_profile(request.current_user)
    scored = score_account_with_profile(account, profile)

    contacts = list_company_contacts(bp_id)
    touchpoints = list_company_touchpoints(bp_id)
    next_actions = list_company_next_actions(bp_id)

    open_actions = sum(1 for action in next_actions if action["status"] != "done")
    overdue_actions = 0
    today = _utc_today_iso()
    for action in next_actions:
        due = action.get("due_date")
        if action["status"] != "done" and due and due < today:
            overdue_actions += 1

    return jsonify(
        sanitize(
            {
                "company": scored,
                "contacts": contacts,
                "touchpoints": touchpoints,
                "next_actions": next_actions,
                "summary": {
                    "contact_count": len(contacts),
                    "touchpoint_count": len(touchpoints),
                    "open_actions_count": open_actions,
                    "overdue_actions_count": overdue_actions,
                },
            }
        )
    )


# --- Contacts ---


@crm_bp.route("/api/companies/<int:bp_id>/contacts", methods=["POST"])
@login_required
def api_create_company_contact(bp_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404

    data = _parse_json_payload()
    if data is None:
        return jsonify({"error": "Invalid JSON payload"}), 400

    full_name = normalize_text(data.get("full_name"), required=True, max_length=160)
    if full_name is None:
        return jsonify({"error": "full_name is required"}), 400

    job_title = normalize_text(data.get("job_title"), max_length=160)
    email = normalize_text(data.get("email"), max_length=200)
    phone = normalize_text(data.get("phone"), max_length=80)
    linkedin_url = normalize_text(data.get("linkedin_url"), max_length=300)
    source = normalize_text(data.get("source"), max_length=120)
    notes = normalize_text(data.get("notes"), max_length=5000)
    confidence = normalize_optional_confidence(data.get("confidence"))
    if data.get("confidence") not in (None, "") and confidence is None:
        return jsonify({"error": "confidence must be between 0 and 100"}), 400

    contact = create_company_contact(
        bp_id=bp_id,
        full_name=full_name,
        job_title=job_title or "",
        email=email or "",
        phone=phone or "",
        linkedin_url=linkedin_url or "",
        source=source or "",
        confidence=confidence,
        notes=notes or "",
    )
    return jsonify(sanitize(contact)), 201


@crm_bp.route("/api/companies/<int:bp_id>/contacts/<int:contact_id>", methods=["PUT"])
@login_required
def api_update_company_contact(bp_id, contact_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404
    data = _parse_json_payload()
    if data is None:
        return jsonify({"error": "Invalid JSON payload"}), 400

    fields = {}
    if "full_name" in data:
        value = normalize_text(data.get("full_name"), required=True, max_length=160)
        if value is None:
            return jsonify({"error": "full_name cannot be empty"}), 400
        fields["full_name"] = value
    if "job_title" in data:
        value = normalize_text(data.get("job_title"), max_length=160)
        if value is None:
            return jsonify({"error": "job_title must be text"}), 400
        fields["job_title"] = value
    if "email" in data:
        value = normalize_text(data.get("email"), max_length=200)
        if value is None:
            return jsonify({"error": "email must be text"}), 400
        fields["email"] = value
    if "phone" in data:
        value = normalize_text(data.get("phone"), max_length=80)
        if value is None:
            return jsonify({"error": "phone must be text"}), 400
        fields["phone"] = value
    if "linkedin_url" in data:
        value = normalize_text(data.get("linkedin_url"), max_length=300)
        if value is None:
            return jsonify({"error": "linkedin_url must be text"}), 400
        fields["linkedin_url"] = value
    if "source" in data:
        value = normalize_text(data.get("source"), max_length=120)
        if value is None:
            return jsonify({"error": "source must be text"}), 400
        fields["source"] = value
    if "notes" in data:
        value = normalize_text(data.get("notes"), max_length=5000)
        if value is None:
            return jsonify({"error": "notes must be text"}), 400
        fields["notes"] = value
    if "confidence" in data:
        confidence = normalize_optional_confidence(data.get("confidence"))
        if data.get("confidence") not in (None, "") and confidence is None:
            return jsonify({"error": "confidence must be between 0 and 100"}), 400
        fields["confidence"] = confidence

    if not fields:
        return jsonify({"error": "No valid fields provided"}), 400

    updated = update_company_contact(bp_id, contact_id, fields)
    if not updated:
        return jsonify({"error": "Contact not found"}), 404
    return jsonify(sanitize(updated))


@crm_bp.route("/api/companies/<int:bp_id>/contacts/<int:contact_id>", methods=["DELETE"])
@login_required
def api_delete_company_contact(bp_id, contact_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404
    ok = delete_company_contact(bp_id, contact_id)
    if not ok:
        return jsonify({"error": "Contact not found"}), 404
    return jsonify({"status": "ok"})


# --- Touchpoints ---


@crm_bp.route("/api/companies/<int:bp_id>/touchpoints", methods=["POST"])
@login_required
def api_create_company_touchpoint(bp_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404

    data = _parse_json_payload()
    if data is None:
        return jsonify({"error": "Invalid JSON payload"}), 400

    touchpoint_type = normalize_text(data.get("touchpoint_type"), required=True, max_length=40)
    touchpoint_type = touchpoint_type.lower() if isinstance(touchpoint_type, str) else touchpoint_type
    if touchpoint_type is None or touchpoint_type not in CRM_TOUCHPOINT_TYPES:
        return jsonify({"error": f"touchpoint_type must be one of: {', '.join(CRM_TOUCHPOINT_TYPES)}"}), 400

    touchpoint_date = normalize_optional_date(data.get("touchpoint_date"))
    if touchpoint_date is None:
        return jsonify({"error": "touchpoint_date must be YYYY-MM-DD"}), 400

    raw_contact_id = data.get("contact_id")
    contact_id = normalize_optional_int(raw_contact_id)
    if raw_contact_id not in (None, "") and contact_id is None:
        return jsonify({"error": "contact_id must be a positive integer"}), 400
    if contact_id and not _contact_belongs_to_company(bp_id, contact_id):
        return jsonify({"error": "contact_id does not belong to this company"}), 400

    summary = normalize_text(data.get("summary"), max_length=200)
    outcome = normalize_text(data.get("outcome"), max_length=200)
    notes = normalize_text(data.get("notes"), max_length=5000)

    touchpoint = create_company_touchpoint(
        bp_id=bp_id,
        touchpoint_date=touchpoint_date,
        touchpoint_type=touchpoint_type,
        contact_id=contact_id,
        summary=summary or "",
        outcome=outcome or "",
        notes=notes or "",
    )
    return jsonify(sanitize(touchpoint)), 201


@crm_bp.route("/api/companies/<int:bp_id>/touchpoints/<int:touchpoint_id>", methods=["PUT"])
@login_required
def api_update_company_touchpoint(bp_id, touchpoint_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404
    data = _parse_json_payload()
    if data is None:
        return jsonify({"error": "Invalid JSON payload"}), 400

    fields = {}
    if "touchpoint_type" in data:
        value = normalize_text(data.get("touchpoint_type"), required=True, max_length=40)
        value = value.lower() if isinstance(value, str) else value
        if value is None or value not in CRM_TOUCHPOINT_TYPES:
            return jsonify({"error": f"touchpoint_type must be one of: {', '.join(CRM_TOUCHPOINT_TYPES)}"}), 400
        fields["touchpoint_type"] = value
    if "touchpoint_date" in data:
        value = normalize_optional_date(data.get("touchpoint_date"))
        if value is None:
            return jsonify({"error": "touchpoint_date must be YYYY-MM-DD"}), 400
        fields["touchpoint_date"] = value
    if "contact_id" in data:
        raw_value = data.get("contact_id")
        value = normalize_optional_int(raw_value)
        if raw_value not in (None, "") and value is None:
            return jsonify({"error": "contact_id must be a positive integer"}), 400
        if value and not _contact_belongs_to_company(bp_id, value):
            return jsonify({"error": "contact_id does not belong to this company"}), 400
        fields["contact_id"] = value
    if "summary" in data:
        value = normalize_text(data.get("summary"), max_length=200)
        if value is None:
            return jsonify({"error": "summary must be text"}), 400
        fields["summary"] = value
    if "outcome" in data:
        value = normalize_text(data.get("outcome"), max_length=200)
        if value is None:
            return jsonify({"error": "outcome must be text"}), 400
        fields["outcome"] = value
    if "notes" in data:
        value = normalize_text(data.get("notes"), max_length=5000)
        if value is None:
            return jsonify({"error": "notes must be text"}), 400
        fields["notes"] = value

    if not fields:
        return jsonify({"error": "No valid fields provided"}), 400

    updated = update_company_touchpoint(bp_id, touchpoint_id, fields)
    if not updated:
        return jsonify({"error": "Touchpoint not found"}), 404
    return jsonify(sanitize(updated))


@crm_bp.route("/api/companies/<int:bp_id>/touchpoints/<int:touchpoint_id>", methods=["DELETE"])
@login_required
def api_delete_company_touchpoint(bp_id, touchpoint_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404
    ok = delete_company_touchpoint(bp_id, touchpoint_id)
    if not ok:
        return jsonify({"error": "Touchpoint not found"}), 404
    return jsonify({"status": "ok"})


# --- Next Actions ---


@crm_bp.route("/api/companies/<int:bp_id>/next-actions", methods=["POST"])
@login_required
def api_create_company_next_action(bp_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404

    data = _parse_json_payload()
    if data is None:
        return jsonify({"error": "Invalid JSON payload"}), 400

    title = normalize_text(data.get("title"), required=True, max_length=200)
    if title is None:
        return jsonify({"error": "title is required"}), 400

    details = normalize_text(data.get("details"), max_length=5000)
    due_date = normalize_optional_date(data.get("due_date"))
    if data.get("due_date") not in (None, "") and due_date is None:
        return jsonify({"error": "due_date must be YYYY-MM-DD"}), 400

    priority = normalize_text(data.get("priority", "medium"), required=True, max_length=20)
    priority = priority.lower() if isinstance(priority, str) else priority
    if priority is None or priority not in CRM_ACTION_PRIORITIES:
        return jsonify({"error": f"priority must be one of: {', '.join(CRM_ACTION_PRIORITIES)}"}), 400

    status = normalize_text(data.get("status", "open"), required=True, max_length=20)
    status = status.lower() if isinstance(status, str) else status
    if status is None or status not in CRM_ACTION_STATUSES:
        return jsonify({"error": f"status must be one of: {', '.join(CRM_ACTION_STATUSES)}"}), 400

    owner_email = normalize_text(data.get("owner_email"), max_length=200)
    raw_contact_id = data.get("contact_id")
    contact_id = normalize_optional_int(raw_contact_id)
    if raw_contact_id not in (None, "") and contact_id is None:
        return jsonify({"error": "contact_id must be a positive integer"}), 400
    if contact_id and not _contact_belongs_to_company(bp_id, contact_id):
        return jsonify({"error": "contact_id does not belong to this company"}), 400

    next_action = create_company_next_action(
        bp_id=bp_id,
        title=title,
        details=details or "",
        due_date=due_date,
        priority=priority,
        status=status,
        owner_email=owner_email or request.current_user["email"],
        contact_id=contact_id,
        completed_at=_utc_now_iso() if status == "done" else None,
    )
    return jsonify(sanitize(next_action)), 201


@crm_bp.route("/api/companies/<int:bp_id>/next-actions/<int:action_id>", methods=["PUT"])
@login_required
def api_update_company_next_action(bp_id, action_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404
    data = _parse_json_payload()
    if data is None:
        return jsonify({"error": "Invalid JSON payload"}), 400

    fields = {}
    if "title" in data:
        value = normalize_text(data.get("title"), required=True, max_length=200)
        if value is None:
            return jsonify({"error": "title cannot be empty"}), 400
        fields["title"] = value
    if "details" in data:
        value = normalize_text(data.get("details"), max_length=5000)
        if value is None:
            return jsonify({"error": "details must be text"}), 400
        fields["details"] = value
    if "due_date" in data:
        value = normalize_optional_date(data.get("due_date"))
        if data.get("due_date") not in (None, "") and value is None:
            return jsonify({"error": "due_date must be YYYY-MM-DD"}), 400
        fields["due_date"] = value
    if "priority" in data:
        value = normalize_text(data.get("priority"), required=True, max_length=20)
        value = value.lower() if isinstance(value, str) else value
        if value is None or value not in CRM_ACTION_PRIORITIES:
            return jsonify({"error": f"priority must be one of: {', '.join(CRM_ACTION_PRIORITIES)}"}), 400
        fields["priority"] = value
    if "status" in data:
        value = normalize_text(data.get("status"), required=True, max_length=20)
        value = value.lower() if isinstance(value, str) else value
        if value is None or value not in CRM_ACTION_STATUSES:
            return jsonify({"error": f"status must be one of: {', '.join(CRM_ACTION_STATUSES)}"}), 400
        fields["status"] = value
        fields["completed_at"] = _utc_now_iso() if value == "done" else None
    if "owner_email" in data:
        value = normalize_text(data.get("owner_email"), max_length=200)
        if value is None:
            return jsonify({"error": "owner_email must be text"}), 400
        fields["owner_email"] = value.lower()
    if "contact_id" in data:
        raw_value = data.get("contact_id")
        value = normalize_optional_int(raw_value)
        if raw_value not in (None, "") and value is None:
            return jsonify({"error": "contact_id must be a positive integer"}), 400
        if value and not _contact_belongs_to_company(bp_id, value):
            return jsonify({"error": "contact_id does not belong to this company"}), 400
        fields["contact_id"] = value

    if not fields:
        return jsonify({"error": "No valid fields provided"}), 400

    updated = update_company_next_action(bp_id, action_id, fields)
    if not updated:
        return jsonify({"error": "Next action not found"}), 404
    return jsonify(sanitize(updated))


@crm_bp.route("/api/companies/<int:bp_id>/next-actions/<int:action_id>", methods=["DELETE"])
@login_required
def api_delete_company_next_action(bp_id, action_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404
    ok = delete_company_next_action(bp_id, action_id)
    if not ok:
        return jsonify({"error": "Next action not found"}), 404
    return jsonify({"status": "ok"})
