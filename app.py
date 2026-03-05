"""
Flask backend for PLT Territory Intelligence Dashboard.
"""

import csv
import io
import json
import math
import os
import re
import threading
import unicodedata
from urllib.parse import urlencode, quote_plus
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from functools import wraps
from datetime import datetime, timezone
from uuid import uuid4

import resend
from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for

from classifier import ALL_INDUSTRIES, classify_account
from data_loader import load_xlsx
from database import (
    create_or_get_user,
    create_scoring_profile,
    create_company_contact,
    create_company_next_action,
    create_company_touchpoint,
    delete_scoring_profile,
    bulk_set_url_candidate_status_for_company,
    delete_company_contact,
    delete_company_next_action,
    delete_company_touchpoint,
    delete_user,
    generate_login_code,
    get_all_enrichments,
    get_all_tags,
    get_all_users,
    get_all_url_candidates,
    get_company_pipeline_statuses,
    get_default_scoring_profile,
    get_default_scoring_profile_id,
    get_scoring_profile_by_id,
    get_scoring_profile_for_user,
    get_scoring_profile_shares,
    get_user_by_email,
    get_url_candidate_by_id,
    init_db,
    list_company_contacts,
    list_company_next_actions,
    list_company_touchpoints,
    list_url_candidates_for_company,
    list_all_scoring_profiles,
    list_scoring_profiles_for_user,
    set_default_scoring_profile,
    share_scoring_profile,
    store_login_code,
    unshare_scoring_profile,
    update_url_candidate,
    upsert_company_pipeline_status,
    upsert_url_candidate,
    update_company_contact,
    update_company_next_action,
    update_company_touchpoint,
    update_scoring_profile,
    update_user_role,
    upsert_enrichment,
    get_all_dd_comments,
    load_cached_accounts,
    save_cached_accounts,
    upsert_dd_comment,
    verify_login_code,
)
from scoring import DEFAULT_WEIGHTS, compute_score


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

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(32).hex())

resend.api_key = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "PLT Dashboard <onboarding@resend.dev>")
FOOTER_CONTACT_EMAIL = os.environ.get("FOOTER_CONTACT_EMAIL", "rodolfo.ruiz@epiuse.com")

# Global account data loaded once. Scoring is computed per request from active profile.
ACCOUNTS = {}


@app.context_processor
def inject_template_globals():
    return {"footer_contact_email": FOOTER_CONTACT_EMAIL}


