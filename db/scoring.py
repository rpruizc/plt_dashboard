"""
Scoring profile CRUD, sharing, defaults, legacy weights.
"""

from db.core import (
    get_db,
    _serialize_json,
    _parse_json,
    _format_profile_row,
    _ensure_default_profile_setting_conn,
    _ensure_user_default_profile_conn,
    DEFAULT_PROFILE_SETTING_KEY,
)


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


def _is_profile_accessible_conn(conn, profile_id: int, user_id: int) -> bool:
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
