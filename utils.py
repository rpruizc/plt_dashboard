"""
Shared utility functions used across routes and app modules.
"""

import json
import math
import re
from datetime import datetime

from scoring import DEFAULT_WEIGHTS
from classifier import ALL_INDUSTRIES


def sanitize(obj):
    """Replace NaN/Infinity with None so JSON serialization works."""
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


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


URL_DOMAIN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$")


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
