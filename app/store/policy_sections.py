import sqlite3
from collections.abc import Iterable


def save_sections(conn: sqlite3.Connection, sections: Iterable[dict[str, str]]) -> None:
    conn.executemany(
        "INSERT INTO policy_sections (number, title, body) VALUES (:number, :title, :body)",
        sections,
    )


def list_sections(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT number, title FROM policy_sections ORDER BY position")
    return [dict(row) for row in rows]


def read_section(conn: sqlite3.Connection, number: str) -> list[dict]:
    """A section comes with its subsections: "5" also returns 5.1, 5.2 and 5.3."""
    rows = conn.execute(
        """
        SELECT number, title, body
        FROM policy_sections
        WHERE number = :number OR number LIKE :number || '.%'
        ORDER BY position
        """,
        {"number": number},
    )
    return [dict(row) for row in rows]
