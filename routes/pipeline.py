"""
URL Pipeline blueprint — URL discovery, validation, and management routes.
"""

import json
import os
import re
import threading
import unicodedata
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote_plus
from urllib.request import Request, urlopen
from uuid import uuid4

from flask import Blueprint, jsonify, render_template, request

import state
from database import (
    bulk_set_url_candidate_status_for_company,
    get_all_url_candidates,
    get_company_pipeline_statuses,
    get_url_candidate_by_id,
    list_url_candidates_for_company,
    update_url_candidate,
    upsert_company_pipeline_status,
    upsert_enrichment,
    upsert_url_candidate,
)
from routes.auth import login_required
from utils import (
    normalize_optional_confidence,
    normalize_text,
    normalize_website_url,
    parse_json_object_from_text,
    sanitize,
)

pipeline_bp = Blueprint("pipeline", __name__)

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _parse_json_payload():
    """Parse JSON payload and return a dict (or None on invalid input)."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None
    return data


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# URL pipeline helper functions
# ---------------------------------------------------------------------------


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


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
    account = state.ACCOUNTS.get(bp_id)
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

    for account in state.ACCOUNTS.values():
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
    if bp_id in state.ACCOUNTS:
        state.ACCOUNTS[bp_id]["website"] = normalized


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
    now_iso = _utc_now_iso()

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
    now_iso = _utc_now_iso()
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

    current_website = normalize_website_url(state.ACCOUNTS.get(candidate["bp_id"], {}).get("website"), allow_blank=True)
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
        account = state.ACCOUNTS.get(row["bp_id"])
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
    now_iso = _utc_now_iso()

    with state.URL_VALIDATION_JOB_LOCK:
        job = state.URL_VALIDATION_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["started_at"] = now_iso
        job["updated_at"] = now_iso
        state.URL_VALIDATION_ACTIVE_JOB_ID = job_id
        targets = list(job.get("targets", []))

    for index, target in enumerate(targets, start=1):
        with state.URL_VALIDATION_JOB_LOCK:
            job = state.URL_VALIDATION_JOBS.get(job_id)
            if not job:
                return
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
                job["finished_at"] = _utc_now_iso()
                job["updated_at"] = job["finished_at"]
                state.URL_VALIDATION_ACTIVE_JOB_ID = None
                return
            job["current_index"] = index
            job["current_bp_id"] = target["bp_id"]
            job["current_company_name"] = target["company_name"]
            job["current_url"] = target["candidate_url"]
            job["updated_at"] = _utc_now_iso()

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
                refresh_company_url_stage(target["bp_id"], last_run_at=_utc_now_iso())
                outcome = "review"

            with state.URL_VALIDATION_JOB_LOCK:
                job = state.URL_VALIDATION_JOBS.get(job_id)
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
                job["updated_at"] = _utc_now_iso()

        except Exception as exc:
            with state.URL_VALIDATION_JOB_LOCK:
                job = state.URL_VALIDATION_JOBS.get(job_id)
                if not job:
                    return
                job["processed"] += 1
                job["errors"] += 1
                job["review"] += 1
                job["last_error"] = str(exc)[:500]
                job["updated_at"] = _utc_now_iso()

    with state.URL_VALIDATION_JOB_LOCK:
        job = state.URL_VALIDATION_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "completed" if not job.get("cancel_requested") else "cancelled"
        job["finished_at"] = _utc_now_iso()
        job["updated_at"] = job["finished_at"]
        job["current_bp_id"] = None
        job["current_company_name"] = ""
        job["current_url"] = ""
        state.URL_VALIDATION_ACTIVE_JOB_ID = None


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
    now_iso = _utc_now_iso()
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
    with state.URL_DISCOVERY_JOB_LOCK:
        job = state.URL_DISCOVERY_JOBS.get(job_id)
        if not job:
            return
        now_iso = _utc_now_iso()
        job["status"] = "running"
        job["started_at"] = now_iso
        job["updated_at"] = now_iso
        state.URL_DISCOVERY_ACTIVE_JOB_ID = job_id
        target_bp_ids = list(job.get("target_bp_ids", []))

    existing_candidates = get_all_url_candidates()
    existing_keys = {(c["bp_id"], c["candidate_url"]) for c in existing_candidates}

    for index, bp_id in enumerate(target_bp_ids, start=1):
        with state.URL_DISCOVERY_JOB_LOCK:
            job = state.URL_DISCOVERY_JOBS.get(job_id)
            if not job:
                return
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
                job["finished_at"] = _utc_now_iso()
                job["updated_at"] = job["finished_at"]
                job["current_bp_id"] = None
                job["current_company_name"] = ""
                state.URL_DISCOVERY_ACTIVE_JOB_ID = None
                return

        account = state.ACCOUNTS.get(bp_id)
        if not account:
            with state.URL_DISCOVERY_JOB_LOCK:
                job = state.URL_DISCOVERY_JOBS.get(job_id)
                if not job:
                    return
                job["processed"] += 1
                job["llm_errors"] += 1
                job["last_error"] = f"Account not found for bp_id {bp_id}"
                job["updated_at"] = _utc_now_iso()
            continue

        with state.URL_DISCOVERY_JOB_LOCK:
            job = state.URL_DISCOVERY_JOBS.get(job_id)
            if not job:
                return
            job["current_index"] = index
            job["current_bp_id"] = bp_id
            job["current_company_name"] = account.get("company_name", "")
            job["updated_at"] = _utc_now_iso()

        now_iso = _utc_now_iso()
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
                with state.URL_DISCOVERY_JOB_LOCK:
                    job = state.URL_DISCOVERY_JOBS.get(job_id)
                    if not job:
                        return
                    job["processed"] += 1
                    job["no_url_companies"] += 1
                    job["updated_at"] = _utc_now_iso()
                continue

            results = run_brave_web_search(query, max_results=job["max_results_to_review"])[: job["max_results_to_review"]]

            accepted = False
            best_probable_confidence = None
            for result_rank, result in enumerate(results, start=1):
                normalized_result_url = normalize_website_url(result.get("url"))
                if not normalized_result_url:
                    continue

                with state.URL_DISCOVERY_JOB_LOCK:
                    job = state.URL_DISCOVERY_JOBS.get(job_id)
                    if not job:
                        return
                    job["reviewed_results"] += 1
                    job["llm_calls"] += 1
                    job["updated_at"] = _utc_now_iso()
                    model = job["model"]

                try:
                    review, in_tokens, out_tokens = llm_review_brave_result_for_company(
                        account,
                        query=query,
                        result=result,
                        model=model,
                    )
                except Exception as exc:
                    with state.URL_DISCOVERY_JOB_LOCK:
                        job = state.URL_DISCOVERY_JOBS.get(job_id)
                        if not job:
                            return
                        job["llm_errors"] += 1
                        job["last_error"] = str(exc)[:500]
                        job["updated_at"] = _utc_now_iso()
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

                with state.URL_DISCOVERY_JOB_LOCK:
                    job = state.URL_DISCOVERY_JOBS.get(job_id)
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
                    job["updated_at"] = _utc_now_iso()

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
                        with state.URL_DISCOVERY_JOB_LOCK:
                            job = state.URL_DISCOVERY_JOBS.get(job_id)
                            if not job:
                                return
                            job["found_url_companies"] += 1
                            job["processed"] += 1
                            job["updated_at"] = _utc_now_iso()
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
                with state.URL_DISCOVERY_JOB_LOCK:
                    job = state.URL_DISCOVERY_JOBS.get(job_id)
                    if not job:
                        return
                    job["probable_url_companies"] += 1
                    job["processed"] += 1
                    job["updated_at"] = _utc_now_iso()
                continue

            upsert_company_pipeline_status(
                bp_id=bp_id,
                url_stage_status="no_url",
                url_stage_confidence=None,
                url_stage_notes="NF",
                url_last_run_at=now_iso,
            )
            with state.URL_DISCOVERY_JOB_LOCK:
                job = state.URL_DISCOVERY_JOBS.get(job_id)
                if not job:
                    return
                job["no_url_companies"] += 1
                job["processed"] += 1
                job["updated_at"] = _utc_now_iso()
        except Exception as exc:
            with state.URL_DISCOVERY_JOB_LOCK:
                job = state.URL_DISCOVERY_JOBS.get(job_id)
                if not job:
                    return
                job["processed"] += 1
                job["llm_errors"] += 1
                job["last_error"] = str(exc)[:500]
                job["updated_at"] = _utc_now_iso()

    with state.URL_DISCOVERY_JOB_LOCK:
        job = state.URL_DISCOVERY_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "completed" if not job.get("cancel_requested") else "cancelled"
        job["finished_at"] = _utc_now_iso()
        job["updated_at"] = job["finished_at"]
        job["current_index"] = 0
        job["current_bp_id"] = None
        job["current_company_name"] = ""
        state.URL_DISCOVERY_ACTIVE_JOB_ID = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@pipeline_bp.route("/pipeline/urls")
@login_required
def url_pipeline_page():
    return render_template("url_pipeline.html", current_user=request.current_user)


@pipeline_bp.route("/api/pipeline/urls/summary")
@login_required
def api_url_pipeline_summary():
    rows = build_url_pipeline_rows()
    return jsonify(sanitize(build_url_pipeline_summary(rows)))


@pipeline_bp.route("/api/pipeline/urls/queue")
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


@pipeline_bp.route("/api/pipeline/urls/discover", methods=["POST"])
@login_required
def api_url_pipeline_discover():
    return api_url_pipeline_discover_start()


@pipeline_bp.route("/api/pipeline/urls/discover-job/start", methods=["POST"])
@login_required
def api_url_pipeline_discover_start():
    data = _parse_json_payload() or {}
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

    with state.URL_DISCOVERY_JOB_LOCK:
        if state.URL_DISCOVERY_ACTIVE_JOB_ID:
            active_job = state.URL_DISCOVERY_JOBS.get(state.URL_DISCOVERY_ACTIVE_JOB_ID)
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

    ordered_accounts = sorted(state.ACCOUNTS.values(), key=lambda a: (a.get("company_name") or "").lower())
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

    now_iso = _utc_now_iso()
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

    with state.URL_DISCOVERY_JOB_LOCK:
        state.URL_DISCOVERY_JOBS[job_id] = job

    worker = threading.Thread(target=run_url_discovery_job, args=(job_id,), daemon=True)
    worker.start()

    return jsonify(sanitize({"status": "ok", "job": serialize_url_discovery_job(job)})), 202


@pipeline_bp.route("/api/pipeline/urls/discover-job")
@login_required
def api_url_pipeline_discover_status():
    requested_job_id = request.args.get("job_id", "").strip()

    with state.URL_DISCOVERY_JOB_LOCK:
        if requested_job_id:
            job = state.URL_DISCOVERY_JOBS.get(requested_job_id)
        elif state.URL_DISCOVERY_ACTIVE_JOB_ID:
            job = state.URL_DISCOVERY_JOBS.get(state.URL_DISCOVERY_ACTIVE_JOB_ID)
        else:
            jobs = list(state.URL_DISCOVERY_JOBS.values())
            jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
            job = jobs[0] if jobs else None

    if not job:
        return jsonify({"job": None, "status": "ok"})
    payload = {"status": "ok", "job": serialize_url_discovery_job(job)}
    if job.get("status") in ("completed", "cancelled", "failed"):
        rows = build_url_pipeline_rows()
        payload["summary"] = build_url_pipeline_summary(rows)
    return jsonify(sanitize(payload))


@pipeline_bp.route("/api/pipeline/urls/discover-job/<job_id>/cancel", methods=["POST"])
@login_required
def api_url_pipeline_discover_cancel(job_id):
    with state.URL_DISCOVERY_JOB_LOCK:
        job = state.URL_DISCOVERY_JOBS.get(job_id)
        if not job:
            return jsonify({"error": "URL discovery job not found"}), 404
        if job.get("status") != "running":
            return jsonify({"error": "URL discovery job is not running"}), 400
        job["cancel_requested"] = True
        job["updated_at"] = _utc_now_iso()

    return jsonify({"status": "ok", "job": serialize_url_discovery_job(job)})


@pipeline_bp.route("/api/pipeline/urls/auto-accept", methods=["POST"])
@login_required
def api_url_pipeline_auto_accept():
    data = _parse_json_payload() or {}
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


@pipeline_bp.route("/api/pipeline/urls/validate-job/start", methods=["POST"])
@login_required
def api_url_pipeline_validate_start():
    data = _parse_json_payload() or {}
    model = normalize_text(data.get("model"), max_length=80) or URL_VALIDATION_DEFAULT_MODEL

    max_companies_raw = data.get("max_companies", 120)
    try:
        max_companies = int(max_companies_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "max_companies must be an integer"}), 400
    if max_companies <= 0:
        return jsonify({"error": "max_companies must be greater than zero"}), 400
    max_companies = min(max_companies, 2000)

    with state.URL_VALIDATION_JOB_LOCK:
        if state.URL_VALIDATION_ACTIVE_JOB_ID:
            active_job = state.URL_VALIDATION_JOBS.get(state.URL_VALIDATION_ACTIVE_JOB_ID)
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

    now_iso = _utc_now_iso()
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

    with state.URL_VALIDATION_JOB_LOCK:
        state.URL_VALIDATION_JOBS[job_id] = job

    worker = threading.Thread(target=run_url_validation_job, args=(job_id,), daemon=True)
    worker.start()

    return jsonify(sanitize({"status": "ok", "job": serialize_url_validation_job(job)})), 202


@pipeline_bp.route("/api/pipeline/urls/validate-job")
@login_required
def api_url_pipeline_validate_status():
    requested_job_id = request.args.get("job_id", "").strip()

    with state.URL_VALIDATION_JOB_LOCK:
        if requested_job_id:
            job = state.URL_VALIDATION_JOBS.get(requested_job_id)
        elif state.URL_VALIDATION_ACTIVE_JOB_ID:
            job = state.URL_VALIDATION_JOBS.get(state.URL_VALIDATION_ACTIVE_JOB_ID)
        else:
            jobs = list(state.URL_VALIDATION_JOBS.values())
            jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
            job = jobs[0] if jobs else None

    if not job:
        return jsonify({"job": None, "status": "ok"})
    return jsonify({"status": "ok", "job": serialize_url_validation_job(job)})


@pipeline_bp.route("/api/pipeline/urls/validate-job/<job_id>/cancel", methods=["POST"])
@login_required
def api_url_pipeline_validate_cancel(job_id):
    with state.URL_VALIDATION_JOB_LOCK:
        job = state.URL_VALIDATION_JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Validation job not found"}), 404
        if job.get("status") != "running":
            return jsonify({"error": "Validation job is not running"}), 400
        job["cancel_requested"] = True
        job["updated_at"] = _utc_now_iso()

    return jsonify({"status": "ok", "job": serialize_url_validation_job(job)})


@pipeline_bp.route("/api/pipeline/urls/company/<int:bp_id>/set-url", methods=["POST"])
@login_required
def api_url_pipeline_set_url(bp_id):
    if bp_id not in state.ACCOUNTS:
        return jsonify({"error": "Company not found"}), 404

    data = _parse_json_payload()
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
        refresh_company_url_stage(bp_id, last_run_at=_utc_now_iso())

    rows = build_url_pipeline_rows()
    return jsonify(sanitize({"status": "ok", "candidate": candidate, "summary": build_url_pipeline_summary(rows)}))


@pipeline_bp.route("/api/pipeline/urls/candidates/<int:candidate_id>/accept", methods=["POST"])
@login_required
def api_url_pipeline_accept(candidate_id):
    data = _parse_json_payload() or {}
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


@pipeline_bp.route("/api/pipeline/urls/candidates/<int:candidate_id>/reject", methods=["POST"])
@login_required
def api_url_pipeline_reject(candidate_id):
    updated = reject_url_candidate(candidate_id, request.current_user["id"])
    if not updated:
        return jsonify({"error": "Candidate not found"}), 404
    rows = build_url_pipeline_rows()
    return jsonify(sanitize({"status": "ok", "candidate": updated, "summary": build_url_pipeline_summary(rows)}))