def sanitize(obj):
    """Replace NaN/Infinity with None so JSON serialization works."""
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def parse_json_payload():
    """Parse JSON payload and return a dict (or None on invalid input)."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None
    return data


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def validate_weights_payload(data: dict) -> dict | None:
    """Validate scoring weights payload and return normalized float values."""
    required = tuple(DEFAULT_WEIGHTS.keys())
    if set(data.keys()) != set(required):
        return None

    weights = {}
    for key in required:
        value = data.get(key)
        if not isinstance(value, (int, float)):
            return None
        value = float(value)
        if value < 0 or value > 1:
            return None
        weights[key] = value

    if abs(sum(weights.values()) - 1.0) > 0.001:
        return None

    return weights


def validate_industry_scores_payload(data: dict) -> dict | None:
    """Validate industry score map and return normalized float values."""
    if not isinstance(data, dict):
        return None

    required = set(ALL_INDUSTRIES)
    if set(data.keys()) != required:
        return None

    scores = {}
    for industry in ALL_INDUSTRIES:
        value = data.get(industry)
        if not isinstance(value, (int, float)):
            return None
        value = float(value)
        if value < 0 or value > 100:
            return None
        scores[industry] = round(value, 1)
    return scores


CRM_TOUCHPOINT_TYPES = ("call", "email", "meeting", "linkedin", "whatsapp", "event", "note")
CRM_ACTION_PRIORITIES = ("low", "medium", "high")
CRM_ACTION_STATUSES = ("open", "in_progress", "done")
URL_STAGE_STATUSES = ("not_started", "pending_review", "accepted", "no_url")
URL_SURE_CONFIDENCE_THRESHOLD = 80.0
URL_DOMAIN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$")
COMPANY_STOP_WORDS = {
    "sa",
    "de",
    "cv",
    "rl",
    "ac",
    "sc",
    "sapi",
    "sab",
    "spr",
    "the",
    "and",
    "grupo",
    "company",
    "co",
    "corp",
    "corporation",
    "inc",
    "llc",
    "ltd",
    "holding",
    "holdings",
    "services",
    "servicios",
    "mexico",
    "mx",
}
URL_VALIDATION_JOB_STATUSES = ("idle", "running", "completed", "failed", "cancelled")
URL_VALIDATION_DEFAULT_MODEL = os.environ.get("OPENAI_URL_VALIDATION_MODEL", "gpt-5-nano")
URL_VALIDATION_MODEL_DEFAULT_PRICING = {
    "gpt-5-nano": {"input_per_1m": 0.05, "output_per_1m": 0.40},
    "gpt-5-mini": {"input_per_1m": 0.25, "output_per_1m": 2.00},
}
URL_VALIDATION_JOBS: dict[str, dict] = {}
URL_VALIDATION_JOB_LOCK = threading.Lock()
URL_VALIDATION_ACTIVE_JOB_ID: str | None = None
BRAVE_SEARCH_API_KEY = os.environ.get("BRAVE_API_KEY", "").strip()
BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
BRAVE_SEARCH_TIMEOUT_SECONDS = float(os.environ.get("BRAVE_SEARCH_TIMEOUT_SECONDS", "8"))
BRAVE_SEARCH_MAX_RESULTS = int(os.environ.get("BRAVE_SEARCH_MAX_RESULTS", "8"))
BRAVE_SEARCH_LOCALES = os.environ.get("BRAVE_SEARCH_LOCALES", "MX:es,US:en")
OPENAI_URL_DISCOVERY_MODEL = os.environ.get("OPENAI_URL_DISCOVERY_MODEL", "gpt-5-nano")
URL_DISCOVERY_REVIEW_DEFAULT_MODEL = os.environ.get("OPENAI_URL_DISCOVERY_REVIEW_MODEL", OPENAI_URL_DISCOVERY_MODEL)
URL_DISCOVERY_DEEP_RESULT_LIMIT = int(os.environ.get("URL_DISCOVERY_DEEP_RESULT_LIMIT", "4"))
URL_DISCOVERY_DEEP_FALLBACK_DEFAULT = (
    os.environ.get("URL_DISCOVERY_DEEP_FALLBACK_DEFAULT", "1").strip().lower() in ("1", "true", "yes", "on")
)
URL_DISCOVERY_JOB_STATUSES = ("idle", "running", "completed", "failed", "cancelled")
URL_DISCOVERY_JOBS: dict[str, dict] = {}
URL_DISCOVERY_JOB_LOCK = threading.Lock()
URL_DISCOVERY_ACTIVE_JOB_ID: str | None = None
URL_DISCOVERY_AGGREGATOR_DOMAINS = {
    "dnb.com",
    "www.dnb.com",
    "linkedin.com",
    "www.linkedin.com",
    "facebook.com",
    "www.facebook.com",
    "instagram.com",
    "www.instagram.com",
    "x.com",
    "twitter.com",
    "www.twitter.com",
    "wikipedia.org",
    "www.wikipedia.org",
    "bloomberg.com",
    "www.bloomberg.com",
    "crunchbase.com",
    "www.crunchbase.com",
    "glassdoor.com",
    "www.glassdoor.com",
}


def normalize_text(value, *, required=False, max_length=5000) -> str | None:
    if value is None:
        if required:
            return None
        return ""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if required and not normalized:
        return None
    return normalized[:max_length]


def normalize_optional_int(value) -> int | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def normalize_optional_date(value) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        return datetime.strptime(candidate, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def normalize_optional_confidence(value) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if confidence < 0 or confidence > 100:
        return None
    return round(confidence, 1)


def contact_belongs_to_company(bp_id: int, contact_id: int) -> bool:
    return any(contact["id"] == contact_id for contact in list_company_contacts(bp_id))


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_website_url(value, *, allow_blank=False) -> str | None:
    if value in (None, ""):
        return "" if allow_blank else None
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return "" if allow_blank else None
    if "://" not in cleaned:
        cleaned = f"https://{cleaned}"
    cleaned = re.sub(r"\s+", "", cleaned)

    domain = cleaned
    if domain.startswith("https://"):
        domain = domain[8:]
    elif domain.startswith("http://"):
        domain = domain[7:]
    domain = domain.split("/", 1)[0].strip().lower()

    if not URL_DOMAIN_PATTERN.match(domain):
        return None

    path = cleaned.split("://", 1)[1]
    if "/" in path:
        path = "/" + path.split("/", 1)[1].strip("/")
        if path == "/":
            path = ""
    else:
        path = ""
    return f"https://{domain}{path}".rstrip("/")


def tokenize_company_name_for_domain(name: str) -> list[str]:
    if not isinstance(name, str):
        return []
    lowered = _strip_accents(name).lower()
    lowered = lowered.replace("&", " and ")
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    tokens = []
    for token in lowered.split():
        if len(token) < 3:
            continue
        if token in COMPANY_STOP_WORDS:
            continue
        if token.isdigit():
            continue
        tokens.append(token)
    return tokens


def domain_from_url(url: str) -> str:
    if not isinstance(url, str):
        return ""
    cleaned = url.strip().lower()
    if cleaned.startswith("https://"):
        cleaned = cleaned[8:]
    elif cleaned.startswith("http://"):
        cleaned = cleaned[7:]
    return cleaned.split("/", 1)[0].strip()


def looks_like_aggregator_domain(domain: str) -> bool:
    if not domain:
        return False
    return domain in URL_DISCOVERY_AGGREGATOR_DOMAINS


def parse_brave_locale_pairs() -> list[tuple[str, str]]:
    pairs = []
    raw = BRAVE_SEARCH_LOCALES.strip()
    if not raw:
        return [("MX", "es"), ("US", "en")]
    for chunk in raw.split(","):
        part = chunk.strip()
        if not part:
            continue
        if ":" in part:
            country, lang = part.split(":", 1)
            country = country.strip().upper()
            lang = lang.strip().lower()
        else:
            country = part.strip().upper()
            lang = "en"
        if country:
            pairs.append((country, lang or "en"))
    return pairs or [("MX", "es"), ("US", "en")]


def generate_heuristic_url_candidates(account: dict) -> list[dict]:
    generated = []
    seen_urls = set()
    name_fields = [
        ("display_name", account.get("company_name", "")),
        ("legal_name", account.get("original_name", "")),
        ("planning_entity", account.get("planning_entity_name", "")),
    ]

    for name_rank, (label, name_value) in enumerate(name_fields):
        tokens = tokenize_company_name_for_domain(name_value)
        if not tokens:
            continue

        bases = []
        bases.append("".join(tokens[:2]))
        bases.append("-".join(tokens[:2]))
        if len(tokens) >= 3:
            bases.append("".join(tokens[:3]))
            bases.append("-".join(tokens[:3]))
        bases.append(tokens[0])

        unique_bases = []
        for base in bases:
            compact = re.sub(r"[^a-z0-9-]", "", base).strip("-")
            if len(compact) < 3:
                continue
            if compact in unique_bases:
                continue
            unique_bases.append(compact)

        for base_rank, base in enumerate(unique_bases[:3]):
            for tld_rank, tld in enumerate((".com.mx", ".mx", ".com")):
                candidate_url = f"https://{base}{tld}"
                if candidate_url in seen_urls:
                    continue
                seen_urls.add(candidate_url)
                score = max(10.0, 62.0 - (name_rank * 10) - (base_rank * 7) - (tld_rank * 3))
                confidence = max(12.0, min(72.0, score))
                generated.append(
                    {
                        "candidate_url": candidate_url,
                        "score": round(score, 1),
                        "confidence": round(confidence, 1),
                        "status": "pending",
                        "source": "heuristic_pass",
                        "reasons": [
                            f"name_source:{label}",
                            f"base:{base}",
                            f"tld:{tld}",
                        ],
                    }
                )

    generated.sort(key=lambda item: (item["confidence"], item["score"]), reverse=True)
    return generated[:6]


def build_brave_queries_for_account(account: dict) -> list[str]:
    def to_search_friendly_name(raw_name: str) -> str:
        cleaned = _strip_accents(raw_name or "").lower()
        cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
        parts = []
        legal_suffixes = {"sa", "de", "cv", "s", "a", "rl", "ac", "sc", "sapi", "sab"}
        for token in cleaned.split():
            if token in legal_suffixes:
                continue
            if len(token) < 2:
                continue
            parts.append(token)
        if not parts:
            return ""
        return " ".join(parts[:4]).strip()

    name_candidates = []
    for field in ("company_name", "original_name", "planning_entity_name"):
        value = normalize_text(account.get(field), max_length=180)
        friendly = to_search_friendly_name(value or "")
        if friendly and friendly not in name_candidates:
            name_candidates.append(friendly)
    if not name_candidates:
        return []

    primary_name = name_candidates[0]
    industry = normalize_text(account.get("industry"), max_length=120) or ""

    queries = [
        f"\"{primary_name}\" official website",
        f"\"{primary_name}\" sitio oficial mexico",
        f"{primary_name} dnb.com company profile",
    ]
    if industry and industry.lower() not in ("unclassified", "other"):
        queries.append(f"{primary_name} {industry} mexico")
    if len(name_candidates) > 1:
        queries.append(f"{name_candidates[1]} official website")

    unique_queries = []
    for query in queries:
        normalized = query.strip()
        if normalized and normalized not in unique_queries:
            unique_queries.append(normalized)
    return unique_queries[:4]


def build_step2_brave_query(account: dict) -> str:
    def compact_terms(value: str, *, max_terms: int) -> str:
        cleaned = _strip_accents(value or "").lower()
        cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned).strip()
        terms = []
        for token in cleaned.split():
            if len(token) < 2:
                continue
            if token in COMPANY_STOP_WORDS:
                continue
            terms.append(token)
        return " ".join(terms[:max_terms]).strip()

    raw_name = (
        normalize_text(account.get("company_name"), max_length=180)
        or normalize_text(account.get("original_name"), max_length=180)
        or normalize_text(account.get("planning_entity_name"), max_length=180)
        or ""
    )
    raw_market = normalize_text(account.get("master_industry"), max_length=120) or ""
    raw_products = normalize_text(account.get("sic_description"), max_length=220) or ""

    name = compact_terms(raw_name, max_terms=6)
    market = compact_terms(raw_market, max_terms=5)
    products = compact_terms(raw_products, max_terms=8)
    if not name:
        return ""

    query_parts = [f"\"{name}\""]
    if market and market not in ("unclassified", "other"):
        query_parts.append(market)
    if products:
        query_parts.append(products)
    query_parts.append("official website")
    return " ".join(part for part in query_parts if part).strip()


def run_brave_web_search(query: str, max_results: int = BRAVE_SEARCH_MAX_RESULTS) -> list[dict]:
    if not BRAVE_SEARCH_API_KEY:
        return []
    locale_pairs = parse_brave_locale_pairs()
    deduped = []
    seen_urls = set()

    for country, lang in locale_pairs:
        params = urlencode(
            {
                "q": query,
                "count": max(1, min(max_results, 20)),
                "search_lang": lang,
                "country": country,
                "safesearch": "moderate",
            },
            quote_via=quote_plus,
        )
        url = f"{BRAVE_SEARCH_ENDPOINT}?{params}"
        req = Request(
            url,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
                "User-Agent": "PLT-URL-Pipeline/1.0",
            },
        )
        try:
            with urlopen(req, timeout=BRAVE_SEARCH_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 429:
                continue
            continue
        except (URLError, TimeoutError, ValueError):
            continue

        web_payload = payload.get("web", {})
        results = web_payload.get("results", [])
        if not isinstance(results, list):
            continue
        for result in results:
            url_val = result.get("url")
            if not isinstance(url_val, str):
                continue
            key = url_val.strip().lower()
            if not key or key in seen_urls:
                continue
            seen_urls.add(key)
            item = dict(result)
            item["_locale_country"] = country
            item["_locale_lang"] = lang
            deduped.append(item)
            if len(deduped) >= max_results:
                return deduped

    return deduped


def llm_review_brave_result_for_company(
    account: dict,
    *,
    query: str,
    result: dict,
    model: str,
) -> tuple[dict, int, int]:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("OpenAI SDK is not installed. Run `uv sync`.") from exc

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for Step 2 LLM review.")

    result_url = normalize_website_url(result.get("url", ""))
    payload = {
        "company": {
            "bp_id": account.get("bp_id"),
            "name": account.get("company_name", ""),
            "original_name": account.get("original_name", ""),
            "planning_entity_name": account.get("planning_entity_name", ""),
            "market": account.get("master_industry", ""),
            "products": account.get("sic_description", ""),
            "region": account.get("region", ""),
        },
        "query": query,
        "search_result": {
            "url": result_url or "",
            "title": normalize_text(result.get("title"), max_length=220) or "",
            "description": normalize_text(result.get("description"), max_length=320) or "",
        },
    }

    system_msg = (
        "You are validating whether a Brave search result corresponds to the official company website.\n"
        "Return JSON only with keys: decision, confidence, official_website_url, reason, reason_codes.\n"
        "decision must be one of accept, review, reject.\n"
        "confidence is 0-100.\n"
        "official_website_url should be the company's official URL when identifiable, else empty string.\n"
        "If the result is an aggregator/listing profile, use review/reject unless an official URL is explicit."
    )

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
        ],
    )

    parsed = parse_json_object_from_text(extract_response_text(response)) or {}
    decision = str(parsed.get("decision", "review")).strip().lower()
    if decision not in ("accept", "review", "reject"):
        decision = "review"

    try:
        confidence = float(parsed.get("confidence", 50))
    except (TypeError, ValueError):
        confidence = 50.0
    confidence = max(0.0, min(100.0, confidence))

    official_url = normalize_website_url(str(parsed.get("official_website_url", "")).strip())
    if not official_url:
        official_url = result_url or ""

    reason = normalize_text(parsed.get("reason"), max_length=260) or ""
    reason_codes = parsed.get("reason_codes")
    if not isinstance(reason_codes, list):
        reason_codes = []

    normalized_codes = []
    for code in reason_codes[:6]:
        if not isinstance(code, str):
            continue
        cleaned = re.sub(r"[^a-z0-9_]", "", code.lower())[:40]
        if cleaned:
            normalized_codes.append(cleaned)

    output = {
        "decision": decision,
        "confidence": round(confidence, 1),
        "official_website_url": official_url or "",
        "reason": reason,
        "reason_codes": normalized_codes,
    }
    input_tokens, output_tokens = extract_usage_tokens(response)
    return output, input_tokens, output_tokens


def score_brave_candidate(
    normalized_url: str,
    *,
    result_rank: int,
    query_rank: int,
    account_tokens: set[str],
) -> tuple[float, float, list[str]]:
    domain = domain_from_url(normalized_url)
    domain_root = domain.split(".")[0] if domain else ""
    domain_tokens = set(re.findall(r"[a-z0-9]{3,}", domain_root))
    overlap = len(account_tokens.intersection(domain_tokens))

    confidence = 86.0 - (result_rank * 8.0) - (query_rank * 5.0)
    score = 82.0 - (result_rank * 6.0) - (query_rank * 4.0)

    if overlap >= 2:
        confidence += 8.0
        score += 8.0
    elif overlap == 1:
        confidence += 3.5
        score += 3.0
    else:
        confidence -= 18.0
        score -= 16.0

    if looks_like_aggregator_domain(domain):
        confidence -= 25.0
        score -= 22.0

    confidence = max(5.0, min(98.0, confidence))
    score = max(5.0, min(98.0, score))
    reasons = [
        f"search_rank:{result_rank + 1}",
        f"query_rank:{query_rank + 1}",
        f"domain_overlap:{overlap}",
    ]
    if looks_like_aggregator_domain(domain):
        reasons.append("aggregator_domain")
    return round(score, 1), round(confidence, 1), reasons


def candidate_is_useful_direct_hit(candidate: dict, account_tokens: set[str]) -> bool:
    confidence = float(candidate.get("confidence") or 0)
    domain = domain_from_url(candidate.get("candidate_url", ""))
    if not domain:
        return False
    if looks_like_aggregator_domain(domain):
        return False
    domain_root = domain.split(".")[0]
    domain_tokens = set(re.findall(r"[a-z0-9]{3,}", domain_root))
    overlap = len(account_tokens.intersection(domain_tokens))
    return confidence >= 72 and overlap >= 1


def select_results_for_deep_fallback(search_result_pool: list[dict], limit: int) -> list[dict]:
    normalized_limit = max(1, limit)
    enriched = []
    for row in search_result_pool:
        domain = domain_from_url(row.get("url", ""))
        enriched.append((looks_like_aggregator_domain(domain), row))

    aggregators = [row for is_agg, row in enriched if is_agg]
    non_aggregators = [row for is_agg, row in enriched if not is_agg]

    aggregators.sort(key=lambda row: (row.get("query_rank", 99), row.get("result_rank", 99)))
    non_aggregators.sort(key=lambda row: (row.get("query_rank", 99), row.get("result_rank", 99)))

    selected = []
    max_aggregators = min(2, normalized_limit)
    selected.extend(aggregators[:max_aggregators])
    remaining = normalized_limit - len(selected)
    if remaining > 0:
        selected.extend(non_aggregators[:remaining])
    if len(selected) < normalized_limit:
        for row in aggregators[max_aggregators:]:
            if len(selected) >= normalized_limit:
                break
            selected.append(row)
    return selected


def llm_extract_official_website_from_result(
    account: dict,
    search_result: dict,
    snapshot: dict,
    model: str,
) -> tuple[dict | None, int, int]:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("OpenAI SDK is not installed. Run `uv sync`.") from exc

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for deep URL discovery.")

    client = OpenAI(api_key=api_key)
    payload = {
        "company": {
            "name": account.get("company_name", ""),
            "original_name": account.get("original_name", ""),
            "planning_entity_name": account.get("planning_entity_name", ""),
            "industry": account.get("industry", ""),
            "region": account.get("region", ""),
        },
        "search_result": {
            "url": search_result.get("url", ""),
            "title": search_result.get("title", ""),
            "description": search_result.get("description", ""),
        },
        "page_snapshot": snapshot,
    }
    system_prompt = (
        "You are extracting the official website URL for a company from a search result page.\n"
        "Return JSON only with keys: official_website_url, confidence, reason, reason_codes.\n"
        "official_website_url should be a concrete company domain (not aggregator/listing) or empty string.\n"
        "confidence: 0-100.\n"
        "reason_codes: array of short snake_case codes."
    )

    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
        ],
    )
    text = extract_response_text(response)
    parsed = parse_json_object_from_text(text)
    if not parsed:
        return None, *extract_usage_tokens(response)

    official_url = normalize_website_url(str(parsed.get("official_website_url", "")).strip())
    if not official_url:
        return None, *extract_usage_tokens(response)
    domain = domain_from_url(official_url)
    if looks_like_aggregator_domain(domain):
        return None, *extract_usage_tokens(response)

    confidence_raw = parsed.get("confidence")
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 45.0
    confidence = max(0.0, min(100.0, confidence))

    reason = normalize_text(parsed.get("reason"), max_length=180) or ""
    reason_codes = parsed.get("reason_codes")
    if not isinstance(reason_codes, list):
        reason_codes = []
    cleaned_codes = []
    for code in reason_codes[:6]:
        if isinstance(code, str):
            cleaned = re.sub(r"[^a-z0-9_]", "", code.lower())
            if cleaned:
                cleaned_codes.append(cleaned[:40])

    candidate = {
        "candidate_url": official_url,
        "score": round(max(10.0, min(98.0, confidence - 4.0)), 1),
        "confidence": round(confidence, 1),
        "status": "pending",
        "source": "search_page_playwright_llm",
        "reasons": ["deep_page_extraction"] + [f"llm_reason_code:{code}" for code in cleaned_codes],
    }
    if reason:
        candidate["reasons"].append(f"llm_reason:{reason}")
    input_tokens, output_tokens = extract_usage_tokens(response)
    return candidate, input_tokens, output_tokens


def generate_brave_url_candidates(
    account: dict,
    *,
    include_deep_fallback: bool = False,
    deep_result_limit: int = URL_DISCOVERY_DEEP_RESULT_LIMIT,
    deep_model: str = OPENAI_URL_DISCOVERY_MODEL,
) -> tuple[list[dict], dict]:
    if not BRAVE_SEARCH_API_KEY:
        return [], {
            "deep_llm_calls": 0,
            "deep_input_tokens": 0,
            "deep_output_tokens": 0,
            "deep_estimated_cost_usd": 0.0,
            "deep_candidates": 0,
            "deep_errors": 0,
        }

    queries = build_brave_queries_for_account(account)
    if not queries:
        return [], {
            "deep_llm_calls": 0,
            "deep_input_tokens": 0,
            "deep_output_tokens": 0,
            "deep_estimated_cost_usd": 0.0,
            "deep_candidates": 0,
            "deep_errors": 0,
        }

    account_tokens = set(tokenize_company_name_for_domain(account.get("company_name", "")))
    account_tokens.update(tokenize_company_name_for_domain(account.get("original_name", "")))

    candidates = []
    search_result_pool = []
    seen_urls = set()
    for query_rank, query in enumerate(queries):
        results = run_brave_web_search(query)
        for result_rank, result in enumerate(results[:BRAVE_SEARCH_MAX_RESULTS]):
            url_raw = result.get("url")
            normalized_url = normalize_website_url(url_raw)
            if not normalized_url or normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)
            search_result_pool.append(
                {
                    "url": normalized_url,
                    "title": normalize_text(result.get("title"), max_length=180) or "",
                    "description": normalize_text(result.get("description"), max_length=260) or "",
                    "query": query,
                    "query_rank": query_rank,
                    "result_rank": result_rank,
                }
            )

            title = normalize_text(result.get("title"), max_length=140) or ""
            description = normalize_text(result.get("description"), max_length=240) or ""
            score, confidence, reason_bits = score_brave_candidate(
                normalized_url,
                result_rank=result_rank,
                query_rank=query_rank,
                account_tokens=account_tokens,
            )
            reasons = [f"query:{query[:80]}"] + reason_bits
            if title:
                reasons.append(f"title:{title[:80]}")
            if description:
                reasons.append(f"snippet:{description[:110]}")

            candidates.append(
                {
                    "candidate_url": normalized_url,
                    "score": score,
                    "confidence": confidence,
                    "status": "pending",
                    "source": "brave_search",
                    "reasons": reasons,
                }
            )

    metrics = {
        "deep_llm_calls": 0,
        "deep_input_tokens": 0,
        "deep_output_tokens": 0,
        "deep_estimated_cost_usd": 0.0,
        "deep_candidates": 0,
        "deep_errors": 0,
    }

    has_useful_direct = any(candidate_is_useful_direct_hit(candidate, account_tokens) for candidate in candidates)
    if include_deep_fallback and not has_useful_direct and search_result_pool:
        deep_targets = select_results_for_deep_fallback(search_result_pool, max(1, deep_result_limit))
        for search_result in deep_targets:
            try:
                snapshot = fetch_website_snapshot(search_result["url"])
                extracted_candidate, input_tokens, output_tokens = llm_extract_official_website_from_result(
                    account=account,
                    search_result=search_result,
                    snapshot=snapshot,
                    model=deep_model,
                )
                metrics["deep_llm_calls"] += 1
                metrics["deep_input_tokens"] += input_tokens
                metrics["deep_output_tokens"] += output_tokens
                if extracted_candidate:
                    extra_reasons = list(extracted_candidate.get("reasons", []))
                    extra_reasons.append(f"from_result_url:{search_result['url'][:120]}")
                    extracted_candidate["reasons"] = extra_reasons[:12]
                    candidates.append(extracted_candidate)
                    metrics["deep_candidates"] += 1
            except Exception:
                metrics["deep_errors"] += 1
                continue
        metrics["deep_estimated_cost_usd"] = estimate_openai_cost_usd(
            deep_model,
            metrics["deep_input_tokens"],
            metrics["deep_output_tokens"],
        )

    candidates.sort(key=lambda item: (item["confidence"], item["score"]), reverse=True)
    return candidates[:12], metrics


def merge_url_candidates(*candidate_lists: list[dict]) -> list[dict]:
    merged = {}
    for source_list in candidate_lists:
        for candidate in source_list:
            url = candidate.get("candidate_url")
            if not url:
                continue
            existing = merged.get(url)
            if not existing:
                merged[url] = dict(candidate)
                continue

            existing_score = float(existing.get("score") or 0)
            existing_conf = float(existing.get("confidence") or 0)
            new_score = float(candidate.get("score") or 0)
            new_conf = float(candidate.get("confidence") or 0)
            if (new_conf, new_score) > (existing_conf, existing_score):
                merged[url] = dict(candidate)
                existing = merged[url]

            reasons = list(existing.get("reasons", []))
            for reason in candidate.get("reasons", []) or []:
                if reason not in reasons:
                    reasons.append(reason)
            existing["reasons"] = reasons[:12]
            sources = set((existing.get("source") or "").split("+"))
            candidate_source = candidate.get("source") or ""
            for source in candidate_source.split("+"):
                if source:
                    sources.add(source)
            existing["source"] = "+".join(sorted(s for s in sources if s))

    final_candidates = list(merged.values())
    final_candidates.sort(key=lambda item: (float(item.get("confidence") or 0), float(item.get("score") or 0)), reverse=True)
    return final_candidates[:10]


def generate_url_candidates(
    account: dict,
    *,
    include_brave: bool = True,
    include_deep_fallback: bool = URL_DISCOVERY_DEEP_FALLBACK_DEFAULT,
) -> tuple[list[dict], dict]:
    heuristic_candidates = generate_heuristic_url_candidates(account)
    brave_candidates = []
    brave_metrics = {
        "deep_llm_calls": 0,
        "deep_input_tokens": 0,
        "deep_output_tokens": 0,
        "deep_estimated_cost_usd": 0.0,
        "deep_candidates": 0,
        "deep_errors": 0,
    }
    if include_brave:
        brave_candidates, brave_metrics = generate_brave_url_candidates(
            account,
            include_deep_fallback=include_deep_fallback,
            deep_result_limit=URL_DISCOVERY_DEEP_RESULT_LIMIT,
            deep_model=OPENAI_URL_DISCOVERY_MODEL,
        )
    return merge_url_candidates(brave_candidates, heuristic_candidates), brave_metrics


def infer_url_stage_status(accepted_url: str, candidates: list[dict]) -> tuple[str, float | None]:
    accepted_candidates = [c for c in candidates if c.get("status") == "accepted"]
    pending_candidates = [c for c in candidates if c.get("status") == "pending"]

    if accepted_url or accepted_candidates:
        confidence = None
        if accepted_candidates:
            confidence = max(c.get("confidence") or 0 for c in accepted_candidates)
        elif accepted_url:
            confidence = 95.0
        return "accepted", confidence
    if pending_candidates:
        confidence = max(c.get("confidence") or 0 for c in pending_candidates)
        return "pending_review", confidence
    if candidates and all(c.get("status") == "rejected" for c in candidates):
        return "no_url", None
    if candidates:
        return "pending_review", None
    return "not_started", None


def refresh_company_url_stage(bp_id: int, *, last_run_at: str | None = None):
    account = ACCOUNTS.get(bp_id)
    if not account:
        return
    accepted_url = (account.get("website") or "").strip()
    candidates = list_url_candidates_for_company(bp_id)
    stage_status, stage_confidence = infer_url_stage_status(accepted_url, candidates)
    upsert_company_pipeline_status(
        bp_id=bp_id,
        url_stage_status=stage_status,
        url_stage_confidence=stage_confidence,
        url_last_run_at=last_run_at,
    )


def candidate_source_priority(source: str) -> int:
    normalized = (source or "").strip().lower()
    if "brave_top3_llm" in normalized or "playwright_llm" in normalized:
        return 4
    if "search_page_playwright_llm" in normalized:
        return 3
    if "brave_search" in normalized:
        return 2
    if "heuristic" in normalized:
        return 1
    return 0


def build_url_pipeline_rows() -> list[dict]:
    candidates = get_all_url_candidates()
    candidates_by_bp = {}
    for candidate in candidates:
        candidates_by_bp.setdefault(candidate["bp_id"], []).append(candidate)
    for bp_id in candidates_by_bp:
        candidates_by_bp[bp_id].sort(
            key=lambda c: (
                candidate_source_priority(c.get("source", "")),
                float(c.get("confidence") or 0),
                float(c.get("score") or 0),
            ),
            reverse=True,
        )

    pipeline_map = get_company_pipeline_statuses()
    rows = []

    for account in ACCOUNTS.values():
        bp_id = account["bp_id"]
        account_candidates = candidates_by_bp.get(bp_id, [])
        accepted_candidates = [c for c in account_candidates if c.get("status") == "accepted"]
        pending_candidates = [c for c in account_candidates if c.get("status") == "pending"]
        accepted_candidate = accepted_candidates[0] if accepted_candidates else None
        top_candidate = pending_candidates[0] if pending_candidates else None

        accepted_url = (account.get("website") or "").strip()
        accepted_confidence = None
        accepted_source = ""
        if accepted_candidate:
            accepted_url = accepted_candidate["candidate_url"]
            accepted_confidence = accepted_candidate.get("confidence")
            accepted_source = accepted_candidate.get("source", "")
        elif accepted_url:
            accepted_confidence = 95.0
            accepted_source = "manual_or_existing"

        pipeline_row = pipeline_map.get(bp_id, {})
        stage_status = pipeline_row.get("url_stage_status")
        if stage_status not in URL_STAGE_STATUSES:
            stage_status, _ = infer_url_stage_status(accepted_url, account_candidates)
        elif stage_status == "accepted" and not accepted_url:
            stage_status, _ = infer_url_stage_status(accepted_url, account_candidates)
        elif stage_status == "pending_review" and not top_candidate:
            stage_status, _ = infer_url_stage_status(accepted_url, account_candidates)
        stage_confidence = pipeline_row.get("url_stage_confidence")
        if stage_confidence is None and stage_status == "accepted":
            stage_confidence = accepted_confidence
        if stage_confidence is None and stage_status == "pending_review" and top_candidate:
            stage_confidence = top_candidate.get("confidence")

        rows.append(
            {
                "bp_id": bp_id,
                "company_name": account.get("company_name", ""),
                "industry": account.get("industry", ""),
                "score": account.get("score"),
                "tier": account.get("tier"),
                "stage_status": stage_status,
                "stage_confidence": stage_confidence,
                "accepted_url": accepted_url,
                "accepted_confidence": accepted_confidence,
                "accepted_source": accepted_source,
                "top_candidate": top_candidate,
                "candidate_count": len(account_candidates),
                "pending_count": len(pending_candidates),
                "accepted_count": len(accepted_candidates),
                "last_run_at": pipeline_row.get("url_last_run_at"),
            }
        )

    return rows


def build_url_pipeline_summary(rows: list[dict]) -> dict:
    total = len(rows)
    with_url = sum(1 for row in rows if row.get("accepted_url"))
    pending_review = sum(1 for row in rows if row.get("stage_status") == "pending_review")
    no_url = sum(1 for row in rows if row.get("stage_status") == "no_url")
    not_started = sum(1 for row in rows if row.get("stage_status") == "not_started")
    sure = sum(
        1
        for row in rows
        if row.get("accepted_url") and (row.get("accepted_confidence") or 0) >= URL_SURE_CONFIDENCE_THRESHOLD
    )
    dubious = sum(
        1
        for row in rows
        if row.get("accepted_url") and 0 < (row.get("accepted_confidence") or 0) < URL_SURE_CONFIDENCE_THRESHOLD
    )
    return {
        "total_companies": total,
        "with_url": with_url,
        "without_url": total - with_url,
        "pending_review": pending_review,
        "no_url": no_url,
        "not_started": not_started,
        "sure_url": sure,
        "dubious_url": dubious,
        "coverage_pct": round((with_url / total) * 100, 1) if total else 0.0,
        "brave_enabled": bool(BRAVE_SEARCH_API_KEY),
    }


def set_company_accepted_website(bp_id: int, website: str | None):
    normalized = normalize_website_url(website, allow_blank=True)
    if normalized is None:
        raise ValueError("Invalid website URL")
    upsert_enrichment(bp_id, website=normalized)
    if bp_id in ACCOUNTS:
        ACCOUNTS[bp_id]["website"] = normalized


def accept_url_candidate(
    candidate_id: int,
    user_id: int,
    *,
    candidate_url: str | None = None,
    confidence: float | None = None,
    source: str | None = None,
) -> dict | None:
    candidate = get_url_candidate_by_id(candidate_id)
    if not candidate:
        return None

    accepted_url = normalize_website_url(candidate_url or candidate.get("candidate_url"))
    if accepted_url is None:
        return None

    domain = accepted_url[8:].split("/", 1)[0]
    now_iso = utc_now_iso()

    bulk_set_url_candidate_status_for_company(candidate["bp_id"], "rejected", user_id)
    updated_fields = {
        "candidate_url": accepted_url,
        "normalized_domain": domain,
        "status": "accepted",
        "validated_by_user_id": user_id,
        "validated_at": now_iso,
    }
    if confidence is not None:
        updated_fields["confidence"] = float(confidence)
    if source is not None:
        updated_fields["source"] = source
    updated_candidate = update_url_candidate(candidate_id, updated_fields)
    if not updated_candidate:
        return None

    set_company_accepted_website(candidate["bp_id"], accepted_url)
    upsert_company_pipeline_status(
        bp_id=candidate["bp_id"],
        url_stage_status="accepted",
        url_stage_confidence=updated_candidate.get("confidence"),
        url_last_run_at=now_iso,
    )
    return updated_candidate


def reject_url_candidate(candidate_id: int, user_id: int) -> dict | None:
    candidate = get_url_candidate_by_id(candidate_id)
    if not candidate:
        return None
    now_iso = utc_now_iso()
    updated = update_url_candidate(
        candidate_id,
        {
            "status": "rejected",
            "validated_by_user_id": user_id,
            "validated_at": now_iso,
        },
    )
    if not updated:
        return None

    current_website = normalize_website_url(ACCOUNTS.get(candidate["bp_id"], {}).get("website"), allow_blank=True)
    candidate_url = normalize_website_url(candidate.get("candidate_url"), allow_blank=True)
    if current_website and candidate_url and current_website == candidate_url:
        set_company_accepted_website(candidate["bp_id"], "")

    refresh_company_url_stage(candidate["bp_id"], last_run_at=now_iso)
    return updated


def get_model_pricing(model: str) -> tuple[float, float]:
    defaults = URL_VALIDATION_MODEL_DEFAULT_PRICING.get(model, {"input_per_1m": 0.0, "output_per_1m": 0.0})
    input_per_1m = float(os.environ.get("OPENAI_URL_VALIDATION_INPUT_COST_PER_1M", defaults["input_per_1m"]))
    output_per_1m = float(os.environ.get("OPENAI_URL_VALIDATION_OUTPUT_COST_PER_1M", defaults["output_per_1m"]))
    return input_per_1m, output_per_1m


def estimate_openai_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    input_per_1m, output_per_1m = get_model_pricing(model)
    input_cost = (max(0, input_tokens) / 1_000_000.0) * input_per_1m
    output_cost = (max(0, output_tokens) / 1_000_000.0) * output_per_1m
    return round(input_cost + output_cost, 6)


def extract_response_text(response) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    try:
        dumped = response.model_dump()
    except Exception:
        dumped = None

    if isinstance(dumped, dict):
        chunks = []
        for item in dumped.get("output", []) or []:
            for content in item.get("content", []) or []:
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text.strip())
        if chunks:
            return "\n".join(chunks)
    return ""


def extract_usage_tokens(response) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0

    if isinstance(usage, dict):
        return int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)

    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    return input_tokens, output_tokens


def parse_json_object_from_text(text: str) -> dict | None:
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    snippet = text[start : end + 1]
    try:
        data = json.loads(snippet)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def fetch_website_snapshot(candidate_url: str) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run `uv sync` and `uv run playwright install chromium`."
        ) from exc

    snapshot = {
        "requested_url": candidate_url,
        "final_url": candidate_url,
        "http_status": None,
        "browser_used": "",
        "title": "",
        "meta_description": "",
        "headings": [],
        "body_excerpt": "",
        "contact_links": [],
        "external_links": [],
    }

    with sync_playwright() as playwright:
        def capture_with_browser(browser_name: str):
            launcher = getattr(playwright, browser_name)
            browser = launcher.launch(headless=True)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            try:
                response = page.goto(candidate_url, wait_until="domcontentloaded", timeout=25000)
                try:
                    page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                page.wait_for_timeout(1200)

                snapshot["browser_used"] = browser_name
                snapshot["final_url"] = page.url
                snapshot["http_status"] = response.status if response else None
                snapshot["title"] = (page.title() or "").strip()

                snapshot["meta_description"] = page.evaluate(
                    """() => {
                        const el = document.querySelector('meta[name="description"]');
                        return el ? (el.getAttribute('content') || '').trim() : '';
                    }"""
                )
                snapshot["headings"] = page.evaluate(
                    """() => {
                        const nodes = Array.from(document.querySelectorAll('h1, h2')).slice(0, 12);
                        return nodes
                            .map((node) => (node.textContent || '').trim())
                            .filter((text) => text.length > 0);
                    }"""
                )
                body_text = page.inner_text("body") or ""
                body_text = re.sub(r"\s+", " ", body_text).strip()
                snapshot["body_excerpt"] = body_text[:5200]
                snapshot["contact_links"] = page.evaluate(
                    """() => {
                        const wanted = ['contact', 'about', 'team', 'linkedin', 'company profile', 'website'];
                        const links = Array.from(document.querySelectorAll('a[href]'));
                        const out = [];
                        for (const link of links) {
                            const href = (link.getAttribute('href') || '').trim();
                            const text = (link.textContent || '').trim();
                            if (!href) continue;
                            const hay = (href + ' ' + text).toLowerCase();
                            if (wanted.some((kw) => hay.includes(kw))) {
                                out.push({href, text});
                            }
                            if (out.length >= 20) break;
                        }
                        return out;
                    }"""
                )
                snapshot["external_links"] = page.evaluate(
                    """() => {
                        const links = Array.from(document.querySelectorAll('a[href]'));
                        const out = [];
                        for (const link of links) {
                            const href = (link.href || '').trim();
                            const text = (link.textContent || '').trim();
                            if (!href.startsWith('http')) continue;
                            out.push({href, text});
                            if (out.length >= 80) break;
                        }
                        return out;
                    }"""
                )
                return True
            finally:
                context.close()
                browser.close()

        capture_errors = []
        for browser_name in ("chromium", "firefox"):
            try:
                if capture_with_browser(browser_name):
                    break
            except Exception as exc:
                capture_errors.append(f"{browser_name}:{exc}")
        else:
            raise RuntimeError("; ".join(capture_errors[-2:]) if capture_errors else "Failed to capture webpage snapshot")
    return snapshot


def llm_validate_url_candidate(account: dict, candidate_url: str, snapshot: dict, model: str) -> tuple[dict, int, int]:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("OpenAI SDK is not installed. Run `uv sync`.") from exc

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for URL validation.")

    client = OpenAI(api_key=api_key)
    prompt_payload = {
        "company": {
            "bp_id": account["bp_id"],
            "name": account.get("company_name", ""),
            "original_name": account.get("original_name", ""),
            "planning_entity_name": account.get("planning_entity_name", ""),
            "industry": account.get("industry", ""),
            "region": account.get("region", ""),
        },
        "candidate_url": candidate_url,
        "snapshot": snapshot,
    }

    system_msg = (
        "You validate if a website belongs to the target company.\n"
        "Return JSON only with keys: decision, confidence, reason, reason_codes, corrected_url.\n"
        "decision must be one of accept, review, reject.\n"
        "confidence must be number 0-100.\n"
        "reason_codes must be array of short snake_case labels.\n"
        "corrected_url may be empty string if none."
    )

    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=True)},
        ],
    )

    response_text = extract_response_text(response)
    parsed = parse_json_object_from_text(response_text)
    if not parsed:
        raise RuntimeError("Model did not return parseable JSON")

    decision_raw = str(parsed.get("decision", "")).strip().lower()
    decision = decision_raw if decision_raw in ("accept", "review", "reject") else "review"
    confidence_raw = parsed.get("confidence")
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 50.0
    confidence = max(0.0, min(100.0, confidence))
    reason = str(parsed.get("reason", "")).strip()[:500]

    reason_codes = parsed.get("reason_codes")
    if not isinstance(reason_codes, list):
        reason_codes = []
    normalized_reason_codes = []
    for code in reason_codes:
        if isinstance(code, str):
            cleaned = re.sub(r"[^a-z0-9_]", "", code.lower())[:40]
            if cleaned:
                normalized_reason_codes.append(cleaned)

    corrected_url = str(parsed.get("corrected_url", "")).strip()
    normalized_corrected_url = normalize_website_url(corrected_url) if corrected_url else ""

    input_tokens, output_tokens = extract_usage_tokens(response)
    result = {
        "decision": decision,
        "confidence": round(confidence, 1),
        "reason": reason,
        "reason_codes": normalized_reason_codes,
        "corrected_url": normalized_corrected_url or "",
    }
    return result, input_tokens, output_tokens


def build_validation_targets(max_companies: int | None = None) -> list[dict]:
    rows = build_url_pipeline_rows()
    targets = []
    for row in rows:
        top_candidate = row.get("top_candidate")
        if row.get("stage_status") != "pending_review":
            continue
        if not top_candidate or top_candidate.get("status") != "pending":
            continue
        account = ACCOUNTS.get(row["bp_id"])
        if not account:
            continue
        targets.append(
            {
                "bp_id": row["bp_id"],
                "company_name": row.get("company_name", ""),
                "candidate_id": top_candidate["id"],
                "candidate_url": top_candidate.get("candidate_url", ""),
                "account": account,
            }
        )

    targets.sort(key=lambda item: item["company_name"].lower())
    if max_companies and max_companies > 0:
        targets = targets[:max_companies]
    return targets


def serialize_url_validation_job(job: dict) -> dict:
    input_per_1m, output_per_1m = get_model_pricing(job.get("model", URL_VALIDATION_DEFAULT_MODEL))
    return sanitize(
        {
            "job_id": job.get("job_id"),
            "status": job.get("status"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "total": job.get("total", 0),
            "processed": job.get("processed", 0),
            "accepted": job.get("accepted", 0),
            "review": job.get("review", 0),
            "rejected": job.get("rejected", 0),
            "errors": job.get("errors", 0),
            "current_index": job.get("current_index", 0),
            "current_bp_id": job.get("current_bp_id"),
            "current_company_name": job.get("current_company_name"),
            "current_url": job.get("current_url"),
            "model": job.get("model"),
            "input_tokens": job.get("input_tokens", 0),
            "output_tokens": job.get("output_tokens", 0),
            "total_tokens": job.get("total_tokens", 0),
            "estimated_cost_usd": job.get("estimated_cost_usd", 0.0),
            "pricing": {
                "input_cost_per_1m": input_per_1m,
                "output_cost_per_1m": output_per_1m,
            },
            "last_error": job.get("last_error"),
            "updated_at": job.get("updated_at"),
        }
    )


def run_url_validation_job(job_id: str):
    global URL_VALIDATION_ACTIVE_JOB_ID
    now_iso = utc_now_iso()

    with URL_VALIDATION_JOB_LOCK:
        job = URL_VALIDATION_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["started_at"] = now_iso
        job["updated_at"] = now_iso
        URL_VALIDATION_ACTIVE_JOB_ID = job_id
        targets = list(job.get("targets", []))

    for index, target in enumerate(targets, start=1):
        with URL_VALIDATION_JOB_LOCK:
            job = URL_VALIDATION_JOBS.get(job_id)
            if not job:
                return
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
                job["finished_at"] = utc_now_iso()
                job["updated_at"] = job["finished_at"]
                URL_VALIDATION_ACTIVE_JOB_ID = None
                return
            job["current_index"] = index
            job["current_bp_id"] = target["bp_id"]
            job["current_company_name"] = target["company_name"]
            job["current_url"] = target["candidate_url"]
            job["updated_at"] = utc_now_iso()

        try:
            snapshot = fetch_website_snapshot(target["candidate_url"])
            llm_result, input_tokens, output_tokens = llm_validate_url_candidate(
                target["account"],
                target["candidate_url"],
                snapshot,
                model=job["model"],
            )

            decision = llm_result["decision"]
            confidence = llm_result["confidence"]
            corrected_url = llm_result.get("corrected_url") or target["candidate_url"]
            reason_codes = llm_result.get("reason_codes", [])
            reason = llm_result.get("reason", "")
            reasons = [f"llm_decision:{decision}", f"llm_confidence:{confidence}"]
            reasons.extend([f"llm_reason_code:{code}" for code in reason_codes[:5]])
            if reason:
                reasons.append(f"llm_reason:{reason[:120]}")

            if decision == "accept" and confidence >= 70:
                accepted = accept_url_candidate(
                    target["candidate_id"],
                    user_id=job["user_id"],
                    candidate_url=corrected_url,
                    confidence=confidence,
                    source="playwright_llm",
                )
                if accepted:
                    outcome = "accepted"
                else:
                    outcome = "review"
            elif decision == "reject" and confidence >= 60:
                rejected = reject_url_candidate(target["candidate_id"], user_id=job["user_id"])
                outcome = "rejected" if rejected else "review"
            else:
                update_url_candidate(
                    target["candidate_id"],
                    {
                        "confidence": confidence,
                        "source": "playwright_llm",
                        "status": "pending",
                        "reasons_json": json.dumps(reasons),
                    },
                )
                refresh_company_url_stage(target["bp_id"], last_run_at=utc_now_iso())
                outcome = "review"

            with URL_VALIDATION_JOB_LOCK:
                job = URL_VALIDATION_JOBS.get(job_id)
                if not job:
                    return
                job["processed"] += 1
                job["input_tokens"] += input_tokens
                job["output_tokens"] += output_tokens
                job["total_tokens"] = job["input_tokens"] + job["output_tokens"]
                job["estimated_cost_usd"] = estimate_openai_cost_usd(
                    job["model"], job["input_tokens"], job["output_tokens"]
                )
                if outcome == "accepted":
                    job["accepted"] += 1
                elif outcome == "rejected":
                    job["rejected"] += 1
                else:
                    job["review"] += 1
                job["updated_at"] = utc_now_iso()

        except Exception as exc:
            with URL_VALIDATION_JOB_LOCK:
                job = URL_VALIDATION_JOBS.get(job_id)
                if not job:
                    return
                job["processed"] += 1
                job["errors"] += 1
                job["review"] += 1
                job["last_error"] = str(exc)[:500]
                job["updated_at"] = utc_now_iso()

    with URL_VALIDATION_JOB_LOCK:
        job = URL_VALIDATION_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "completed" if not job.get("cancel_requested") else "cancelled"
        job["finished_at"] = utc_now_iso()
        job["updated_at"] = job["finished_at"]
        job["current_bp_id"] = None
        job["current_company_name"] = ""
        job["current_url"] = ""
        URL_VALIDATION_ACTIVE_JOB_ID = None


def serialize_url_discovery_job(job: dict) -> dict:
    input_per_1m, output_per_1m = get_model_pricing(job.get("model", URL_DISCOVERY_REVIEW_DEFAULT_MODEL))
    return sanitize(
        {
            "job_id": job.get("job_id"),
            "status": job.get("status"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "total": job.get("total", 0),
            "processed": job.get("processed", 0),
            "skipped": job.get("skipped", 0),
            "found_url_companies": job.get("found_url_companies", 0),
            "probable_url_companies": job.get("probable_url_companies", 0),
            "no_url_companies": job.get("no_url_companies", 0),
            "new_candidates_created": job.get("new_candidates_created", 0),
            "reviewed_results": job.get("reviewed_results", 0),
            "max_results_to_review": job.get("max_results_to_review", 3),
            "llm_calls": job.get("llm_calls", 0),
            "llm_errors": job.get("llm_errors", 0),
            "current_index": job.get("current_index", 0),
            "current_bp_id": job.get("current_bp_id"),
            "current_company_name": job.get("current_company_name"),
            "model": job.get("model"),
            "input_tokens": job.get("input_tokens", 0),
            "output_tokens": job.get("output_tokens", 0),
            "total_tokens": job.get("total_tokens", 0),
            "estimated_cost_usd": job.get("estimated_cost_usd", 0.0),
            "pricing": {
                "input_cost_per_1m": input_per_1m,
                "output_cost_per_1m": output_per_1m,
            },
            "last_error": job.get("last_error", ""),
            "updated_at": job.get("updated_at"),
        }
    )


def reject_legacy_heuristic_candidates(bp_id: int, user_id: int):
    now_iso = utc_now_iso()
    for candidate in list_url_candidates_for_company(bp_id):
        source = (candidate.get("source") or "").lower()
        if candidate.get("status") != "pending":
            continue
        if "heuristic" not in source:
            continue
        update_url_candidate(
            candidate["id"],
            {
                "status": "rejected",
                "source": "legacy_heuristic_rejected",
                "validated_by_user_id": user_id,
                "validated_at": now_iso,
            },
        )


def run_url_discovery_job(job_id: str):
    global URL_DISCOVERY_ACTIVE_JOB_ID

    with URL_DISCOVERY_JOB_LOCK:
        job = URL_DISCOVERY_JOBS.get(job_id)
        if not job:
            return
        now_iso = utc_now_iso()
        job["status"] = "running"
        job["started_at"] = now_iso
        job["updated_at"] = now_iso
        URL_DISCOVERY_ACTIVE_JOB_ID = job_id
        target_bp_ids = list(job.get("target_bp_ids", []))

    existing_candidates = get_all_url_candidates()
    existing_keys = {(c["bp_id"], c["candidate_url"]) for c in existing_candidates}

    for index, bp_id in enumerate(target_bp_ids, start=1):
        with URL_DISCOVERY_JOB_LOCK:
            job = URL_DISCOVERY_JOBS.get(job_id)
            if not job:
                return
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
                job["finished_at"] = utc_now_iso()
                job["updated_at"] = job["finished_at"]
                job["current_bp_id"] = None
                job["current_company_name"] = ""
                URL_DISCOVERY_ACTIVE_JOB_ID = None
                return

        account = ACCOUNTS.get(bp_id)
        if not account:
            with URL_DISCOVERY_JOB_LOCK:
                job = URL_DISCOVERY_JOBS.get(job_id)
                if not job:
                    return
                job["processed"] += 1
                job["llm_errors"] += 1
                job["last_error"] = f"Account not found for bp_id {bp_id}"
                job["updated_at"] = utc_now_iso()
            continue

        with URL_DISCOVERY_JOB_LOCK:
            job = URL_DISCOVERY_JOBS.get(job_id)
            if not job:
                return
            job["current_index"] = index
            job["current_bp_id"] = bp_id
            job["current_company_name"] = account.get("company_name", "")
            job["updated_at"] = utc_now_iso()

        now_iso = utc_now_iso()
        try:
            if job.get("clean_legacy_heuristics", True):
                reject_legacy_heuristic_candidates(bp_id, user_id=job["user_id"])

            query = build_step2_brave_query(account)
            if not query:
                upsert_company_pipeline_status(
                    bp_id=bp_id,
                    url_stage_status="no_url",
                    url_stage_confidence=None,
                    url_stage_notes="NF",
                    url_last_run_at=now_iso,
                )
                with URL_DISCOVERY_JOB_LOCK:
                    job = URL_DISCOVERY_JOBS.get(job_id)
                    if not job:
                        return
                    job["processed"] += 1
                    job["no_url_companies"] += 1
                    job["updated_at"] = utc_now_iso()
                continue

            results = run_brave_web_search(query, max_results=job["max_results_to_review"])[: job["max_results_to_review"]]

            accepted = False
            best_probable_confidence = None
            for result_rank, result in enumerate(results, start=1):
                normalized_result_url = normalize_website_url(result.get("url"))
                if not normalized_result_url:
                    continue

                with URL_DISCOVERY_JOB_LOCK:
                    job = URL_DISCOVERY_JOBS.get(job_id)
                    if not job:
                        return
                    job["reviewed_results"] += 1
                    job["llm_calls"] += 1
                    job["updated_at"] = utc_now_iso()
                    model = job["model"]

                try:
                    review, in_tokens, out_tokens = llm_review_brave_result_for_company(
                        account,
                        query=query,
                        result=result,
                        model=model,
                    )
                except Exception as exc:
                    with URL_DISCOVERY_JOB_LOCK:
                        job = URL_DISCOVERY_JOBS.get(job_id)
                        if not job:
                            return
                        job["llm_errors"] += 1
                        job["last_error"] = str(exc)[:500]
                        job["updated_at"] = utc_now_iso()
                    continue

                decision = review.get("decision", "review")
                confidence = float(review.get("confidence") or 0.0)
                candidate_url = normalize_website_url(
                    review.get("official_website_url") or normalized_result_url
                )
                if not candidate_url:
                    continue
                if looks_like_aggregator_domain(domain_from_url(candidate_url)):
                    continue

                reasons = [
                    f"step2_query:{query[:120]}",
                    f"step2_result_rank:{result_rank}",
                    f"llm_decision:{decision}",
                    f"llm_confidence:{round(confidence, 1)}",
                ]
                reason = review.get("reason", "")
                for code in (review.get("reason_codes") or [])[:5]:
                    reasons.append(f"llm_reason_code:{code}")
                if reason:
                    reasons.append(f"llm_reason:{str(reason)[:160]}")

                score = max(5.0, min(98.0, confidence - 2.0))
                created = upsert_url_candidate(
                    bp_id=bp_id,
                    candidate_url=candidate_url,
                    score=round(score, 1),
                    confidence=round(confidence, 1),
                    status="pending",
                    source="brave_top3_llm",
                    reasons=reasons,
                )
                key = (bp_id, candidate_url)
                is_new = key not in existing_keys
                existing_keys.add(key)

                with URL_DISCOVERY_JOB_LOCK:
                    job = URL_DISCOVERY_JOBS.get(job_id)
                    if not job:
                        return
                    job["input_tokens"] += in_tokens
                    job["output_tokens"] += out_tokens
                    job["total_tokens"] = job["input_tokens"] + job["output_tokens"]
                    job["estimated_cost_usd"] = estimate_openai_cost_usd(
                        job["model"], job["input_tokens"], job["output_tokens"]
                    )
                    if is_new:
                        job["new_candidates_created"] += 1
                    job["updated_at"] = utc_now_iso()

                if decision == "accept" and confidence >= 70:
                    accepted_candidate = accept_url_candidate(
                        created["id"],
                        user_id=job["user_id"],
                        candidate_url=candidate_url,
                        confidence=round(confidence, 1),
                        source="brave_top3_llm",
                    )
                    if accepted_candidate:
                        accepted = True
                        with URL_DISCOVERY_JOB_LOCK:
                            job = URL_DISCOVERY_JOBS.get(job_id)
                            if not job:
                                return
                            job["found_url_companies"] += 1
                            job["processed"] += 1
                            job["updated_at"] = utc_now_iso()
                        break

                if decision in ("accept", "review") and confidence >= 50:
                    if best_probable_confidence is None or confidence > best_probable_confidence:
                        best_probable_confidence = round(confidence, 1)

            if accepted:
                continue

            if best_probable_confidence is not None:
                upsert_company_pipeline_status(
                    bp_id=bp_id,
                    url_stage_status="pending_review",
                    url_stage_confidence=best_probable_confidence,
                    url_stage_notes="Probable URL from Brave top-3 review",
                    url_last_run_at=now_iso,
                )
                with URL_DISCOVERY_JOB_LOCK:
                    job = URL_DISCOVERY_JOBS.get(job_id)
                    if not job:
                        return
                    job["probable_url_companies"] += 1
                    job["processed"] += 1
                    job["updated_at"] = utc_now_iso()
                continue

            upsert_company_pipeline_status(
                bp_id=bp_id,
                url_stage_status="no_url",
                url_stage_confidence=None,
                url_stage_notes="NF",
                url_last_run_at=now_iso,
            )
            with URL_DISCOVERY_JOB_LOCK:
                job = URL_DISCOVERY_JOBS.get(job_id)
                if not job:
                    return
                job["no_url_companies"] += 1
                job["processed"] += 1
                job["updated_at"] = utc_now_iso()
        except Exception as exc:
            with URL_DISCOVERY_JOB_LOCK:
                job = URL_DISCOVERY_JOBS.get(job_id)
                if not job:
                    return
                job["processed"] += 1
                job["llm_errors"] += 1
                job["last_error"] = str(exc)[:500]
                job["updated_at"] = utc_now_iso()

    with URL_DISCOVERY_JOB_LOCK:
        job = URL_DISCOVERY_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "completed" if not job.get("cancel_requested") else "cancelled"
        job["finished_at"] = utc_now_iso()
        job["updated_at"] = job["finished_at"]
        job["current_index"] = 0
        job["current_bp_id"] = None
        job["current_company_name"] = ""
        URL_DISCOVERY_ACTIVE_JOB_ID = None


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

        # Website: enrichment overrides xlsx
        website = enrichment.get("website", "") or row.get("xlsx_web_address", "")
        # City: enrichment overrides xlsx
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
            # New fields from V2
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


def get_active_scoring_profile(user: dict) -> dict:
    """Return active scoring profile for user (session override, else global default)."""
    profile = None
    selected_id = session.get("active_scoring_profile_id")
    manual_selection = bool(session.get("active_scoring_profile_manual"))

    if manual_selection and selected_id is not None:
        try:
            profile = get_profile_for_user(user, int(selected_id))
        except (TypeError, ValueError):
            profile = None

    if not profile:
        default_id = get_default_scoring_profile_id()
        if default_id is not None:
            profile = get_profile_for_user(user, default_id)

    if not profile:
        if user["role"] == "super_admin":
            profiles = list_all_scoring_profiles(current_user_id=user["id"])
        else:
            profiles = list_scoring_profiles_for_user(user["id"])
        if profiles:
            profile = profiles[0]

    if not profile:
        raise RuntimeError("No scoring profile available.")

    session["active_scoring_profile_id"] = profile["id"]
    if not manual_selection:
        session["active_scoring_profile_manual"] = False
    return profile


def score_account_with_profile(account: dict, profile: dict) -> dict:
    scored = dict(account)
    score_result = compute_score(
        scored,
        weights=profile["weights"],
        industry_scores=profile["industry_scores"],
    )
    scored["score"] = score_result["composite"]
    scored["tier"] = score_result["tier"]
    scored["score_breakdown"] = score_result["breakdown"]
    return scored


def get_scored_accounts(profile: dict) -> list[dict]:
    return [score_account_with_profile(a, profile) for a in ACCOUNTS.values()]


def get_profile_for_user(user: dict, profile_id: int) -> dict | None:
    profile = get_scoring_profile_for_user(profile_id, user["id"])
    if profile:
        return profile
    if user["role"] == "super_admin":
        return get_scoring_profile_by_id(profile_id, current_user_id=user["id"])
    return None


def send_code_email(email, code):
    """Send login code via Resend."""
    resend.Emails.send(
        {
            "from": RESEND_FROM,
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


# --- Auth helpers ---

ALLOWED_DOMAIN = "epiuse.com"


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        email = session.get("user_email")
        if not email:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("login"))
        user = get_user_by_email(email)
        if not user:
            session.clear()
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("login"))
        if user["role"] == "pending":
            if request.path.startswith("/api/"):
                return jsonify({"error": "Access pending approval"}), 403
            return redirect(url_for("pending"))
        request.current_user = user
        return f(*args, **kwargs)

    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        email = session.get("user_email")
        if not email:
            return redirect(url_for("login"))
        user = get_user_by_email(email)
        if not user or user["role"] not in ("admin", "super_admin"):
            return redirect(url_for("index"))
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


@app.route("/login", methods=["GET"])
def login():
    if session.get("user_email"):
        user = get_user_by_email(session["user_email"])
        if user and user["role"] in ("user", "admin", "super_admin"):
            return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/login", methods=["POST"])
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


@app.route("/verify", methods=["POST"])
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
        return redirect(url_for("pending"))
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/pending")
def pending():
    email = session.get("user_email")
    if not email:
        return redirect(url_for("login"))
    user = get_user_by_email(email)
    if user and user["role"] in ("user", "admin", "super_admin"):
        return redirect(url_for("index"))
    return render_template("pending.html", email=email)


# --- Admin routes ---


@app.route("/admin/users")
@admin_required
def admin_users():
    users = get_all_users()
    return render_template("admin.html", users=users, current_user=request.current_user)


@app.route("/admin/users/approve", methods=["POST"])
@admin_required
def admin_approve():
    email = request.form.get("email", "")
    update_user_role(email, "user")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/make-admin", methods=["POST"])
@admin_required
def admin_make_admin():
    email = request.form.get("email", "")
    update_user_role(email, "admin")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/deny", methods=["POST"])
@admin_required
def admin_deny():
    email = request.form.get("email", "")
    delete_user(email)
    return redirect(url_for("admin_users"))


# --- Dashboard routes ---


@app.route("/")
@login_required
def index():
    return render_template("index.html", current_user=request.current_user)


@app.route("/scoring-profiles")
@login_required
def scoring_profiles_page():
    return render_template("scoring_profiles.html", current_user=request.current_user)


@app.route("/tier/<tier_letter>")
@login_required
def tier_page(tier_letter):
    tier_letter = tier_letter.upper()
    if tier_letter not in ("A", "B", "C", "D", "E"):
        return redirect(url_for("index"))
    return render_template("tier_view.html", current_user=request.current_user, tier=tier_letter)


@app.route("/pipeline/urls")
@login_required
def url_pipeline_page():
    return render_template("url_pipeline.html", current_user=request.current_user)


@app.route("/data-dictionary")
@login_required
def data_dictionary_page():
    return render_template("data_dictionary.html", current_user=request.current_user)


@app.route("/api/data-dictionary")
@login_required
def api_data_dictionary():
    excel_fields = [
        # --- Campos exclusivos de Hoja 1 ---
        {"field_key": "s1_bp_id", "excel_column": "Business Partner ID", "sheet": "Both", "stored": True,
         "internal_name": "bp_id", "storage_type": "transformed",
         "transform_notes": "Convertido a entero. Las filas sin este valor se descartan. Se usa como clave de union entre hojas.",
         "description": "Identificador unico de Business Partner en SAP para cada cuenta."},
        {"field_key": "s1_erp_isp_id", "excel_column": "ERP ISP ID", "sheet": "Sheet1", "stored": True,
         "internal_name": "erp_isp_id", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto. Tambien se usa para derivar el campo sap_status (con valor = 'Existing SAP', vacio = 'Net New').",
         "description": "Identificador ERP ISP. Indica si la cuenta tiene una relacion existente con SAP."},
        {"field_key": "s1_company_name", "excel_column": "Organization Name1", "sheet": "Both", "stored": True,
         "internal_name": "company_name", "storage_type": "transformed",
         "transform_notes": "Se convierte a titulo si esta en mayusculas (preservando acronimos como SA, DE, CV). Se prefiere el valor de Hoja 2 sobre Hoja 1.",
         "description": "Nombre legal o registrado de la empresa."},
        {"field_key": "s1_planning_entity", "excel_column": "Planning Entity", "sheet": "Sheet1", "stored": True,
         "internal_name": "planning_entity_id", "storage_type": "transformed",
         "transform_notes": "Convertido a entero.",
         "description": "Identificador numerico de la entidad de planificacion."},
        {"field_key": "s1_planning_entity_name", "excel_column": "Planning Entity Name", "sheet": "Both", "stored": True,
         "internal_name": "planning_entity_name", "storage_type": "transformed",
         "transform_notes": "Se convierte a titulo si esta en mayusculas. Se prefiere el valor de Hoja 2 sobre Hoja 1.",
         "description": "Nombre de la entidad de planificacion a la que pertenece la cuenta."},
        {"field_key": "s1_region", "excel_column": "Default Address Region Descr", "sheet": "Both", "stored": True,
         "internal_name": "region", "storage_type": "transformed",
         "transform_notes": "Se prefiere el valor de Hoja 2. Si es nulo, se usa 'Jalisco' por defecto.",
         "description": "Descripcion de la region geografica de la direccion predeterminada de la cuenta."},
        {"field_key": "s1_account_exec", "excel_column": "Account Executive Name 2026", "sheet": "Both", "stored": True,
         "internal_name": "account_exec", "storage_type": "renamed",
         "transform_notes": "Se prefiere el valor de Hoja 2 sobre Hoja 1.",
         "description": "Nombre del ejecutivo de cuenta asignado para la planificacion 2026."},
        {"field_key": "s1_master_industry", "excel_column": "Default Master Code Descr", "sheet": "Both", "stored": True,
         "internal_name": "master_industry", "storage_type": "transformed",
         "transform_notes": "Se prefiere Hoja 2. Si es nulo, se usa 'Unclassified'. Se utiliza como entrada para el algoritmo de clasificacion de industria.",
         "description": "Descripcion del codigo maestro de industria de SAP (clasificacion cruda de industria de SAP)."},
        {"field_key": "s1_sic_description", "excel_column": "Default SIC Primary Descr", "sheet": "Both", "stored": True,
         "internal_name": "sic_description", "storage_type": "transformed",
         "transform_notes": "Se prefiere Hoja 2. Si es nulo, queda como texto vacio. Se usa como entrada para clasificacion de industria por palabras clave.",
         "description": "Descripcion primaria de la Clasificacion Industrial Estandar (SIC)."},
        {"field_key": "s1_base_instalada", "excel_column": "BASE INSTALADA", "sheet": "Sheet1", "stored": True,
         "internal_name": "base_instalada", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto. Si es nulo, queda vacio. Nota: el nombre de la columna puede tener un espacio al final en Excel.",
         "description": "Lista de productos SAP instalados para esta cuenta (base instalada)."},
        # --- Campos exclusivos de Hoja 2 ---
        {"field_key": "s2_tax_number", "excel_column": "Account Main Tax Number", "sheet": "Sheet2", "stored": True,
         "internal_name": "tax_number", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto.",
         "description": "Numero de identificacion fiscal principal (RFC) de la cuenta."},
        {"field_key": "s2_top_parent_name", "excel_column": "SAP Top Parent Name", "sheet": "Sheet2", "stored": True,
         "internal_name": "top_parent_name", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto.",
         "description": "Nombre de la empresa matriz de nivel superior en la jerarquia corporativa de SAP."},
        {"field_key": "s2_address_street", "excel_column": "Default Address Street", "sheet": "Sheet2", "stored": True,
         "internal_name": "address_street", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto.",
         "description": "Calle de la direccion predeterminada de la cuenta."},
        {"field_key": "s2_address_city", "excel_column": "Default Address City", "sheet": "Sheet2", "stored": True,
         "internal_name": "address_city / city", "storage_type": "transformed",
         "transform_notes": "Se guarda como texto. Se usa como respaldo para el campo 'city'; el valor de enriquecimiento en BD tiene prioridad si existe.",
         "description": "Ciudad de la direccion predeterminada de la cuenta."},
        {"field_key": "s2_address_region_code", "excel_column": "Default Address Region", "sheet": "Sheet2", "stored": True,
         "internal_name": "address_region_code", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto.",
         "description": "Codigo de region (ISO o local) de la direccion predeterminada de la cuenta."},
        {"field_key": "s2_address_postal_code", "excel_column": "Default Address Postal Code", "sheet": "Sheet2", "stored": True,
         "internal_name": "address_postal_code", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto.",
         "description": "Codigo postal de la direccion predeterminada de la cuenta."},
        {"field_key": "s2_account_owner_id", "excel_column": "Account Owner ID", "sheet": "Sheet2", "stored": True,
         "internal_name": "account_owner_id", "storage_type": "transformed",
         "transform_notes": "Convertido a entero.",
         "description": "Identificador numerico del propietario/responsable de la cuenta."},
        {"field_key": "s2_turnover_usd", "excel_column": "Turnover US Dollar Value", "sheet": "Sheet2", "stored": True,
         "internal_name": "revenue", "storage_type": "transformed",
         "transform_notes": "Convertido a decimal. Renombrado de 'turnover_usd' a 'revenue'. El valor de enriquecimiento en BD tiene prioridad si existe.",
         "description": "Facturacion/ingresos anuales en dolares estadounidenses."},
        {"field_key": "s2_num_employees", "excel_column": "Num Employees Local Value", "sheet": "Sheet2", "stored": True,
         "internal_name": "employee_count", "storage_type": "transformed",
         "transform_notes": "Convertido a entero. Renombrado de 'num_employees' a 'employee_count'. El valor de enriquecimiento en BD tiene prioridad si existe.",
         "description": "Numero de empleados en la entidad local."},
        {"field_key": "s2_market_segment", "excel_column": "Internal Market Segment IMS Desc", "sheet": "Sheet2", "stored": True,
         "internal_name": "market_segment", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto.",
         "description": "Descripcion de la clasificacion de Segmento de Mercado Interno (IMS) de SAP."},
        {"field_key": "s2_bpr_products", "excel_column": "Buying Product Relationship BPR Descr Concat", "sheet": "Sheet2", "stored": True,
         "internal_name": "bpr_products", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto. Si es nulo, queda vacio.",
         "description": "Lista concatenada de Relaciones de Compra de Productos (BPR) — los productos SAP que la cuenta ha comprado o con los que esta asociada."},
        {"field_key": "s2_archetype", "excel_column": "Archetype Descr Plan 2026", "sheet": "Sheet2", "stored": True,
         "internal_name": "archetype", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto.",
         "description": "Clasificacion de arquetipo de la cuenta para el plan 2026."},
        {"field_key": "s2_rbc_plan", "excel_column": "RBC 2026 PLAN", "sheet": "Sheet2", "stored": True,
         "internal_name": "rbc_plan", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto.",
         "description": "Asignacion de plan de Clasificacion Basada en Ingresos (RBC) para 2026."},
        {"field_key": "s2_master_code", "excel_column": "Default Master Code", "sheet": "Sheet2", "stored": True,
         "internal_name": "master_code", "storage_type": "transformed",
         "transform_notes": "Convertido a entero. Se usa para clasificacion de industria mediante mapeo de codigos maestros (27 codigos SAP → 16 categorias de industria).",
         "description": "Codigo maestro numerico de SAP utilizado para la clasificacion de industria."},
        {"field_key": "s2_xlsx_web_address", "excel_column": "Account Web Address", "sheet": "Sheet2", "stored": True,
         "internal_name": "xlsx_web_address / website", "storage_type": "transformed",
         "transform_notes": "Se guarda como texto. Se usa como respaldo para el campo 'website'; el valor de enriquecimiento en BD tiene prioridad si existe.",
         "description": "URL del sitio web de la empresa segun registro en SAP."},
    ]

    derived_fields = [
        {"field_key": "d_display_name", "internal_name": "display_name", "source": "Calculado",
         "description": "Nombre preferido para mostrar: el mas largo entre company_name y planning_entity_name.",
         "transform_notes": "Se compara por longitud de texto al momento de cargar."},
        {"field_key": "d_sap_status", "internal_name": "sap_status", "source": "Derivado de erp_isp_id",
         "description": "Estado de la relacion SAP de la cuenta.",
         "transform_notes": "'Existing SAP' si erp_isp_id tiene valor, 'Net New' en caso contrario."},
        {"field_key": "d_industry", "internal_name": "industry", "source": "Clasificado",
         "description": "Categoria de industria normalizada (una de 16 categorias + Sin Clasificar).",
         "transform_notes": "Cascada: override manual → mapeo de master_code (27 codigos) → coincidencia por palabras clave (espanol e ingles) → 'Unclassified'."},
        {"field_key": "d_industry_source", "internal_name": "industry_source", "source": "Clasificado",
         "description": "Como se determino la clasificacion de industria.",
         "transform_notes": "Valores: 'manual' (override de usuario), 'master_code', 'keyword', 'none'."},
        {"field_key": "d_score", "internal_name": "score", "source": "Calculado por solicitud",
         "description": "Puntaje compuesto de la cuenta (0–100) basado en el perfil de scoring activo.",
         "transform_notes": "Suma ponderada de: industry_match, company_size, sap_relationship, data_completeness."},
        {"field_key": "d_tier", "internal_name": "tier", "source": "Calculado por solicitud",
         "description": "Nivel por letra (A/B/C/D/E) derivado del puntaje compuesto.",
         "transform_notes": "A ≥ 80, B ≥ 60, C ≥ 40, D ≥ 20, E < 20 (umbrales del perfil de scoring)."},
        {"field_key": "d_notes", "internal_name": "notes", "source": "BD (account_enrichments)",
         "description": "Notas de texto libre agregadas por usuarios sobre la cuenta.",
         "transform_notes": "Almacenado en tabla SQLite account_enrichments, no proviene del Excel."},
        {"field_key": "d_starred", "internal_name": "starred", "source": "BD (account_enrichments)",
         "description": "Indicador booleano de si un usuario ha marcado como favorita la cuenta.",
         "transform_notes": "Almacenado como entero (0/1) en BD, convertido a booleano."},
        {"field_key": "d_tags", "internal_name": "tags", "source": "BD (account_enrichments)",
         "description": "Etiquetas definidas por el usuario para categorizar cuentas.",
         "transform_notes": "Almacenado como arreglo JSON en BD."},
        {"field_key": "d_target_list", "internal_name": "target_list", "source": "BD (account_enrichments)",
         "description": "Indicador booleano de si la cuenta esta en la lista objetivo.",
         "transform_notes": "Almacenado como entero (0/1) en BD, convertido a booleano."},
        {"field_key": "d_website", "internal_name": "website", "source": "Enriquecimiento BD o xlsx_web_address",
         "description": "URL del sitio web de la empresa (enriquecido o respaldo del Excel).",
         "transform_notes": "Prioridad: BD account_enrichments.website → columna 'Account Web Address' del Excel."},
        {"field_key": "d_city", "internal_name": "city", "source": "Enriquecimiento BD o address_city",
         "description": "Ciudad de la empresa (enriquecido o respaldo del Excel).",
         "transform_notes": "Prioridad: BD account_enrichments.city → columna 'Default Address City' del Excel."},
    ]

    comments = get_all_dd_comments()
    for f in excel_fields + derived_fields:
        c = comments.get(f["field_key"], {})
        f["comment"] = c.get("comment", "")
        f["comment_updated_by"] = c.get("updated_by", "")
        f["comment_updated_at"] = c.get("updated_at", "")

    return jsonify({"excel_fields": excel_fields, "derived_fields": derived_fields})


@app.route("/api/data-dictionary/<field_key>/comment", methods=["PUT"])
@login_required
def api_data_dictionary_comment(field_key):
    data = request.get_json(force=True)
    comment = data.get("comment", "").strip()
    upsert_dd_comment(field_key, comment, request.current_user["email"])
    return jsonify({"ok": True})


@app.route("/api/pipeline/urls/summary")
@login_required
def api_url_pipeline_summary():
    rows = build_url_pipeline_rows()
    return jsonify(sanitize(build_url_pipeline_summary(rows)))


@app.route("/api/pipeline/urls/queue")
@login_required
def api_url_pipeline_queue():
    status_filter = request.args.get("status", "all").strip().lower()
    search = request.args.get("search", "").strip().lower()

    rows = build_url_pipeline_rows()

    if status_filter in URL_STAGE_STATUSES:
        rows = [row for row in rows if row.get("stage_status") == status_filter]
    elif status_filter == "with_url":
        rows = [row for row in rows if row.get("accepted_url")]
    elif status_filter == "without_url":
        rows = [row for row in rows if not row.get("accepted_url")]

    if search:
        rows = [
            row
            for row in rows
            if search in row.get("company_name", "").lower()
            or search in row.get("industry", "").lower()
            or search in (row.get("accepted_url") or "").lower()
            or search in ((row.get("top_candidate") or {}).get("candidate_url") or "").lower()
        ]

    stage_order = {
        "pending_review": 0,
        "not_started": 1,
        "no_url": 2,
        "accepted": 3,
    }
    rows.sort(
        key=lambda row: (
            stage_order.get(row.get("stage_status"), 99),
            row.get("company_name", "").lower(),
        )
    )

    return jsonify(sanitize({"rows": rows, "summary": build_url_pipeline_summary(rows)}))


@app.route("/api/pipeline/urls/discover", methods=["POST"])
@login_required
def api_url_pipeline_discover():
    return api_url_pipeline_discover_start()


@app.route("/api/pipeline/urls/discover-job/start", methods=["POST"])
@login_required
def api_url_pipeline_discover_start():
    global URL_DISCOVERY_ACTIVE_JOB_ID
    data = parse_json_payload() or {}
    force = bool(data.get("force", False))
    only_missing = bool(data.get("only_missing", True))
    clean_legacy_heuristics = bool(data.get("clean_legacy_heuristics", True))
    model = normalize_text(data.get("model"), max_length=80) or URL_DISCOVERY_REVIEW_DEFAULT_MODEL
    max_results_to_review_raw = data.get("max_results_to_review", 3)
    max_companies_raw = data.get("max_companies", 0)
    try:
        max_results_to_review = int(max_results_to_review_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "max_results_to_review must be an integer"}), 400
    max_results_to_review = max(1, min(max_results_to_review, 3))
    try:
        max_companies = int(max_companies_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "max_companies must be an integer"}), 400
    max_companies = max(0, min(max_companies, 5000))

    if not BRAVE_SEARCH_API_KEY:
        return jsonify({"error": "BRAVE_API_KEY is required for URL discovery"}), 400
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        return jsonify({"error": "OPENAI_API_KEY is required for URL discovery"}), 400

    with URL_DISCOVERY_JOB_LOCK:
        if URL_DISCOVERY_ACTIVE_JOB_ID:
            active_job = URL_DISCOVERY_JOBS.get(URL_DISCOVERY_ACTIVE_JOB_ID)
            if active_job and active_job.get("status") == "running":
                return jsonify(
                    {
                        "error": "A URL discovery job is already running",
                        "active_job": serialize_url_discovery_job(active_job),
                    }
                ), 409

    accepted_bp_ids = set()
    for candidate in get_all_url_candidates():
        if candidate.get("status") == "accepted":
            accepted_bp_ids.add(int(candidate["bp_id"]))

    ordered_accounts = sorted(ACCOUNTS.values(), key=lambda a: (a.get("company_name") or "").lower())
    target_bp_ids = []
    skipped_companies = 0
    for account in ordered_accounts:
        bp_id = int(account["bp_id"])
        current_website = normalize_website_url(account.get("website"), allow_blank=True)
        has_accepted = bp_id in accepted_bp_ids
        if only_missing and not force and (current_website or has_accepted):
            skipped_companies += 1
            continue
        target_bp_ids.append(bp_id)
        if max_companies > 0 and len(target_bp_ids) >= max_companies:
            break

    now_iso = utc_now_iso()
    job_id = str(uuid4())
    job = {
        "job_id": job_id,
        "status": "idle",
        "model": model,
        "user_id": request.current_user["id"],
        "created_at": now_iso,
        "started_at": None,
        "finished_at": None,
        "updated_at": now_iso,
        "total": len(target_bp_ids),
        "processed": 0,
        "skipped": skipped_companies,
        "found_url_companies": 0,
        "probable_url_companies": 0,
        "no_url_companies": 0,
        "new_candidates_created": 0,
        "reviewed_results": 0,
        "max_results_to_review": max_results_to_review,
        "llm_calls": 0,
        "llm_errors": 0,
        "current_index": 0,
        "current_bp_id": None,
        "current_company_name": "",
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
        "last_error": "",
        "cancel_requested": False,
        "only_missing": only_missing,
        "force": force,
        "clean_legacy_heuristics": clean_legacy_heuristics,
        "target_bp_ids": target_bp_ids,
    }

    with URL_DISCOVERY_JOB_LOCK:
        URL_DISCOVERY_JOBS[job_id] = job

    worker = threading.Thread(target=run_url_discovery_job, args=(job_id,), daemon=True)
    worker.start()

    return jsonify(sanitize({"status": "ok", "job": serialize_url_discovery_job(job)})), 202


@app.route("/api/pipeline/urls/discover-job")
@login_required
def api_url_pipeline_discover_status():
    requested_job_id = request.args.get("job_id", "").strip()

    with URL_DISCOVERY_JOB_LOCK:
        if requested_job_id:
            job = URL_DISCOVERY_JOBS.get(requested_job_id)
        elif URL_DISCOVERY_ACTIVE_JOB_ID:
            job = URL_DISCOVERY_JOBS.get(URL_DISCOVERY_ACTIVE_JOB_ID)
        else:
            jobs = list(URL_DISCOVERY_JOBS.values())
            jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
            job = jobs[0] if jobs else None

    if not job:
        return jsonify({"job": None, "status": "ok"})
    payload = {"status": "ok", "job": serialize_url_discovery_job(job)}
    if job.get("status") in ("completed", "cancelled", "failed"):
        rows = build_url_pipeline_rows()
        payload["summary"] = build_url_pipeline_summary(rows)
    return jsonify(sanitize(payload))


@app.route("/api/pipeline/urls/discover-job/<job_id>/cancel", methods=["POST"])
@login_required
def api_url_pipeline_discover_cancel(job_id):
    with URL_DISCOVERY_JOB_LOCK:
        job = URL_DISCOVERY_JOBS.get(job_id)
        if not job:
            return jsonify({"error": "URL discovery job not found"}), 404
        if job.get("status") != "running":
            return jsonify({"error": "URL discovery job is not running"}), 400
        job["cancel_requested"] = True
        job["updated_at"] = utc_now_iso()

    return jsonify({"status": "ok", "job": serialize_url_discovery_job(job)})


@app.route("/api/pipeline/urls/auto-accept", methods=["POST"])
@login_required
def api_url_pipeline_auto_accept():
    data = parse_json_payload() or {}
    threshold_raw = data.get("min_confidence", URL_SURE_CONFIDENCE_THRESHOLD)
    try:
        min_confidence = float(threshold_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "min_confidence must be numeric"}), 400
    min_confidence = max(0.0, min(100.0, min_confidence))

    rows = build_url_pipeline_rows()
    accepted_count = 0

    for row in rows:
        top_candidate = row.get("top_candidate")
        if row.get("stage_status") != "pending_review":
            continue
        if not top_candidate or top_candidate.get("status") != "pending":
            continue
        confidence = top_candidate.get("confidence") or 0
        if confidence < min_confidence:
            continue
        updated = accept_url_candidate(top_candidate["id"], request.current_user["id"])
        if updated:
            accepted_count += 1

    updated_rows = build_url_pipeline_rows()
    return jsonify(
        sanitize(
            {
                "status": "ok",
                "accepted_count": accepted_count,
                "summary": build_url_pipeline_summary(updated_rows),
            }
        )
    )


@app.route("/api/pipeline/urls/validate-job/start", methods=["POST"])
@login_required
def api_url_pipeline_validate_start():
    global URL_VALIDATION_ACTIVE_JOB_ID
    data = parse_json_payload() or {}
    model = normalize_text(data.get("model"), max_length=80) or URL_VALIDATION_DEFAULT_MODEL

    max_companies_raw = data.get("max_companies", 120)
    try:
        max_companies = int(max_companies_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "max_companies must be an integer"}), 400
    if max_companies <= 0:
        return jsonify({"error": "max_companies must be greater than zero"}), 400
    max_companies = min(max_companies, 2000)

    with URL_VALIDATION_JOB_LOCK:
        if URL_VALIDATION_ACTIVE_JOB_ID:
            active_job = URL_VALIDATION_JOBS.get(URL_VALIDATION_ACTIVE_JOB_ID)
            if active_job and active_job.get("status") == "running":
                return jsonify(
                    {
                        "error": "A validation job is already running",
                        "active_job": serialize_url_validation_job(active_job),
                    }
                ), 409

    targets = build_validation_targets(max_companies=max_companies)
    if not targets:
        return jsonify({"error": "No pending URL candidates available for validation"}), 400

    now_iso = utc_now_iso()
    job_id = str(uuid4())
    job = {
        "job_id": job_id,
        "status": "idle",
        "model": model,
        "user_id": request.current_user["id"],
        "created_at": now_iso,
        "started_at": None,
        "finished_at": None,
        "updated_at": now_iso,
        "total": len(targets),
        "processed": 0,
        "accepted": 0,
        "review": 0,
        "rejected": 0,
        "errors": 0,
        "current_index": 0,
        "current_bp_id": None,
        "current_company_name": "",
        "current_url": "",
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
        "last_error": "",
        "cancel_requested": False,
        "targets": targets,
    }

    with URL_VALIDATION_JOB_LOCK:
        URL_VALIDATION_JOBS[job_id] = job

    worker = threading.Thread(target=run_url_validation_job, args=(job_id,), daemon=True)
    worker.start()

    return jsonify(sanitize({"status": "ok", "job": serialize_url_validation_job(job)})), 202


@app.route("/api/pipeline/urls/validate-job")
@login_required
def api_url_pipeline_validate_status():
    requested_job_id = request.args.get("job_id", "").strip()

    with URL_VALIDATION_JOB_LOCK:
        if requested_job_id:
            job = URL_VALIDATION_JOBS.get(requested_job_id)
        elif URL_VALIDATION_ACTIVE_JOB_ID:
            job = URL_VALIDATION_JOBS.get(URL_VALIDATION_ACTIVE_JOB_ID)
        else:
            jobs = list(URL_VALIDATION_JOBS.values())
            jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
            job = jobs[0] if jobs else None

    if not job:
        return jsonify({"job": None, "status": "ok"})
    return jsonify({"status": "ok", "job": serialize_url_validation_job(job)})


@app.route("/api/pipeline/urls/validate-job/<job_id>/cancel", methods=["POST"])
@login_required
def api_url_pipeline_validate_cancel(job_id):
    with URL_VALIDATION_JOB_LOCK:
        job = URL_VALIDATION_JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Validation job not found"}), 404
        if job.get("status") != "running":
            return jsonify({"error": "Validation job is not running"}), 400
        job["cancel_requested"] = True
        job["updated_at"] = utc_now_iso()

    return jsonify({"status": "ok", "job": serialize_url_validation_job(job)})


@app.route("/api/pipeline/urls/company/<int:bp_id>/set-url", methods=["POST"])
@login_required
def api_url_pipeline_set_url(bp_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404

    data = parse_json_payload()
    if data is None:
        return jsonify({"error": "Invalid JSON payload"}), 400

    raw_url = data.get("candidate_url")
    candidate_url = normalize_website_url(raw_url)
    if not candidate_url:
        return jsonify({"error": "candidate_url must be a valid domain or URL"}), 400

    source = normalize_text(data.get("source"), max_length=80) or "manual_review"
    status = normalize_text(data.get("status"), max_length=20) or "accepted"
    status = status.lower()
    if status not in ("pending", "accepted"):
        return jsonify({"error": "status must be pending or accepted"}), 400

    confidence_raw = data.get("confidence")
    confidence = normalize_optional_confidence(confidence_raw)
    if confidence_raw not in (None, "") and confidence is None:
        return jsonify({"error": "confidence must be between 0 and 100"}), 400
    if confidence is None:
        confidence = 90.0 if status == "accepted" else 65.0

    score_raw = data.get("score")
    score = None
    if score_raw not in (None, ""):
        try:
            score = float(score_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "score must be numeric"}), 400

    candidate = upsert_url_candidate(
        bp_id=bp_id,
        candidate_url=candidate_url,
        score=score,
        confidence=confidence,
        status="pending",
        source=source,
        reasons=["manual_set"],
    )
    if status == "accepted":
        candidate = accept_url_candidate(
            candidate["id"],
            request.current_user["id"],
            candidate_url=candidate_url,
            confidence=confidence,
            source=source,
        )
        if not candidate:
            return jsonify({"error": "Failed to accept URL"}), 400
    else:
        refresh_company_url_stage(bp_id, last_run_at=utc_now_iso())

    rows = build_url_pipeline_rows()
    return jsonify(sanitize({"status": "ok", "candidate": candidate, "summary": build_url_pipeline_summary(rows)}))


@app.route("/api/pipeline/urls/candidates/<int:candidate_id>/accept", methods=["POST"])
@login_required
def api_url_pipeline_accept(candidate_id):
    data = parse_json_payload() or {}
    confidence_raw = data.get("confidence")
    confidence = normalize_optional_confidence(confidence_raw)
    if confidence_raw not in (None, "") and confidence is None:
        return jsonify({"error": "confidence must be between 0 and 100"}), 400
    candidate_url = data.get("candidate_url")
    if candidate_url is not None:
        candidate_url = normalize_website_url(candidate_url)
        if candidate_url is None:
            return jsonify({"error": "candidate_url must be a valid URL"}), 400

    source = normalize_text(data.get("source"), max_length=80)

    updated = accept_url_candidate(
        candidate_id=candidate_id,
        user_id=request.current_user["id"],
        candidate_url=candidate_url,
        confidence=confidence,
        source=source,
    )
    if not updated:
        return jsonify({"error": "Candidate not found or invalid"}), 404
    rows = build_url_pipeline_rows()
    return jsonify(sanitize({"status": "ok", "candidate": updated, "summary": build_url_pipeline_summary(rows)}))


@app.route("/api/pipeline/urls/candidates/<int:candidate_id>/reject", methods=["POST"])
@login_required
def api_url_pipeline_reject(candidate_id):
    updated = reject_url_candidate(candidate_id, request.current_user["id"])
    if not updated:
        return jsonify({"error": "Candidate not found"}), 404
    rows = build_url_pipeline_rows()
    return jsonify(sanitize({"status": "ok", "candidate": updated, "summary": build_url_pipeline_summary(rows)}))


@app.route("/companies/<int:bp_id>")
@login_required
def company_page(bp_id):
    account = ACCOUNTS.get(bp_id)
    if not account:
        return redirect(url_for("index"))
    profile = get_active_scoring_profile(request.current_user)
    scored = score_account_with_profile(account, profile)
    return render_template("company.html", current_user=request.current_user, account=scored)


@app.route("/api/companies/<int:bp_id>/crm")
@login_required
def api_company_crm(bp_id):
    account = ACCOUNTS.get(bp_id)
    if not account:
        return jsonify({"error": "Company not found"}), 404
    profile = get_active_scoring_profile(request.current_user)
    scored = score_account_with_profile(account, profile)

    contacts = list_company_contacts(bp_id)
    touchpoints = list_company_touchpoints(bp_id)
    next_actions = list_company_next_actions(bp_id)

    open_actions = sum(1 for action in next_actions if action["status"] != "done")
    overdue_actions = 0
    today = utc_today_iso()
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


@app.route("/api/companies/<int:bp_id>/contacts", methods=["POST"])
@login_required
def api_create_company_contact(bp_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404

    data = parse_json_payload()
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


@app.route("/api/companies/<int:bp_id>/contacts/<int:contact_id>", methods=["PUT"])
@login_required
def api_update_company_contact(bp_id, contact_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404
    data = parse_json_payload()
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


@app.route("/api/companies/<int:bp_id>/contacts/<int:contact_id>", methods=["DELETE"])
@login_required
def api_delete_company_contact(bp_id, contact_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404
    ok = delete_company_contact(bp_id, contact_id)
    if not ok:
        return jsonify({"error": "Contact not found"}), 404
    return jsonify({"status": "ok"})


@app.route("/api/companies/<int:bp_id>/touchpoints", methods=["POST"])
@login_required
def api_create_company_touchpoint(bp_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404

    data = parse_json_payload()
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
    if contact_id and not contact_belongs_to_company(bp_id, contact_id):
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


@app.route("/api/companies/<int:bp_id>/touchpoints/<int:touchpoint_id>", methods=["PUT"])
@login_required
def api_update_company_touchpoint(bp_id, touchpoint_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404
    data = parse_json_payload()
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
        if value and not contact_belongs_to_company(bp_id, value):
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


@app.route("/api/companies/<int:bp_id>/touchpoints/<int:touchpoint_id>", methods=["DELETE"])
@login_required
def api_delete_company_touchpoint(bp_id, touchpoint_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404
    ok = delete_company_touchpoint(bp_id, touchpoint_id)
    if not ok:
        return jsonify({"error": "Touchpoint not found"}), 404
    return jsonify({"status": "ok"})


@app.route("/api/companies/<int:bp_id>/next-actions", methods=["POST"])
@login_required
def api_create_company_next_action(bp_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404

    data = parse_json_payload()
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
    if contact_id and not contact_belongs_to_company(bp_id, contact_id):
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
        completed_at=utc_now_iso() if status == "done" else None,
    )
    return jsonify(sanitize(next_action)), 201


@app.route("/api/companies/<int:bp_id>/next-actions/<int:action_id>", methods=["PUT"])
@login_required
def api_update_company_next_action(bp_id, action_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404
    data = parse_json_payload()
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
        fields["completed_at"] = utc_now_iso() if value == "done" else None
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
        if value and not contact_belongs_to_company(bp_id, value):
            return jsonify({"error": "contact_id does not belong to this company"}), 400
        fields["contact_id"] = value

    if not fields:
        return jsonify({"error": "No valid fields provided"}), 400

    updated = update_company_next_action(bp_id, action_id, fields)
    if not updated:
        return jsonify({"error": "Next action not found"}), 404
    return jsonify(sanitize(updated))


@app.route("/api/companies/<int:bp_id>/next-actions/<int:action_id>", methods=["DELETE"])
@login_required
def api_delete_company_next_action(bp_id, action_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404
    ok = delete_company_next_action(bp_id, action_id)
    if not ok:
        return jsonify({"error": "Next action not found"}), 404
    return jsonify({"status": "ok"})


@app.route("/api/accounts")
@login_required
def api_accounts():
    profile = get_active_scoring_profile(request.current_user)
    accounts_list = sorted(get_scored_accounts(profile), key=lambda a: a["score"], reverse=True)
    for i, account in enumerate(accounts_list):
        account["rank"] = i + 1
    return jsonify(sanitize(accounts_list))


@app.route("/api/accounts/<int:bp_id>")
@login_required
def api_account_detail(bp_id):
    profile = get_active_scoring_profile(request.current_user)
    account = ACCOUNTS.get(bp_id)
    if not account:
        return jsonify({"error": "Account not found"}), 404
    return jsonify(sanitize(score_account_with_profile(account, profile)))


@app.route("/api/accounts/<int:bp_id>/update", methods=["POST"])
@login_required
def api_update_account(bp_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Account not found"}), 404

    data = parse_json_payload()
    if data is None:
        return jsonify({"error": "Invalid JSON payload"}), 400

    account = ACCOUNTS[bp_id]
    db_fields = {}

    if "industry_override" in data:
        db_fields["industry_override"] = data["industry_override"]
        account["industry"] = data["industry_override"]
        account["industry_source"] = "manual"

    if "notes" in data:
        db_fields["notes"] = data["notes"]
        account["notes"] = data["notes"]

    if "starred" in data:
        db_fields["starred"] = 1 if data["starred"] else 0
        account["starred"] = bool(data["starred"])

    if "tags" in data:
        db_fields["tags"] = data["tags"]
        account["tags"] = data["tags"]

    if "target_list" in data:
        db_fields["target_list"] = 1 if data["target_list"] else 0
        account["target_list"] = bool(data["target_list"])

    if "employee_count" in data:
        db_fields["employee_count"] = data["employee_count"]
        account["employee_count"] = data["employee_count"]

    if "website" in data:
        db_fields["website"] = data["website"]
        account["website"] = data["website"]

    if "city" in data:
        db_fields["city"] = data["city"]
        account["city"] = data["city"]

    if db_fields:
        upsert_enrichment(bp_id, **db_fields)

    profile = get_active_scoring_profile(request.current_user)
    return jsonify(sanitize(score_account_with_profile(account, profile)))


@app.route("/api/stats")
@login_required
def api_stats():
    profile = get_active_scoring_profile(request.current_user)
    accounts = get_scored_accounts(profile)
    total = len(accounts)

    tiers = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}
    for a in accounts:
        tier = a.get("tier", "E")
        if tier not in tiers:
            tiers[tier] = 0
        tiers[tier] += 1

    industries = {}
    for a in accounts:
        industries[a["industry"]] = industries.get(a["industry"], 0) + 1

    sap_statuses = {}
    for a in accounts:
        sap_statuses[a["sap_status"]] = sap_statuses.get(a["sap_status"], 0) + 1

    score_dist = {f"{i}-{i + 9}": 0 for i in range(0, 100, 10)}
    score_dist["100"] = 0
    for a in accounts:
        score_value = float(a.get("score") or 0)
        if score_value >= 100:
            score_dist["100"] += 1
            continue
        bucket = min(max(int(score_value // 10) * 10, 0), 90)
        score_dist[f"{bucket}-{bucket + 9}"] += 1

    avg_score = sum(a["score"] for a in accounts) / total if total else 0
    starred_count = sum(1 for a in accounts if a["starred"])
    target_count = sum(1 for a in accounts if a["target_list"])

    return jsonify(
        {
            "total": total,
            "tiers": tiers,
            "industries": dict(sorted(industries.items(), key=lambda x: -x[1])),
            "sap_statuses": sap_statuses,
            "score_distribution": score_dist,
            "avg_score": round(avg_score, 1),
            "starred_count": starred_count,
            "target_count": target_count,
        }
    )


@app.route("/api/weights", methods=["GET"])
@login_required
def api_get_weights():
    profile = get_active_scoring_profile(request.current_user)
    return jsonify(profile["weights"])


@app.route("/api/weights", methods=["POST"])
@login_required
def api_set_weights():
    data = parse_json_payload()
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


@app.route("/api/industries")
@login_required
def api_industries():
    return jsonify(ALL_INDUSTRIES)


@app.route("/api/tags")
@login_required
def api_tags():
    return jsonify(get_all_tags())


@app.route("/api/industry-scores")
@login_required
def api_industry_scores():
    profile = get_active_scoring_profile(request.current_user)
    return jsonify(profile["industry_scores"])


@app.route("/api/scoring-profiles", methods=["GET"])
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


@app.route("/api/scoring-profiles", methods=["POST"])
@login_required
def api_create_scoring_profile():
    user = request.current_user
    data = parse_json_payload() or {}

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


@app.route("/api/scoring-profiles/<int:profile_id>", methods=["GET"])
@login_required
def api_get_scoring_profile(profile_id):
    user = request.current_user
    profile = get_profile_for_user(user, profile_id)
    if not profile:
        return jsonify({"error": "Profile not found"}), 404
    if profile["can_edit"]:
        profile["shares"] = get_scoring_profile_shares(user["id"], profile_id)
    return jsonify(sanitize(profile))


@app.route("/api/scoring-profiles/<int:profile_id>", methods=["PUT"])
@login_required
def api_update_scoring_profile(profile_id):
    user = request.current_user
    existing = get_profile_for_user(user, profile_id)
    if not existing:
        return jsonify({"error": "Profile not found"}), 404
    if not existing["can_edit"]:
        return jsonify({"error": "Read-only profile"}), 403

    data = parse_json_payload()
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


@app.route("/api/scoring-profiles/<int:profile_id>", methods=["DELETE"])
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


@app.route("/api/scoring-profiles/<int:profile_id>/select", methods=["POST"])
@login_required
def api_select_scoring_profile(profile_id):
    user = request.current_user
    profile = get_profile_for_user(user, profile_id)
    if not profile:
        return jsonify({"error": "Profile not found"}), 404
    session["active_scoring_profile_id"] = profile_id
    session["active_scoring_profile_manual"] = True
    return jsonify({"status": "ok", "active_profile_id": profile_id})


@app.route("/api/scoring-profiles/<int:profile_id>/share", methods=["POST"])
@login_required
def api_share_scoring_profile(profile_id):
    user = request.current_user
    profile = get_profile_for_user(user, profile_id)
    if not profile:
        return jsonify({"error": "Profile not found"}), 404
    if not profile["can_edit"]:
        return jsonify({"error": "Read-only profile"}), 403

    data = parse_json_payload()
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


@app.route("/api/scoring-profiles/<int:profile_id>/share/<int:target_user_id>", methods=["DELETE"])
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


@app.route("/api/scoring-profiles/<int:profile_id>/set-default", methods=["POST"])
@login_required
@super_admin_api_required
def api_set_default_scoring_profile(profile_id):
    ok = set_default_scoring_profile(profile_id)
    if not ok:
        return jsonify({"error": "Profile not found"}), 404
    return jsonify({"status": "ok", "default_profile_id": profile_id})


@app.route("/api/export/csv")
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


@app.route("/api/export/presentation")
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


def _init_app():
    global ACCOUNTS
    init_db()

    # Try loading from Excel first; fall back to cached DB
    try:
        ACCOUNTS = build_accounts()
        print(f"Loaded {len(ACCOUNTS)} accounts from Excel")
        save_cached_accounts(ACCOUNTS)
    except FileNotFoundError:
        print("Excel file not found, loading from cached DB...")
        cached = load_cached_accounts()
        if cached is None:
            raise RuntimeError(
                "No Excel file and no cached accounts in DB. "
                "Run the app locally with the Excel file first to populate the cache."
            )
        ACCOUNTS = cached
        # Re-apply enrichments on top of cached data
        enrichments = get_all_enrichments()
        for bp_id, account in ACCOUNTS.items():
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
        print(f"Loaded {len(ACCOUNTS)} accounts from cached DB")

    default_profile = get_default_scoring_profile()
    if default_profile:
        tier_counts = {}
        for account in get_scored_accounts(default_profile):
            tier_counts[account["tier"]] = tier_counts.get(account["tier"], 0) + 1
        print(f"Tiers (default profile): {tier_counts}")


_init_app()

if __name__ == "__main__":
    app.run(debug=True, port=5001)
