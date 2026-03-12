"""
Pipeline status and URL candidate functions.
"""

from datetime import datetime, timezone

from db.core import get_db, _serialize_json, _parse_json, _row_to_dict, _UNSET


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
