"""
SQLite database layer for account enrichments, auth, and scoring profiles.
"""

import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from scoring import DEFAULT_WEIGHTS, INDUSTRY_SCORES

DB_PATH = os.environ.get("PLT_DB_PATH", os.path.join(os.path.dirname(__file__), "territory.db"))
DEFAULT_PROFILE_NAME = "My Default"
DEFAULT_PROFILE_DESCRIPTION = "Initial scoring profile"
DEFAULT_PROFILE_SETTING_KEY = "default_scoring_profile_id"
_UNSET = object()


def get_db():
    """Get a database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _serialize_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True)


def _parse_json(value: str, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _format_profile_row(row: sqlite3.Row, current_user_id: int | None = None, default_profile_id: int | None = None) -> dict:
    profile = dict(row)
    profile["weights"] = _parse_json(profile.get("weights_json"), dict(DEFAULT_WEIGHTS))
    profile["industry_scores"] = _parse_json(profile.get("industry_scores_json"), dict(INDUSTRY_SCORES))
    profile.pop("weights_json", None)
    profile.pop("industry_scores_json", None)
    profile["can_edit"] = bool(profile["owner_user_id"] == current_user_id)
    profile["is_default"] = bool(default_profile_id is not None and profile["id"] == default_profile_id)
    return profile


def _ensure_default_profile_setting_conn(conn: sqlite3.Connection):
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        (DEFAULT_PROFILE_SETTING_KEY,),
    ).fetchone()

    default_id = int(row["value"]) if row and str(row["value"]).isdigit() else None
    if default_id is not None:
        exists = conn.execute("SELECT 1 FROM scoring_profiles WHERE id = ?", (default_id,)).fetchone()
        if exists:
            return

    fallback = conn.execute(
        "SELECT id FROM scoring_profiles ORDER BY created_at ASC LIMIT 1",
    ).fetchone()
    if fallback:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (DEFAULT_PROFILE_SETTING_KEY, str(fallback["id"])),
        )


def _ensure_user_default_profile_conn(conn: sqlite3.Connection, user_id: int):
    row = conn.execute(
        "SELECT id FROM scoring_profiles WHERE owner_user_id = ? ORDER BY created_at ASC LIMIT 1",
        (user_id,),
    ).fetchone()
    if row:
        return row["id"]

    conn.execute(
        """
        INSERT INTO scoring_profiles
            (owner_user_id, name, description, weights_json, industry_scores_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            user_id,
            DEFAULT_PROFILE_NAME,
            DEFAULT_PROFILE_DESCRIPTION,
            _serialize_json(dict(DEFAULT_WEIGHTS)),
            _serialize_json(dict(INDUSTRY_SCORES)),
        ),
    )
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def _ensure_super_admin_conn(conn: sqlite3.Connection):
    super_admin = conn.execute(
        "SELECT id FROM users WHERE role = 'super_admin' LIMIT 1",
    ).fetchone()
    if super_admin:
        return

    admin = conn.execute(
        "SELECT id FROM users WHERE role = 'admin' ORDER BY created_at ASC LIMIT 1",
    ).fetchone()
    if admin:
        conn.execute("UPDATE users SET role = 'super_admin' WHERE id = ?", (admin["id"],))
        return

    first_user = conn.execute(
        "SELECT id FROM users ORDER BY created_at ASC LIMIT 1",
    ).fetchone()
    if first_user:
        conn.execute("UPDATE users SET role = 'super_admin' WHERE id = ?", (first_user["id"],))


