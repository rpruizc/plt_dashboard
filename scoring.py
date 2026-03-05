"""
Scoring engine: computes a composite 0-100 score for each account
based on configurable weighted criteria.
"""

# Default weights (must sum to 1.0)
DEFAULT_WEIGHTS = {
    "industry_match": 0.40,
    "company_size": 0.25,
    "sap_relationship": 0.20,
    "data_completeness": 0.15,
}

# Industry score: how well does this industry match our SAP cloud sweet spot?
INDUSTRY_SCORES = {
    "Hotels/Hospitality": 100,
    "Higher Education": 95,
    "Food & Beverage": 90,
    "Mining": 85,
    "Real Estate/Construction": 85,
    "Manufacturing": 70,
    "Financial Services": 65,
    "Healthcare/Pharma": 60,
    "Technology": 55,
    "Retail": 50,
    "Energy/Utilities": 50,
    "Media/Entertainment": 45,
    "Professional Services": 40,
    "Logistics": 40,
    "Public Sector": 35,
    "Other": 30,
    "Unclassified": 20,
}

# SAP relationship scoring
SAP_STATUS_SCORES = {
    "Existing SAP": 90,   # Upsell/cross-sell opportunity
    "Net New": 80,        # Greenfield GROW opportunity
    "Has Business One": 40,  # Harder conversion
}


def score_industry(industry: str, industry_scores: dict | None = None) -> int:
    """Score an account based on its industry classification."""
    scores = industry_scores or INDUSTRY_SCORES
    return scores.get(industry, 30)


def score_company_size(employee_count, revenue=None) -> int:
    """
    Score based on company size. Sweet spot is 200-1000 employees.
    Since we don't have size data yet, return a neutral 50.
    """
    if employee_count is None and revenue is None:
        return 50  # Neutral — unknown size

    if employee_count is not None:
        try:
            emp = int(employee_count)
        except (ValueError, TypeError):
            return 50

        if emp < 50:
            return 30
        elif emp < 200:
            return 60
        elif emp <= 1000:
            return 100  # Sweet spot
        elif emp <= 1500:
            return 80
        else:
            return 40  # Too large for our PLT focus

    return 50


def score_sap_relationship(sap_status: str) -> int:
    """Score based on existing SAP relationship."""
    return SAP_STATUS_SCORES.get(sap_status, 50)


def score_data_completeness(account: dict) -> int:
    """
    Score based on how many useful fields are populated.
    More data = more actionable account.
    """
    fields_to_check = [
        "company_name", "master_industry", "sic_description",
        "region", "erp_isp_id", "employee_count", "revenue",
        "planning_entity_name", "website", "city",
    ]

    populated = 0
    for field in fields_to_check:
        val = account.get(field)
        if val is not None and str(val).strip() != "" and str(val) != "nan":
            populated += 1

    return int((populated / len(fields_to_check)) * 100)


def compute_score(account: dict, weights: dict | None = None, industry_scores: dict | None = None) -> dict:
    """
    Compute the composite score for an account.
    Returns a dict with the total score and breakdown.
    """
    w = weights or DEFAULT_WEIGHTS

    industry = account.get("industry", account.get("master_industry", "Unclassified"))
    sap_status = account.get("sap_status", "Net New")
    employee_count = account.get("employee_count")
    revenue = account.get("revenue")

    ind_score = score_industry(industry, industry_scores=industry_scores)
    size_score = score_company_size(employee_count, revenue)
    sap_score = score_sap_relationship(sap_status)
    completeness_score = score_data_completeness(account)

    composite = (
        ind_score * w["industry_match"]
        + size_score * w["company_size"]
        + sap_score * w["sap_relationship"]
        + completeness_score * w["data_completeness"]
    )

    return {
        "composite": round(composite, 1),
        "tier": (
            "A"
            if composite >= 100
            else ("B" if composite >= 80 else ("C" if composite >= 60 else ("D" if composite >= 40 else "E")))
        ),
        "breakdown": {
            "industry_match": {"score": ind_score, "weight": w["industry_match"], "weighted": round(ind_score * w["industry_match"], 1)},
            "company_size": {"score": size_score, "weight": w["company_size"], "weighted": round(size_score * w["company_size"], 1)},
            "sap_relationship": {"score": sap_score, "weight": w["sap_relationship"], "weighted": round(sap_score * w["sap_relationship"], 1)},
            "data_completeness": {"score": completeness_score, "weight": w["data_completeness"], "weighted": round(completeness_score * w["data_completeness"], 1)},
        },
    }
