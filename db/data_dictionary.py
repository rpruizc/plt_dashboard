"""
Data dictionary comment functions.
"""

from db.core import get_db


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
