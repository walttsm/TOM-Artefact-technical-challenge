import logging
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from app.config import get_settings
from app.policy.manual import MANUAL_NAME, parse_manual
from app.store.database import connect_for_writing, create_schema
from app.store.loader import load_catalog, load_orders
from app.store.policy_sections import save_sections
from app.store.quality import Inconsistency, find_inconsistencies

logger = logging.getLogger(__name__)


class IngestionError(Exception):
    pass


def _check_foreign_keys(conn: sqlite3.Connection) -> None:
    # Enforcement stays off while loading, so this reports every broken reference at once.
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        details = ", ".join(f"{row['table']} row {row['rowid']}" for row in violations)
        raise IngestionError(f"Broken foreign keys: {details}")


def ingest(data_dir: Path, db_path: Path) -> list[Inconsistency]:
    with closing(connect_for_writing(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            create_schema(conn)
            load_catalog(conn, data_dir)
            load_orders(conn, data_dir)
            _check_foreign_keys(conn)
            save_sections(conn, map(asdict, parse_manual(data_dir / MANUAL_NAME)))
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        return find_inconsistencies(conn)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = get_settings()
    inconsistencies = ingest(settings.data_dir, settings.db_path)
    for item in inconsistencies:
        logger.warning("%s %s: %s -> %s", item.kind, item.record_id, item.detail, item.treatment)
    logger.info(
        "Ingested into %s, with %d data inconsistencies", settings.db_path, len(inconsistencies)
    )


if __name__ == "__main__":
    main()
