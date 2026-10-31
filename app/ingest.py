import logging
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from app.config import get_settings
from app.policy.manual import MANUAL_NAME, parse_manual
from app.policy.pricing import is_installment_allowed
from app.store.database import connect_for_writing, create_schema
from app.store.loader import load_catalog, load_orders
from app.store.policy_sections import save_sections
from app.store.quality import Inconsistency, find_inconsistencies, list_credit_orders

logger = logging.getLogger(__name__)


class IngestionError(Exception):
    pass


def _check_foreign_keys(conn: sqlite3.Connection) -> None:
    # Enforcement stays off while loading, so this reports every broken reference at once.
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        details = ", ".join(f"{row['table']} row {row['rowid']}" for row in violations)
        raise IngestionError(f"Broken foreign keys: {details}")


def _orders_below_installment_minimum(conn: sqlite3.Connection) -> list[Inconsistency]:
    # Here and not in quality.py: the minimums belong to policy, which store can't import.
    found = []
    for order in list_credit_orders(conn):
        count = int(re.fullmatch(r"credit_(\d+)x", order["payment_method"])[1])
        if not is_installment_allowed(order["total_brl"], count):
            value = order["total_brl"] / count
            found.append(
                Inconsistency(
                    "below_installment_minimum",
                    order["order_id"],
                    f"{count}x of {value:.2f} on a total of {order['total_brl']:.2f}",
                )
            )
    return found


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
        return [*find_inconsistencies(conn), *_orders_below_installment_minimum(conn)]


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
