"""
Core database utilities: connection, init, serialization helpers, constants.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

from scoring import DEFAULT_WEIGHTS, INDUSTRY_SCORES

DB_PATH = os.environ.get("PLT_DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "territory.db"))
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


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if not row:
        return None
    return dict(row)


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
