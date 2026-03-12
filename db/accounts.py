"""
Account enrichment and cached account functions.
"""

import json

from db.core import get_db, _serialize_json, _parse_json


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