def init_db():
    """Create tables if they don't exist and apply lightweight migrations."""
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS account_enrichments (
            bp_id INTEGER PRIMARY KEY,
            industry_override TEXT,
            notes TEXT DEFAULT '',
            starred INTEGER DEFAULT 0,
            tags TEXT DEFAULT '[]',
            employee_count INTEGER,
            revenue REAL,
            website TEXT,
            city TEXT,
            target_list INTEGER DEFAULT 0,
            custom_data TEXT DEFAULT '{}',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS scoring_weights (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            industry_match REAL DEFAULT 0.40,
            company_size REAL DEFAULT 0.25,
            sap_relationship REAL DEFAULT 0.20,
            data_completeness REAL DEFAULT 0.15
        );
        INSERT OR IGNORE INTO scoring_weights (id) VALUES (1);

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            role TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS login_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            code TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS scoring_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            weights_json TEXT NOT NULL,
            industry_scores_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(owner_user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS scoring_profile_shares (
            profile_id INTEGER NOT NULL,
            shared_with_user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(profile_id, shared_with_user_id),
            FOREIGN KEY(profile_id) REFERENCES scoring_profiles(id) ON DELETE CASCADE,
            FOREIGN KEY(shared_with_user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS company_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bp_id INTEGER NOT NULL,
            full_name TEXT NOT NULL,
            job_title TEXT DEFAULT '',
            email TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            linkedin_url TEXT DEFAULT '',
            source TEXT DEFAULT '',
            confidence REAL,
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS company_touchpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bp_id INTEGER NOT NULL,
            contact_id INTEGER,
            touchpoint_date TEXT NOT NULL,
            touchpoint_type TEXT NOT NULL,
            summary TEXT DEFAULT '',
            outcome TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(contact_id) REFERENCES company_contacts(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS company_next_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bp_id INTEGER NOT NULL,
            contact_id INTEGER,
            title TEXT NOT NULL,
            details TEXT DEFAULT '',
            due_date TEXT,
            priority TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'open',
            owner_email TEXT DEFAULT '',
            completed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(contact_id) REFERENCES company_contacts(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS company_pipeline_status (
            bp_id INTEGER PRIMARY KEY,
            url_stage_status TEXT DEFAULT 'not_started',
            url_stage_confidence REAL,
            url_stage_notes TEXT DEFAULT '',
            url_last_run_at TIMESTAMP,
            contact_stage_status TEXT DEFAULT 'not_started',
            external_stage_status TEXT DEFAULT 'not_started',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS company_url_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bp_id INTEGER NOT NULL,
            candidate_url TEXT NOT NULL,
            normalized_domain TEXT DEFAULT '',
            score REAL,
            confidence REAL,
            status TEXT DEFAULT 'pending',
            source TEXT DEFAULT 'heuristic',
            reasons_json TEXT DEFAULT '[]',
            validated_by_user_id INTEGER,
            validated_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(bp_id, candidate_url),
            FOREIGN KEY(validated_by_user_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_scoring_profiles_owner ON scoring_profiles(owner_user_id);
        CREATE INDEX IF NOT EXISTS idx_scoring_profile_shares_user ON scoring_profile_shares(shared_with_user_id);
        CREATE INDEX IF NOT EXISTS idx_company_contacts_bp_id ON company_contacts(bp_id);
        CREATE INDEX IF NOT EXISTS idx_company_touchpoints_bp_id ON company_touchpoints(bp_id);
        CREATE INDEX IF NOT EXISTS idx_company_next_actions_bp_id ON company_next_actions(bp_id);
        CREATE INDEX IF NOT EXISTS idx_company_pipeline_status_url ON company_pipeline_status(url_stage_status);
        CREATE INDEX IF NOT EXISTS idx_company_url_candidates_bp_id ON company_url_candidates(bp_id);
        CREATE INDEX IF NOT EXISTS idx_company_url_candidates_status ON company_url_candidates(status);

        CREATE TABLE IF NOT EXISTS data_dictionary_comments (
            field_key TEXT PRIMARY KEY,
            comment TEXT DEFAULT '',
            updated_by TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS cached_accounts (
            bp_id INTEGER PRIMARY KEY,
            data_json TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    _ensure_super_admin_conn(conn)

    user_rows = conn.execute("SELECT id FROM users").fetchall()
    for user in user_rows:
        _ensure_user_default_profile_conn(conn, user["id"])

    _ensure_default_profile_setting_conn(conn)

    conn.commit()
    conn.close()


def get_enrichment(bp_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM account_enrichments WHERE bp_id = ?", (bp_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["tags"] = _parse_json(d.get("tags"), [])
    d["custom_data"] = _parse_json(d.get("custom_data"), {})
    return d


def get_all_enrichments() -> dict:
    conn = get_db()
    rows = conn.execute("SELECT * FROM account_enrichments").fetchall()
    conn.close()
    result = {}
    for row in rows:
        d = dict(row)
        d["tags"] = _parse_json(d.get("tags"), [])
        d["custom_data"] = _parse_json(d.get("custom_data"), {})
        result[d["bp_id"]] = d
    return result


def upsert_enrichment(bp_id: int, **fields):
    conn = get_db()
    existing = conn.execute("SELECT bp_id FROM account_enrichments WHERE bp_id = ?", (bp_id,)).fetchone()

    if "tags" in fields and isinstance(fields["tags"], list):
        fields["tags"] = _serialize_json(fields["tags"])
    if "custom_data" in fields and isinstance(fields["custom_data"], dict):
        fields["custom_data"] = _serialize_json(fields["custom_data"])

    if existing:
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [bp_id]
        conn.execute(
            f"UPDATE account_enrichments SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE bp_id = ?",
            values,
        )
    else:
        fields["bp_id"] = bp_id
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        conn.execute(
            f"INSERT INTO account_enrichments ({cols}) VALUES ({placeholders})",
            list(fields.values()),
        )

    conn.commit()
    conn.close()


def get_scoring_weights() -> dict:
    """Legacy helper used by older UI paths."""
    conn = get_db()
    row = conn.execute("SELECT * FROM scoring_weights WHERE id = 1").fetchone()
    conn.close()
    return {
        "industry_match": row["industry_match"],
        "company_size": row["company_size"],
        "sap_relationship": row["sap_relationship"],
        "data_completeness": row["data_completeness"],
    }


def update_scoring_weights(weights: dict):
    """Legacy helper used by older UI paths."""
    conn = get_db()
    conn.execute(
        """
        UPDATE scoring_weights
        SET industry_match = ?, company_size = ?, sap_relationship = ?, data_completeness = ?
        WHERE id = 1
        """,
        (
            weights["industry_match"],
            weights["company_size"],
            weights["sap_relationship"],
            weights["data_completeness"],
        ),
    )
    conn.commit()
    conn.close()


def get_all_tags() -> list[str]:
    """Get all unique tags used across accounts."""
    conn = get_db()
    rows = conn.execute("SELECT tags FROM account_enrichments WHERE tags != '[]'").fetchall()
    conn.close()
    all_tags = set()
    for row in rows:
        tags = _parse_json(row["tags"], [])
        all_tags.update(tags)
    return sorted(all_tags)


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if not row:
        return None
    return dict(row)


def list_company_contacts(bp_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT *
        FROM company_contacts
        WHERE bp_id = ?
        ORDER BY full_name COLLATE NOCASE ASC, id ASC
        """,
        (bp_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_company_contact(
    bp_id: int,
    full_name: str,
    job_title: str = "",
    email: str = "",
    phone: str = "",
    linkedin_url: str = "",
    source: str = "",
    confidence: float | None = None,
    notes: str = "",
) -> dict:
    conn = get_db()
    conn.execute(
        """
        INSERT INTO company_contacts
            (bp_id, full_name, job_title, email, phone, linkedin_url, source, confidence, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bp_id,
            full_name.strip(),
            job_title.strip(),
            email.strip(),
            phone.strip(),
            linkedin_url.strip(),
            source.strip(),
            confidence,
            notes.strip(),
        ),
    )
    contact_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    row = conn.execute("SELECT * FROM company_contacts WHERE id = ?", (contact_id,)).fetchone()
    conn.commit()
    conn.close()
    return dict(row)


def update_company_contact(bp_id: int, contact_id: int, fields: dict) -> dict | None:
    if not fields:
        return None

    conn = get_db()
    sets = ", ".join(f"{k} = ?" for k in fields.keys())
    values = list(fields.values()) + [contact_id, bp_id]
    result = conn.execute(
        f"""
        UPDATE company_contacts
        SET {sets},
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND bp_id = ?
        """,
        values,
    )
    if result.rowcount <= 0:
        conn.close()
        return None
    row = conn.execute(
        "SELECT * FROM company_contacts WHERE id = ? AND bp_id = ?",
        (contact_id, bp_id),
    ).fetchone()
    conn.commit()
    conn.close()
    return _row_to_dict(row)


def delete_company_contact(bp_id: int, contact_id: int) -> bool:
    conn = get_db()
    result = conn.execute(
        "DELETE FROM company_contacts WHERE id = ? AND bp_id = ?",
        (contact_id, bp_id),
    )
    conn.commit()
    conn.close()
    return result.rowcount > 0


def list_company_touchpoints(bp_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT
            tp.*,
            c.full_name AS contact_name
        FROM company_touchpoints tp
        LEFT JOIN company_contacts c ON c.id = tp.contact_id
        WHERE tp.bp_id = ?
        ORDER BY tp.touchpoint_date DESC, tp.id DESC
        """,
        (bp_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_company_touchpoint(
    bp_id: int,
    touchpoint_date: str,
    touchpoint_type: str,
    contact_id: int | None = None,
    summary: str = "",
    outcome: str = "",
    notes: str = "",
) -> dict:
    conn = get_db()
    conn.execute(
        """
        INSERT INTO company_touchpoints
            (bp_id, contact_id, touchpoint_date, touchpoint_type, summary, outcome, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bp_id,
            contact_id,
            touchpoint_date.strip(),
            touchpoint_type.strip(),
            summary.strip(),
            outcome.strip(),
            notes.strip(),
        ),
    )
    touchpoint_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    row = conn.execute(
        """
        SELECT
            tp.*,
            c.full_name AS contact_name
        FROM company_touchpoints tp
        LEFT JOIN company_contacts c ON c.id = tp.contact_id
        WHERE tp.id = ?
        """,
        (touchpoint_id,),
    ).fetchone()
    conn.commit()
    conn.close()
    return dict(row)


def update_company_touchpoint(bp_id: int, touchpoint_id: int, fields: dict) -> dict | None:
    if not fields:
        return None

    conn = get_db()
    sets = ", ".join(f"{k} = ?" for k in fields.keys())
    values = list(fields.values()) + [touchpoint_id, bp_id]
    result = conn.execute(
        f"""
        UPDATE company_touchpoints
        SET {sets},
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND bp_id = ?
        """,
        values,
    )
    if result.rowcount <= 0:
        conn.close()
        return None
    row = conn.execute(
        """
        SELECT
            tp.*,
            c.full_name AS contact_name
        FROM company_touchpoints tp
        LEFT JOIN company_contacts c ON c.id = tp.contact_id
        WHERE tp.id = ? AND tp.bp_id = ?
        """,
        (touchpoint_id, bp_id),
    ).fetchone()
    conn.commit()
    conn.close()
    return _row_to_dict(row)


def delete_company_touchpoint(bp_id: int, touchpoint_id: int) -> bool:
    conn = get_db()
    result = conn.execute(
        "DELETE FROM company_touchpoints WHERE id = ? AND bp_id = ?",
        (touchpoint_id, bp_id),
    )
    conn.commit()
    conn.close()
    return result.rowcount > 0


def list_company_next_actions(bp_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT
            na.*,
            c.full_name AS contact_name
        FROM company_next_actions na
        LEFT JOIN company_contacts c ON c.id = na.contact_id
        WHERE na.bp_id = ?
        ORDER BY
            CASE na.status
                WHEN 'open' THEN 0
                WHEN 'in_progress' THEN 1
                ELSE 2
            END ASC,
            na.due_date IS NULL ASC,
            na.due_date ASC,
            na.id DESC
        """,
        (bp_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_company_next_action(
    bp_id: int,
    title: str,
    details: str = "",
    due_date: str | None = None,
    priority: str = "medium",
    status: str = "open",
    owner_email: str = "",
    contact_id: int | None = None,
    completed_at: str | None = None,
) -> dict:
    conn = get_db()
    conn.execute(
        """
        INSERT INTO company_next_actions
            (bp_id, contact_id, title, details, due_date, priority, status, owner_email, completed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bp_id,
            contact_id,
            title.strip(),
            details.strip(),
            due_date.strip() if isinstance(due_date, str) and due_date.strip() else None,
            priority.strip(),
            status.strip(),
            owner_email.strip().lower(),
            completed_at,
        ),
    )
    action_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    row = conn.execute(
        """
        SELECT
            na.*,
            c.full_name AS contact_name
        FROM company_next_actions na
        LEFT JOIN company_contacts c ON c.id = na.contact_id
        WHERE na.id = ?
        """,
        (action_id,),
    ).fetchone()
    conn.commit()
    conn.close()
    return dict(row)


def update_company_next_action(bp_id: int, action_id: int, fields: dict) -> dict | None:
    if not fields:
        return None

    conn = get_db()
    sets = ", ".join(f"{k} = ?" for k in fields.keys())
    values = list(fields.values()) + [action_id, bp_id]
    result = conn.execute(
        f"""
        UPDATE company_next_actions
        SET {sets},
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND bp_id = ?
        """,
        values,
    )
    if result.rowcount <= 0:
        conn.close()
        return None
    row = conn.execute(
        """
        SELECT
            na.*,
            c.full_name AS contact_name
        FROM company_next_actions na
        LEFT JOIN company_contacts c ON c.id = na.contact_id
        WHERE na.id = ? AND na.bp_id = ?
        """,
        (action_id, bp_id),
    ).fetchone()
    conn.commit()
    conn.close()
    return _row_to_dict(row)


def delete_company_next_action(bp_id: int, action_id: int) -> bool:
    conn = get_db()
    result = conn.execute(
        "DELETE FROM company_next_actions WHERE id = ? AND bp_id = ?",
        (action_id, bp_id),
    )
    conn.commit()
    conn.close()
    return result.rowcount > 0


def _normalize_domain_from_url(url: str) -> str:
    cleaned = (url or "").strip().lower()
    if cleaned.startswith("https://"):
        cleaned = cleaned[8:]
    elif cleaned.startswith("http://"):
        cleaned = cleaned[7:]
    return cleaned.split("/", 1)[0].strip()


def get_company_pipeline_statuses() -> dict[int, dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM company_pipeline_status").fetchall()
    conn.close()
    return {int(row["bp_id"]): dict(row) for row in rows}


def upsert_company_pipeline_status(
    bp_id: int,
    url_stage_status: str | None | object = _UNSET,
    url_stage_confidence: float | None | object = _UNSET,
    url_stage_notes: str | None | object = _UNSET,
    url_last_run_at: str | None | object = _UNSET,
    contact_stage_status: str | None | object = _UNSET,
    external_stage_status: str | None | object = _UNSET,
):
    conn = get_db()
    existing = conn.execute(
        "SELECT bp_id FROM company_pipeline_status WHERE bp_id = ?",
        (bp_id,),
    ).fetchone()

    updates = {}
    if url_stage_status is not _UNSET:
        updates["url_stage_status"] = url_stage_status
    if url_stage_confidence is not _UNSET:
        updates["url_stage_confidence"] = (
            float(url_stage_confidence) if url_stage_confidence is not None else None
        )
    if url_stage_notes is not _UNSET:
        updates["url_stage_notes"] = "" if url_stage_notes is None else str(url_stage_notes)
    if url_last_run_at is not _UNSET:
        updates["url_last_run_at"] = url_last_run_at
    if contact_stage_status is not _UNSET:
        updates["contact_stage_status"] = contact_stage_status
    if external_stage_status is not _UNSET:
        updates["external_stage_status"] = external_stage_status

    if existing:
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates.keys())
            values = list(updates.values()) + [bp_id]
            conn.execute(
                f"""
                UPDATE company_pipeline_status
                SET {sets}, updated_at = CURRENT_TIMESTAMP
                WHERE bp_id = ?
                """,
                values,
            )
    else:
        payload = {
            "bp_id": bp_id,
            "url_stage_status": "not_started",
            "contact_stage_status": "not_started",
            "external_stage_status": "not_started",
        }
        payload.update(updates)
        cols = ", ".join(payload.keys())
        placeholders = ", ".join("?" for _ in payload)
        conn.execute(
            f"INSERT INTO company_pipeline_status ({cols}) VALUES ({placeholders})",
            list(payload.values()),
        )

    conn.commit()
    conn.close()


def list_url_candidates_for_company(bp_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT *
        FROM company_url_candidates
        WHERE bp_id = ?
        ORDER BY
            CASE status
                WHEN 'accepted' THEN 0
                WHEN 'pending' THEN 1
                ELSE 2
            END ASC,
            confidence DESC,
            score DESC,
            id ASC
        """,
        (bp_id,),
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        d["reasons"] = _parse_json(d.get("reasons_json"), [])
        d.pop("reasons_json", None)
        result.append(d)
    return result


def get_url_candidate_by_id(candidate_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM company_url_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["reasons"] = _parse_json(d.get("reasons_json"), [])
    d.pop("reasons_json", None)
    return d


def get_all_url_candidates() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT *
        FROM company_url_candidates
        ORDER BY bp_id ASC, confidence DESC, score DESC, id ASC
        """
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        d["reasons"] = _parse_json(d.get("reasons_json"), [])
        d.pop("reasons_json", None)
        result.append(d)
    return result


def upsert_url_candidate(
    bp_id: int,
    candidate_url: str,
    score: float | None = None,
    confidence: float | None = None,
    status: str = "pending",
    source: str = "heuristic",
    reasons: list[str] | None = None,
) -> dict:
    normalized_url = (candidate_url or "").strip()
    normalized_domain = _normalize_domain_from_url(normalized_url)
    reasons_json = _serialize_json(list(reasons or []))

    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM company_url_candidates WHERE bp_id = ? AND candidate_url = ?",
        (bp_id, normalized_url),
    ).fetchone()

    if existing:
        fields = {
            "normalized_domain": normalized_domain,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if score is not None:
            fields["score"] = float(score)
        if confidence is not None:
            fields["confidence"] = float(confidence)
        if status:
            fields["status"] = status
        if source:
            fields["source"] = source
        if reasons is not None:
            fields["reasons_json"] = reasons_json

        sets = ", ".join(f"{k} = ?" for k in fields.keys())
        values = list(fields.values()) + [existing["id"]]
        conn.execute(
            f"UPDATE company_url_candidates SET {sets} WHERE id = ?",
            values,
        )
        candidate_id = existing["id"]
    else:
        conn.execute(
            """
            INSERT INTO company_url_candidates
                (bp_id, candidate_url, normalized_domain, score, confidence, status, source, reasons_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bp_id,
                normalized_url,
                normalized_domain,
                score,
                confidence,
                status,
                source,
                reasons_json,
            ),
        )
        candidate_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    row = conn.execute(
        "SELECT * FROM company_url_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    conn.commit()
    conn.close()
    d = dict(row)
    d["reasons"] = _parse_json(d.get("reasons_json"), [])
    d.pop("reasons_json", None)
    return d


def update_url_candidate(candidate_id: int, fields: dict) -> dict | None:
    if not fields:
        return None
    conn = get_db()
    sets = ", ".join(f"{k} = ?" for k in fields.keys())
    values = list(fields.values()) + [candidate_id]
    result = conn.execute(
        f"""
        UPDATE company_url_candidates
        SET {sets},
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        values,
    )
    if result.rowcount <= 0:
        conn.close()
        return None
    row = conn.execute(
        "SELECT * FROM company_url_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    conn.commit()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["reasons"] = _parse_json(d.get("reasons_json"), [])
    d.pop("reasons_json", None)
    return d


def bulk_set_url_candidate_status_for_company(
    bp_id: int,
    status: str,
    validated_by_user_id: int | None = None,
):
    conn = get_db()
    conn.execute(
        """
        UPDATE company_url_candidates
        SET status = ?,
            validated_by_user_id = ?,
            validated_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE bp_id = ?
        """,
        (status, validated_by_user_id, bp_id),
    )
    conn.commit()
    conn.close()


def get_default_scoring_profile_id() -> int | None:
    conn = get_db()
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        (DEFAULT_PROFILE_SETTING_KEY,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return None


def set_default_scoring_profile(profile_id: int) -> bool:
    conn = get_db()
    exists = conn.execute(
        "SELECT id FROM scoring_profiles WHERE id = ?",
        (profile_id,),
    ).fetchone()
    if not exists:
        conn.close()
        return False

    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (DEFAULT_PROFILE_SETTING_KEY, str(profile_id)),
    )
    conn.commit()
    conn.close()
    return True


def _is_profile_accessible_conn(conn: sqlite3.Connection, profile_id: int, user_id: int) -> bool:
    default_id = get_default_scoring_profile_id()
    if default_id == profile_id:
        return True

    row = conn.execute(
        """
        SELECT sp.id
        FROM scoring_profiles sp
        LEFT JOIN scoring_profile_shares s
            ON s.profile_id = sp.id AND s.shared_with_user_id = ?
        WHERE sp.id = ? AND (sp.owner_user_id = ? OR s.shared_with_user_id IS NOT NULL)
        """,
        (user_id, profile_id, user_id),
    ).fetchone()
    return bool(row)


def get_scoring_profile_for_user(profile_id: int, user_id: int) -> dict | None:
    conn = get_db()
    if not _is_profile_accessible_conn(conn, profile_id, user_id):
        conn.close()
        return None

    row = conn.execute(
        """
        SELECT sp.*, u.email AS owner_email
        FROM scoring_profiles sp
        JOIN users u ON u.id = sp.owner_user_id
        WHERE sp.id = ?
        """,
        (profile_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return _format_profile_row(row, current_user_id=user_id, default_profile_id=get_default_scoring_profile_id())


def get_default_scoring_profile() -> dict | None:
    default_id = get_default_scoring_profile_id()
    if default_id is None:
        return None
    conn = get_db()
    row = conn.execute(
        """
        SELECT sp.*, u.email AS owner_email
        FROM scoring_profiles sp
        JOIN users u ON u.id = sp.owner_user_id
        WHERE sp.id = ?
        """,
        (default_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return _format_profile_row(row, current_user_id=None, default_profile_id=default_id)


def get_scoring_profile_by_id(profile_id: int, current_user_id: int | None = None) -> dict | None:
    conn = get_db()
    row = conn.execute(
        """
        SELECT sp.*, u.email AS owner_email
        FROM scoring_profiles sp
        JOIN users u ON u.id = sp.owner_user_id
        WHERE sp.id = ?
        """,
        (profile_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return _format_profile_row(
        row,
        current_user_id=current_user_id,
        default_profile_id=get_default_scoring_profile_id(),
    )


def list_scoring_profiles_for_user(user_id: int) -> list[dict]:
    default_profile_id = get_default_scoring_profile_id()
    conn = get_db()
    rows = conn.execute(
        """
        SELECT DISTINCT sp.*, u.email AS owner_email
        FROM scoring_profiles sp
        JOIN users u ON u.id = sp.owner_user_id
        LEFT JOIN scoring_profile_shares s
            ON s.profile_id = sp.id
        WHERE sp.owner_user_id = ?
           OR s.shared_with_user_id = ?
           OR sp.id = ?
        ORDER BY sp.updated_at DESC, sp.created_at DESC
        """,
        (user_id, user_id, default_profile_id if default_profile_id is not None else -1),
    ).fetchall()
    conn.close()
    return [
        _format_profile_row(row, current_user_id=user_id, default_profile_id=default_profile_id)
        for row in rows
    ]


def list_all_scoring_profiles(current_user_id: int | None = None) -> list[dict]:
    default_profile_id = get_default_scoring_profile_id()
    conn = get_db()
    rows = conn.execute(
        """
        SELECT sp.*, u.email AS owner_email
        FROM scoring_profiles sp
        JOIN users u ON u.id = sp.owner_user_id
        ORDER BY sp.updated_at DESC, sp.created_at DESC
        """
    ).fetchall()
    conn.close()
    return [
        _format_profile_row(row, current_user_id=current_user_id, default_profile_id=default_profile_id)
        for row in rows
    ]


def create_scoring_profile(
    owner_user_id: int,
    name: str,
    weights: dict,
    industry_scores: dict,
    description: str = "",
) -> dict:
    conn = get_db()
    conn.execute(
        """
        INSERT INTO scoring_profiles
            (owner_user_id, name, description, weights_json, industry_scores_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            owner_user_id,
            name.strip(),
            description.strip(),
            _serialize_json(weights),
            _serialize_json(industry_scores),
        ),
    )
    profile_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    row = conn.execute(
        """
        SELECT sp.*, u.email AS owner_email
        FROM scoring_profiles sp
        JOIN users u ON u.id = sp.owner_user_id
        WHERE sp.id = ?
        """,
        (profile_id,),
    ).fetchone()
    conn.commit()
    conn.close()
    return _format_profile_row(
        row,
        current_user_id=owner_user_id,
        default_profile_id=get_default_scoring_profile_id(),
    )


def update_scoring_profile(
    owner_user_id: int,
    profile_id: int,
    name: str,
    weights: dict,
    industry_scores: dict,
    description: str = "",
) -> bool:
    conn = get_db()
    result = conn.execute(
        """
        UPDATE scoring_profiles
        SET name = ?,
            description = ?,
            weights_json = ?,
            industry_scores_json = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND owner_user_id = ?
        """,
        (
            name.strip(),
            description.strip(),
            _serialize_json(weights),
            _serialize_json(industry_scores),
            profile_id,
            owner_user_id,
        ),
    )
    conn.commit()
    conn.close()
    return result.rowcount > 0


def delete_scoring_profile(owner_user_id: int, profile_id: int) -> tuple[bool, str | None]:
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM scoring_profiles WHERE id = ? AND owner_user_id = ?",
        (profile_id, owner_user_id),
    ).fetchone()
    if not row:
        conn.close()
        return False, "forbidden"

    count = conn.execute(
        "SELECT COUNT(*) AS c FROM scoring_profiles WHERE owner_user_id = ?",
        (owner_user_id,),
    ).fetchone()["c"]
    if count <= 1:
        conn.close()
        return False, "last_profile"

    conn.execute("DELETE FROM scoring_profiles WHERE id = ?", (profile_id,))
    _ensure_default_profile_setting_conn(conn)
    conn.commit()
    conn.close()
    return True, None


def duplicate_scoring_profile(source_profile_id: int, owner_user_id: int, new_name: str) -> dict | None:
    source = get_scoring_profile_for_user(source_profile_id, owner_user_id)
    if not source:
        return None
    return create_scoring_profile(
        owner_user_id=owner_user_id,
        name=new_name,
        weights=source["weights"],
        industry_scores=source["industry_scores"],
        description=source.get("description", ""),
    )


def share_scoring_profile(owner_user_id: int, profile_id: int, target_email: str) -> tuple[bool, str | None]:
    conn = get_db()
    profile = conn.execute(
        "SELECT id FROM scoring_profiles WHERE id = ? AND owner_user_id = ?",
        (profile_id, owner_user_id),
    ).fetchone()
    if not profile:
        conn.close()
        return False, "forbidden"

    target = conn.execute(
        "SELECT id FROM users WHERE email = ?",
        (target_email.strip().lower(),),
    ).fetchone()
    if not target:
        conn.close()
        return False, "user_not_found"

    if target["id"] == owner_user_id:
        conn.close()
        return False, "cannot_share_to_self"

    conn.execute(
        """
        INSERT OR IGNORE INTO scoring_profile_shares (profile_id, shared_with_user_id)
        VALUES (?, ?)
        """,
        (profile_id, target["id"]),
    )
    conn.commit()
    conn.close()
    return True, None


def unshare_scoring_profile(owner_user_id: int, profile_id: int, target_user_id: int) -> bool:
    conn = get_db()
    profile = conn.execute(
        "SELECT id FROM scoring_profiles WHERE id = ? AND owner_user_id = ?",
        (profile_id, owner_user_id),
    ).fetchone()
    if not profile:
        conn.close()
        return False

    result = conn.execute(
        "DELETE FROM scoring_profile_shares WHERE profile_id = ? AND shared_with_user_id = ?",
        (profile_id, target_user_id),
    )
    conn.commit()
    conn.close()
    return result.rowcount > 0


def get_scoring_profile_shares(owner_user_id: int, profile_id: int) -> list[dict]:
    conn = get_db()
    profile = conn.execute(
        "SELECT id FROM scoring_profiles WHERE id = ? AND owner_user_id = ?",
        (profile_id, owner_user_id),
    ).fetchone()
    if not profile:
        conn.close()
        return []

    rows = conn.execute(
        """
        SELECT u.id, u.email
        FROM scoring_profile_shares s
        JOIN users u ON u.id = s.shared_with_user_id
        WHERE s.profile_id = ?
        ORDER BY u.email ASC
        """,
        (profile_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_or_get_user(email: str) -> dict:
    """Create a user if new, or return existing. First user becomes super_admin."""
    conn = get_db()
    existing = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        _ensure_user_default_profile_conn(conn, existing["id"])
        _ensure_default_profile_setting_conn(conn)
        conn.commit()
        user = dict(existing)
        conn.close()
        return user

    count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    role = "super_admin" if count == 0 else "pending"
    conn.execute("INSERT INTO users (email, role) VALUES (?, ?)", (email, role))
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    _ensure_user_default_profile_conn(conn, user["id"])
    _ensure_default_profile_setting_conn(conn)
    conn.commit()
    conn.close()
    return dict(user)


def get_user_by_email(email: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_users() -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_user_role(email: str, role: str):
    conn = get_db()
    conn.execute("UPDATE users SET role = ? WHERE email = ?", (role, email))
    conn.commit()
    conn.close()


def delete_user(email: str):
    conn = get_db()
    conn.execute("DELETE FROM users WHERE email = ?", (email,))
    _ensure_super_admin_conn(conn)
    _ensure_default_profile_setting_conn(conn)
    conn.commit()
    conn.close()


def store_login_code(email: str, code: str, expires_at: datetime):
    conn = get_db()
    conn.execute("UPDATE login_codes SET used = 1 WHERE email = ? AND used = 0", (email,))
    conn.execute(
        "INSERT INTO login_codes (email, code, expires_at) VALUES (?, ?, ?)",
        (email, code, expires_at.isoformat()),
    )
    conn.commit()
    conn.close()


def verify_login_code(email: str, code: str) -> bool:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM login_codes WHERE email = ? AND code = ? AND used = 0 ORDER BY created_at DESC LIMIT 1",
        (email, code),
    ).fetchone()
    if not row:
        conn.close()
        return False

    expires_at = datetime.fromisoformat(row["expires_at"])
    if datetime.now() > expires_at:
        conn.close()
        return False

    conn.execute("UPDATE login_codes SET used = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return True


def generate_login_code() -> tuple[str, datetime]:
    """Generate a 6-digit code and expiry (5 minutes from now)."""
    code = f"{secrets.randbelow(1000000):06d}"
    expires_at = datetime.now() + timedelta(minutes=5)
    return code, expires_at


# ---------------------------------------------------------------------------
# Cached accounts (for deployment without Excel file)
# ---------------------------------------------------------------------------

def save_cached_accounts(accounts: dict):
    """Persist the full ACCOUNTS dict to SQLite so the app can run without the Excel file."""
    conn = get_db()
    conn.execute("DELETE FROM cached_accounts")
    for bp_id, data in accounts.items():
        conn.execute(
            "INSERT INTO cached_accounts (bp_id, data_json) VALUES (?, ?)",
            (bp_id, json.dumps(data, default=str)),
        )
    conn.commit()
    conn.close()
    print(f"Cached {len(accounts)} accounts to DB")


def load_cached_accounts() -> dict | None:
    """Load accounts from the cached_accounts table. Returns None if table is empty."""
    conn = get_db()
    rows = conn.execute("SELECT bp_id, data_json FROM cached_accounts").fetchall()
    conn.close()
    if not rows:
        return None
    accounts = {}
    for row in rows:
        data = json.loads(row["data_json"])
        # Restore proper types
        data["bp_id"] = int(row["bp_id"])
        data["starred"] = bool(data.get("starred", False))
        data["tags"] = data.get("tags", [])
        accounts[int(row["bp_id"])] = data
    return accounts


# ---------------------------------------------------------------------------
# Data dictionary comments
# ---------------------------------------------------------------------------

def get_all_dd_comments() -> dict:
    """Return {field_key: {comment, updated_by, updated_at}} for all saved comments."""
    conn = get_db()
    rows = conn.execute("SELECT field_key, comment, updated_by, updated_at FROM data_dictionary_comments").fetchall()
    conn.close()
    return {r["field_key"]: {"comment": r["comment"], "updated_by": r["updated_by"], "updated_at": r["updated_at"]} for r in rows}


def upsert_dd_comment(field_key: str, comment: str, updated_by: str = ""):
    """Insert or update a data dictionary comment."""
    conn = get_db()
    conn.execute(
        """INSERT INTO data_dictionary_comments (field_key, comment, updated_by, updated_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(field_key) DO UPDATE SET comment=excluded.comment, updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP""",
        (field_key, comment, updated_by),
    )
    conn.commit()
    conn.close()
