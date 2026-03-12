"""
CRM functions: company_contacts, company_touchpoints, company_next_actions CRUD.
"""

from db.core import get_db, _row_to_dict


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
