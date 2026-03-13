"""
Flask backend for PLT Territory Intelligence Dashboard.

This module is the app factory: creates the Flask app, loads config,
registers blueprints, loads account data, and initializes the database.
All route handlers live in the routes/ package.
"""

import os

import resend
from flask import Flask

from classifier import classify_account
from data_loader import load_xlsx
from database import (
    get_all_enrichments,
    get_default_scoring_profile,
    init_db,
    load_cached_accounts,
    save_cached_accounts,
)
from scoring import compute_score

import state
from routes import ALL_BLUEPRINTS


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def load_env_file(path: str):
    """Load KEY=VALUE entries from a local .env file into os.environ."""
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue

            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]

            os.environ.setdefault(key, value)


load_env_file(os.path.join(os.path.dirname(__file__), ".env"))


# ---------------------------------------------------------------------------
# App creation & config
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(32).hex())

resend.api_key = os.environ.get("RESEND_API_KEY", "")
FOOTER_CONTACT_EMAIL = os.environ.get("FOOTER_CONTACT_EMAIL", "rodolfo.ruiz@epiuse.com")


@app.context_processor
def inject_template_globals():
    return {"footer_contact_email": FOOTER_CONTACT_EMAIL}


# ---------------------------------------------------------------------------
# Blueprint registration
# ---------------------------------------------------------------------------

for bp in ALL_BLUEPRINTS:
    app.register_blueprint(bp)


# ---------------------------------------------------------------------------
# Account loading
# ---------------------------------------------------------------------------

def build_accounts():
    """Load xlsx, classify, and merge with DB enrichments."""
    df = load_xlsx()
    enrichments = get_all_enrichments()
    accounts = {}

    for _, row in df.iterrows():
        bp_id = int(row["bp_id"])
        enrichment = enrichments.get(bp_id, {})

        if enrichment.get("industry_override"):
            industry = enrichment["industry_override"]
            industry_source = "manual"
        else:
            industry, industry_source = classify_account(
                row["master_industry"],
                row["display_name"],
                row.get("sic_description", ""),
            )

        employee_count = enrichment.get("employee_count") or row.get("employee_count")
        revenue = enrichment.get("revenue") or row.get("revenue")

        website = enrichment.get("website", "") or row.get("xlsx_web_address", "")
        city = enrichment.get("city", "") or row.get("address_city", "")

        account = {
            "bp_id": bp_id,
            "company_name": row["display_name"],
            "original_name": row["company_name"],
            "planning_entity_name": row.get("planning_entity_name", ""),
            "planning_entity_id": row.get("planning_entity_id"),
            "region": row.get("region", "Jalisco"),
            "account_exec": row.get("account_exec", ""),
            "master_industry": row["master_industry"],
            "sic_description": row.get("sic_description", ""),
            "industry": industry,
            "industry_source": industry_source,
            "sap_status": row["sap_status"],
            "erp_isp_id": row.get("erp_isp_id"),
            "employee_count": employee_count,
            "revenue": revenue,
            "notes": enrichment.get("notes", ""),
            "starred": bool(enrichment.get("starred", 0)),
            "tags": enrichment.get("tags", []),
            "target_list": bool(enrichment.get("target_list", 0)),
            "website": website,
            "city": city,
            "base_instalada": row.get("base_instalada", ""),
            "tax_number": row.get("tax_number", ""),
            "top_parent_name": row.get("top_parent_name", ""),
            "address_street": row.get("address_street", ""),
            "address_region_code": row.get("address_region_code", ""),
            "address_postal_code": row.get("address_postal_code", ""),
            "account_owner_id": row.get("account_owner_id"),
            "market_segment": row.get("market_segment", ""),
            "bpr_products": row.get("bpr_products", ""),
            "archetype": row.get("archetype", ""),
            "rbc_plan": row.get("rbc_plan", ""),
            "master_code": row.get("master_code"),
        }
        accounts[bp_id] = account

    return accounts


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

def _init_app():
    init_db()

    cached = load_cached_accounts()
    if cached is not None:
        state.ACCOUNTS = cached
        enrichments = get_all_enrichments()
        for bp_id, account in state.ACCOUNTS.items():
            enrichment = enrichments.get(bp_id, {})
            if enrichment.get("industry_override"):
                account["industry"] = enrichment["industry_override"]
                account["industry_source"] = "manual"
            if enrichment.get("employee_count"):
                account["employee_count"] = enrichment["employee_count"]
            if enrichment.get("revenue"):
                account["revenue"] = enrichment["revenue"]
            if enrichment.get("website"):
                account["website"] = enrichment["website"]
            if enrichment.get("city"):
                account["city"] = enrichment["city"]
            account["notes"] = enrichment.get("notes", account.get("notes", ""))
            account["starred"] = bool(enrichment.get("starred", account.get("starred", 0)))
            account["tags"] = enrichment.get("tags", account.get("tags", []))
            account["target_list"] = bool(enrichment.get("target_list", account.get("target_list", 0)))
        print(f"Loaded {len(state.ACCOUNTS)} accounts from DB")
    else:
        try:
            state.ACCOUNTS = build_accounts()
            save_cached_accounts(state.ACCOUNTS)
            print(f"Imported {len(state.ACCOUNTS)} accounts from Excel into DB")
        except FileNotFoundError:
            raise RuntimeError(
                "No accounts in DB and no Excel file found. "
                "Place the Excel file in data/ for initial import."
            )

    default_profile = get_default_scoring_profile()
    if default_profile:
        tier_counts = {}
        for account in state.ACCOUNTS.values():
            scored = dict(account)
            score_result = compute_score(
                scored,
                weights=default_profile["weights"],
                industry_scores=default_profile["industry_scores"],
            )
            tier = score_result["tier"]
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
        print(f"Tiers (default profile): {tier_counts}")


_init_app()

if __name__ == "__main__":
    app.run(debug=True, port=5001)
