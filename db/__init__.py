"""
Database package — re-exports all public functions for backward compatibility.
"""

# Core
from db.core import get_db, init_db, _serialize_json, _parse_json, _row_to_dict, _UNSET

# Accounts
from db.accounts import (
    get_enrichment,
    get_all_enrichments,
    upsert_enrichment,
    get_all_tags,
    save_cached_accounts,
    load_cached_accounts,
)

# CRM
from db.crm import (
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

# Pipeline
from db.pipeline import (
    get_company_pipeline_statuses,
    upsert_company_pipeline_status,
    list_url_candidates_for_company,
    get_url_candidate_by_id,
    get_all_url_candidates,
    upsert_url_candidate,
    update_url_candidate,
    bulk_set_url_candidate_status_for_company,
)

# Scoring
from db.scoring import (
    get_scoring_weights,
    update_scoring_weights,
    get_default_scoring_profile_id,
    set_default_scoring_profile,
    get_scoring_profile_for_user,
    get_default_scoring_profile,
    get_scoring_profile_by_id,
    list_scoring_profiles_for_user,
    list_all_scoring_profiles,
    create_scoring_profile,
    update_scoring_profile,
    delete_scoring_profile,
    duplicate_scoring_profile,
    share_scoring_profile,
    unshare_scoring_profile,
    get_scoring_profile_shares,
)

# Users
from db.users import (
    create_or_get_user,
    get_user_by_email,
    get_all_users,
    update_user_role,
    delete_user,
    store_login_code,
    verify_login_code,
    generate_login_code,
)

# Data Dictionary
from db.data_dictionary import (
    get_all_dd_comments,
    upsert_dd_comment,
)
