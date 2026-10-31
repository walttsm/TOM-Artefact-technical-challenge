import sqlite3


def create_handoff(conn: sqlite3.Connection, kind: str, summary: str, contact: str) -> int:
    cursor = conn.execute(
        "INSERT INTO handoffs (kind, summary, contact) VALUES (?, ?, ?)",
        (kind, summary, contact),
    )
    return cursor.lastrowid
