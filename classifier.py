"""
Industry classifier: maps SAP master codes to our target industries,
and uses keyword matching on company names + SIC descriptions for refinement.
"""

import re

# Map SAP's 27 master codes to our target industry categories
MASTER_CODE_MAP = {
    "Higher Education and Research": "Higher Education",
    "Travel and Transportation": "Hotels/Hospitality",
    "Consumer Products": "Food & Beverage",
    "Agribusiness": "Food & Beverage",
    "Mill Products and Mining": "Mining",
    "Engineering, Construction, and Operation": "Real Estate/Construction",
    "Banking": "Financial Services",
    "Insurance": "Financial Services",
    "Automotive": "Manufacturing",
    "Industrial Manufacturing": "Manufacturing",
    "Healthcare": "Healthcare/Pharma",
    "Life Sciences": "Healthcare/Pharma",
    "High Tech": "Technology",
    "Telecommunications": "Technology",
    "Media": "Media/Entertainment",
    "Sports and Entertainment": "Media/Entertainment",
    "Retail": "Retail",
    "Wholesale Distribution": "Retail",
    "Oil, Gas, and Energy": "Energy/Utilities",
    "Utilities": "Energy/Utilities",
    "Chemicals": "Manufacturing",
    "Aerospace and Defense": "Manufacturing",
    "Professional Services": "Professional Services",
    "Public Sector": "Public Sector",
    "Postal": "Logistics",
    "Nonclassifiable Est.": "Unclassified",
    "SAP Consolidated companies": "Other",
}

# Keyword patterns for name/SIC-based classification (Spanish + English)
# Order matters: first match wins
KEYWORD_RULES = [
    (
        "Hotels/Hospitality",
        [
            r"\bhotel\b", r"\bresort\b", r"\binn\b", r"\bsuites?\b",
            r"\bhospitalidad\b", r"\bposada\b", r"\bmotel\b",
            r"\balojamiento\b", r"\bturismo\b", r"\bturístic",
        ],
    ),
    (
        "Higher Education",
        [
            r"\buniversidad\b", r"\biteso\b", r"\btecnológico\b",
            r"\bcolegio\b", r"\binstituto\b", r"\beducación\b",
            r"\bfundación\b", r"\bacademi", r"\bescuela\b",
            r"\buniversit", r"\beducativ",
        ],
    ),
    (
        "Food & Beverage",
        [
            r"\bembotelladora\b", r"\bbebidas?\b", r"\balimentos?\b",
            r"\bagroindustrial\b", r"\bcarne\b", r"\blácteo",
            r"\btequila\b", r"\bdestilad", r"\bbottle?r",
            r"\bfood\b", r"\bbeverage\b", r"\brestaurant",
            r"\bpanadería\b", r"\bgalleta\b", r"\bcereal\b",
        ],
    ),
    (
        "Mining",
        [
            r"\bminer[aío]\b", r"\bmining\b", r"\bmetales?\b",
            r"\bcantera\b", r"\bextracción\b",
        ],
    ),
    (
        "Real Estate/Construction",
        [
            r"\bconstructora\b", r"\binmobiliaria\b", r"\bdesarrollo\b",
            r"\bvivienda\b", r"\bconstru", r"\breal estate\b",
            r"\bedificacion", r"\bcement", r"\bconcret",
        ],
    ),
    (
        "Financial Services",
        [
            r"\bbanco\b", r"\bfinanciera\b", r"\bseguros?\b",
            r"\bcrédito\b", r"\basegurador", r"\bbank\b",
            r"\bfinanci", r"\bcaja\b",
        ],
    ),
    (
        "Manufacturing",
        [
            r"\bmanufactura\b", r"\bautomotriz\b", r"\bautopartes?\b",
            r"\bplástico\b", r"\bmetal\b", r"\bfábrica\b",
            r"\bindustria[ls]?\b", r"\bmaquinaria\b", r"\bacero\b",
            r"\bmanufact",
        ],
    ),
    (
        "Healthcare/Pharma",
        [
            r"\bfarmacéutica\b", r"\blaboratorio\b", r"\bmédic[ao]\b",
            r"\bsalud\b", r"\bhospital\b", r"\bclínica\b",
            r"\bpharma\b", r"\bhealth\b",
        ],
    ),
    (
        "Technology",
        [
            r"\btecnología\b", r"\bsoftware\b", r"\bsistemas?\b",
            r"\bdigital\b", r"\b[IT]{2}\b", r"\btech\b",
            r"\bcomput", r"\binformátic",
        ],
    ),
    (
        "Retail",
        [
            r"\bcomercio\b", r"\btienda\b", r"\bretail\b",
            r"\bsupermercado\b", r"\babarrotes\b", r"\balmacén\b",
        ],
    ),
]


def classify_by_master_code(master_code: str) -> str:
    """Map an SAP master code to our industry category."""
    if not master_code or master_code == "Unclassified":
        return "Unclassified"
    return MASTER_CODE_MAP.get(master_code, "Other")


def classify_by_keywords(company_name: str, sic_description: str = "") -> str | None:
    """
    Attempt to classify by keyword matching on name and SIC description.
    Returns the industry string or None if no match.
    """
    text = f"{company_name} {sic_description}".lower()
    for industry, patterns in KEYWORD_RULES:
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return industry
    return None


def classify_account(master_code: str, company_name: str, sic_description: str = "") -> tuple[str, str]:
    """
    Classify an account. Returns (industry, source) where source indicates
    how the classification was determined.

    Priority:
    1. SAP master code mapping (most reliable)
    2. Keyword matching on name + SIC (fallback)
    3. "Unclassified" if nothing matches
    """
    # First: use the master code
    from_master = classify_by_master_code(master_code)
    if from_master not in ("Unclassified", "Other"):
        return from_master, "master_code"

    # Second: try keyword matching
    from_keywords = classify_by_keywords(company_name, sic_description)
    if from_keywords:
        return from_keywords, "keyword"

    # If master code gave us "Other", that's still better than Unclassified
    if from_master == "Other":
        return "Other", "master_code"

    return "Unclassified", "none"


# All possible industry categories for dropdowns
ALL_INDUSTRIES = sorted(set(MASTER_CODE_MAP.values()) | {ind for ind, _ in KEYWORD_RULES} | {"Unclassified", "Other"})
