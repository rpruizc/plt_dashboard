"""
Data loader: reads the PLT xlsx file, cleans and normalizes data.
The xlsx is read-only — all enrichments go to SQLite.

Supports the two-sheet format of PLT_Jalisco_2026_II.xlsx:
  Sheet1: original columns + BASE INSTALADA (installed SAP products)
  Sheet2: extended account data (address, revenue, employees, etc.)
Falls back to the single-sheet PLT_Jalisco_2026.xlsx if the new file is absent.
"""

import os
import re
import pandas as pd


DATA_DIR = os.environ.get("PLT_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
XLSX_PATH_V2 = os.path.join(DATA_DIR, "PLT_Jalisco_2026_II.xlsx")
XLSX_PATH_V1 = os.path.join(DATA_DIR, "PLT_Jalisco_2026.xlsx")

# Map raw column names to internal names — Sheet 1 (original + base instalada)
SHEET1_COLUMN_MAP = {
    "Business Partner ID": "bp_id",
    "ERP ISP ID": "erp_isp_id",
    "Organization Name1": "company_name",
    "Planning Entity": "planning_entity_id",
    "Planning Entity Name": "planning_entity_name",
    "Default Address Region Descr": "region",
    "Account Executive Name 2026": "account_exec",
    "Default Master Code Descr": "master_industry",
    "Default SIC Primary Descr": "sic_description",
    "BASE INSTALADA ": "base_instalada",
    "BASE INSTALADA": "base_instalada",
}

# Map raw column names to internal names — Sheet 2 (extended data)
SHEET2_COLUMN_MAP = {
    "Business Partner ID": "bp_id",
    "Organization Name1": "company_name_s2",
    "Planning Entity Name": "planning_entity_name_s2",
    "Account Main Tax Number": "tax_number",
    "SAP Top Parent Name": "top_parent_name",
    "Default Address Street": "address_street",
    "Default Address City": "address_city",
    "Default Address Region": "address_region_code",
    "Default Address Region Descr": "region_s2",
    "Default Address Postal Code": "address_postal_code",
    "Account Owner ID": "account_owner_id",
    "Account Executive Name 2026": "account_exec_s2",
    "Turnover US Dollar Value": "turnover_usd",
    "Num Employees Local Value": "num_employees",
    "Internal Market Segment IMS Desc": "market_segment",
    "Buying Product Relationship BPR Descr Concat": "bpr_products",
    "Archetype Descr Plan 2026": "archetype",
    "RBC 2026 PLAN": "rbc_plan",
    "Default Master Code": "master_code",
    "Default Master Code Descr": "master_industry_s2",
    "Default SIC Primary Descr": "sic_description_s2",
    "Account Web Address": "xlsx_web_address",
}


def clean_company_name(name: str) -> str:
    """Normalize a company name for display and matching."""
    if not name or not isinstance(name, str):
        return ""
    # Strip extra whitespace
    name = re.sub(r"\s+", " ", name.strip())
    # Title-case common all-caps names, but leave mixed case alone
    if name == name.upper() and len(name) > 3:
        # Smart title-case: preserve acronyms like "SA", "SA DE CV"
        words = name.split()
        result = []
        skip_words = {"SA", "DE", "CV", "RL", "AC", "SC", "AB", "SPR", "SAPI"}
        for w in words:
            if w in skip_words or len(w) <= 2:
                result.append(w)
            else:
                result.append(w.capitalize())
        name = " ".join(result)
    return name


def determine_sap_status(erp_isp_id) -> str:
    """
    Determine SAP relationship status from ERP ISP ID presence.
    If the field is populated, there's an existing SAP relationship.
    """
    if pd.isna(erp_isp_id) or erp_isp_id is None or str(erp_isp_id).strip() == "":
        return "Net New"
    return "Existing SAP"


def _safe_str(val) -> str:
    """Convert a value to string, returning empty string for NaN/None."""
    if pd.isna(val) or val is None:
        return ""
    return str(val).strip()


def _safe_int(val):
    """Convert to int or return None."""
    if pd.isna(val) or val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _safe_float(val):
    """Convert to float or return None."""
    if pd.isna(val) or val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _load_v2() -> pd.DataFrame:
    """Load the two-sheet PLT_Jalisco_2026_II.xlsx format."""
    # --- Sheet 1: original columns + BASE INSTALADA ---
    s1 = pd.read_excel(XLSX_PATH_V2, sheet_name="Sheet1", engine="openpyxl")
    s1 = s1.loc[:, s1.columns.notna()]
    unnamed = [c for c in s1.columns if str(c).startswith("Unnamed")]
    s1 = s1.drop(columns=unnamed, errors="ignore")
    s1 = s1.rename(columns=SHEET1_COLUMN_MAP)
    s1 = s1.dropna(subset=["bp_id"])
    s1["bp_id"] = s1["bp_id"].astype(int)

    # --- Sheet 2: extended data ---
    s2 = pd.read_excel(XLSX_PATH_V2, sheet_name="Sheet2", engine="openpyxl")
    s2 = s2.loc[:, s2.columns.notna()]
    unnamed = [c for c in s2.columns if str(c).startswith("Unnamed")]
    s2 = s2.drop(columns=unnamed, errors="ignore")
    s2 = s2.rename(columns=SHEET2_COLUMN_MAP)
    s2 = s2.dropna(subset=["bp_id"])
    s2["bp_id"] = s2["bp_id"].astype(int)

    # Merge: Sheet2 is the primary source; Sheet1 provides erp_isp_id + base_instalada
    s1_extra = s1[["bp_id", "erp_isp_id", "base_instalada", "planning_entity_id"]].drop_duplicates(subset=["bp_id"])
    df = s2.merge(s1_extra, on="bp_id", how="left")

    # Use Sheet2's company_name and planning_entity_name
    df["company_name"] = df["company_name_s2"]
    df["planning_entity_name"] = df["planning_entity_name_s2"]
    df["region"] = df["region_s2"]
    df["account_exec"] = df["account_exec_s2"]
    df["master_industry"] = df["master_industry_s2"]
    df["sic_description"] = df["sic_description_s2"]

    # Clean company names
    df["company_name"] = df["company_name"].apply(clean_company_name)
    df["planning_entity_name"] = df["planning_entity_name"].apply(clean_company_name)

    # Display name: prefer the longer of company_name vs planning_entity_name
    df["display_name"] = df.apply(
        lambda r: r["company_name"]
        if len(str(r["company_name"])) >= len(str(r["planning_entity_name"]))
        else r["planning_entity_name"],
        axis=1,
    )

    # SAP status from ERP ISP ID
    df["sap_status"] = df["erp_isp_id"].apply(determine_sap_status)

    # Fill defaults
    df["master_industry"] = df["master_industry"].fillna("Unclassified")
    df["sic_description"] = df["sic_description"].fillna("")
    df["region"] = df["region"].fillna("Jalisco")
    df["base_instalada"] = df["base_instalada"].fillna("")
    df["bpr_products"] = df["bpr_products"].fillna("")

    # Numeric fields from Sheet2
    df["employee_count"] = df["num_employees"].apply(_safe_int)
    df["revenue"] = df["turnover_usd"].apply(_safe_float)

    # String fields
    for col in ["tax_number", "top_parent_name", "address_street", "address_city",
                 "address_region_code", "market_segment", "archetype",
                 "rbc_plan", "xlsx_web_address"]:
        if col in df.columns:
            df[col] = df[col].apply(_safe_str)

    df["address_postal_code"] = df["address_postal_code"].apply(_safe_str)
    df["master_code"] = df["master_code"].apply(_safe_int)
    df["account_owner_id"] = df["account_owner_id"].apply(_safe_int)

    # Keep columns we need
    keep_cols = [
        "bp_id", "company_name", "display_name", "planning_entity_id",
        "planning_entity_name", "region", "account_exec",
        "master_industry", "sic_description", "sap_status",
        "erp_isp_id", "employee_count", "revenue",
        # New columns from V2
        "base_instalada", "tax_number", "top_parent_name",
        "address_street", "address_city", "address_region_code",
        "address_postal_code", "account_owner_id",
        "market_segment", "bpr_products", "archetype", "rbc_plan",
        "master_code", "xlsx_web_address",
    ]
    df = df[[c for c in keep_cols if c in df.columns]]
    df = df.reset_index(drop=True)
    return df


def _load_v1() -> pd.DataFrame:
    """Load the single-sheet PLT_Jalisco_2026.xlsx format (legacy)."""
    column_map = {
        "Business Partner ID": "bp_id",
        "ERP ISP ID": "erp_isp_id",
        "Organization Name1": "company_name",
        "Planning Entity": "planning_entity_id",
        "Planning Entity Name": "planning_entity_name",
        "Default Address Region Descr": "region",
        "Account Executive Name 2026": "account_exec",
        "Default Master Code Descr": "master_industry",
        "Default SIC Primary Descr": "sic_description",
    }

    df = pd.read_excel(XLSX_PATH_V1, engine="openpyxl")
    df = df.loc[:, df.columns.notna()]
    unnamed = [c for c in df.columns if str(c).startswith("Unnamed")]
    df = df.drop(columns=unnamed, errors="ignore")
    df = df.rename(columns=column_map)
    df = df.dropna(subset=["bp_id"])
    df["bp_id"] = df["bp_id"].astype(int)

    df["company_name"] = df["company_name"].apply(clean_company_name)
    df["planning_entity_name"] = df["planning_entity_name"].apply(clean_company_name)

    df["display_name"] = df.apply(
        lambda r: r["company_name"]
        if len(str(r["company_name"])) >= len(str(r["planning_entity_name"]))
        else r["planning_entity_name"],
        axis=1,
    )

    df["sap_status"] = df["erp_isp_id"].apply(determine_sap_status)
    df["master_industry"] = df["master_industry"].fillna("Unclassified")
    df["sic_description"] = df["sic_description"].fillna("")
    df["region"] = df["region"].fillna("Jalisco")
    df["employee_count"] = None
    df["revenue"] = None

    keep_cols = [
        "bp_id", "company_name", "display_name", "planning_entity_id",
        "planning_entity_name", "region", "account_exec",
        "master_industry", "sic_description", "sap_status",
        "erp_isp_id", "employee_count", "revenue",
    ]
    df = df[[c for c in keep_cols if c in df.columns]]
    df = df.reset_index(drop=True)
    return df


def load_xlsx() -> pd.DataFrame:
    """Load and normalize the PLT xlsx file. Returns a clean DataFrame.

    Raises FileNotFoundError if neither xlsx file is present.
    """
    if os.path.exists(XLSX_PATH_V2):
        return _load_v2()
    if os.path.exists(XLSX_PATH_V1):
        return _load_v1()
    raise FileNotFoundError(
        f"No Excel file found at {XLSX_PATH_V2} or {XLSX_PATH_V1}"
    )


if __name__ == "__main__":
    data = load_xlsx()
    print(f"Loaded {len(data)} accounts")
    print(f"Columns: {list(data.columns)}")
    print(f"\nIndustry distribution:")
    print(data["master_industry"].value_counts().to_string())
    print(f"\nSAP status distribution:")
    print(data["sap_status"].value_counts().to_string())
    if "rbc_plan" in data.columns:
        print(f"\nRBC Plan distribution:")
        print(data["rbc_plan"].value_counts().to_string())
    if "archetype" in data.columns:
        print(f"\nArchetype distribution:")
        print(data["archetype"].value_counts().to_string())
