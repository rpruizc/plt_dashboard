"""
Shared application state — mutable globals imported by routes and app modules.
"""

import threading

# Global account data loaded once. Scoring is computed per request from active profile.
ACCOUNTS: dict[int, dict] = {}

# URL discovery background jobs
URL_DISCOVERY_JOBS: dict[str, dict] = {}
URL_DISCOVERY_JOB_LOCK = threading.Lock()
URL_DISCOVERY_ACTIVE_JOB_ID: str | None = None

# URL validation background jobs
URL_VALIDATION_JOBS: dict[str, dict] = {}
URL_VALIDATION_JOB_LOCK = threading.Lock()
URL_VALIDATION_ACTIVE_JOB_ID: str | None = None
