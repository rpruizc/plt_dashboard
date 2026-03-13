"""
Export routes — /api/export/csv, /api/export/presentation.
"""

import csv
import io

from flask import Blueprint, Response, jsonify, request

from routes.auth import login_required
from routes.accounts import get_active_scoring_profile, get_scored_accounts
from utils import sanitize

export_bp = Blueprint("export", __name__)


@export_bp.route("/api/export/csv")
@login_required
def export_csv():
    """Export all accounts (or target list) as CSV."""
    target_only = request.args.get("target_only", "false") == "true"
    profile = get_active_scoring_profile(request.current_user)
    accounts = sorted(get_scored_accounts(profile), key=lambda a: a["score"], reverse=True)
    if target_only:
        accounts = [a for a in accounts if a["target_list"] or a["starred"]]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Rank",
            "BP ID",
            "Company Name",
            "Top Parent",
            "Industry",
            "Score",
            "Tier",
            "SAP Status",
            "RBC 2026 Plan",
            "Archetype 2026",
            "Market Segment",
            "SIC Description",
            "Master Industry",
            "Master Code",
            "Region",
            "City",
            "Street",
            "Postal Code",
            "Employee Count",
            "Revenue (USD)",
            "Website",
            "Tax Number",
            "Base Instalada",
            "BPR Products",
            "Account Exec",
            "Notes",
            "Tags",
            "Starred",
            "Target List",
        ]
    )
    for i, a in enumerate(accounts):
        writer.writerow(
            [
                i + 1,
                a["bp_id"],
                a["company_name"],
                a.get("top_parent_name", ""),
                a["industry"],
                a["score"],
                a["tier"],
                a["sap_status"],
                a.get("rbc_plan", ""),
                a.get("archetype", ""),
                a.get("market_segment", ""),
                a["sic_description"],
                a["master_industry"],
                a.get("master_code", ""),
                a["region"],
                a.get("city", ""),
                a.get("address_street", ""),
                a.get("address_postal_code", ""),
                a.get("employee_count", ""),
                a.get("revenue", ""),
                a.get("website", ""),
                a.get("tax_number", ""),
                a.get("base_instalada", ""),
                a.get("bpr_products", ""),
                a.get("account_exec", ""),
                a["notes"],
                "; ".join(a["tags"]),
                a["starred"],
                a["target_list"],
            ]
        )

    output.seek(0)
    filename = "target_accounts.csv" if target_only else "all_accounts.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@export_bp.route("/api/export/presentation")
@login_required
def export_presentation():
    """Export top N accounts as a clean presentation-ready JSON."""
    n = int(request.args.get("n", 30))
    target_only = request.args.get("target_only", "false") == "true"

    profile = get_active_scoring_profile(request.current_user)
    accounts = sorted(get_scored_accounts(profile), key=lambda a: a["score"], reverse=True)
    if target_only:
        accounts = [a for a in accounts if a["target_list"] or a["starred"]]

    accounts = accounts[:n]
    presentation = []
    for i, a in enumerate(accounts):
        presentation.append(
            {
                "rank": i + 1,
                "company": a["company_name"],
                "industry": a["industry"],
                "score": a["score"],
                "tier": a["tier"],
                "sap_status": a["sap_status"],
                "sic_description": a["sic_description"],
                "notes": a["notes"],
            }
        )
    return jsonify(presentation)
