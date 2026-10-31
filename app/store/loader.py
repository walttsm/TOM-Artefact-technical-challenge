import csv
import re
import sqlite3
import unicodedata
from collections import Counter
from pathlib import Path

CSV_NAME = "desafio_tecnico_ai_eng - {table}.csv"


def to_search_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _read_rows(data_dir: Path, table: str) -> list[dict[str, str]]:
    path = data_dir / CSV_NAME.format(table=table)
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _with_search_columns(row: dict[str, str]) -> dict[str, str]:
    return {
        **row,
        "name_search": to_search_text(row["name"]),
        "description_search": to_search_text(row["description"]),
    }


def _clean_customers(rows: list[dict[str, str]]) -> list[dict[str, str | bool]]:
    email_counts = Counter(row["email"].lower() for row in rows)
    return [
        {
            **row,
            "phone": re.sub(r"\D", "", row["phone"]),
            "email": row["email"].lower(),
            "email_is_shared": email_counts[row["email"].lower()] > 1,
        }
        for row in rows
    ]


def _clean_order(row: dict[str, str]) -> dict[str, str | None]:
    return {
        **row,
        "tracking_code": row["tracking_code"] or None,
        "estimated_delivery": row["estimated_delivery"] or None,
        "notes": row["notes"] or None,
    }


def load_catalog(conn: sqlite3.Connection, data_dir: Path) -> None:
    conn.executemany(
        """
        INSERT INTO categories (category_id, name, description, name_search, description_search)
        VALUES (:category_id, :name, :description, :name_search, :description_search)
        """,
        map(_with_search_columns, _read_rows(data_dir, "categories")),
    )
    conn.executemany(
        """
        INSERT INTO products (
            product_id, category_id, name, description, price_brl, stock_quantity,
            status, specs, created_at, name_search, description_search
        )
        VALUES (
            :product_id, :category_id, :name, :description, :price_brl, :stock_quantity,
            :status, :specs, :created_at, :name_search, :description_search
        )
        """,
        map(_with_search_columns, _read_rows(data_dir, "products")),
    )
    conn.executemany(
        """
        INSERT INTO promotions (promotion_id, product_id, discount_percent, description, is_active)
        VALUES (:promotion_id, :product_id, :discount_percent, :description, :is_active)
        """,
        _read_rows(data_dir, "promotions"),
    )


def load_orders(conn: sqlite3.Connection, data_dir: Path) -> None:
    conn.executemany(
        """
        INSERT INTO customers (customer_id, name, phone, email, city, email_is_shared)
        VALUES (:customer_id, :name, :phone, :email, :city, :email_is_shared)
        """,
        _clean_customers(_read_rows(data_dir, "customers")),
    )
    conn.executemany(
        """
        INSERT INTO orders (
            order_id, customer_id, order_date, status, total_brl,
            payment_method, tracking_code, estimated_delivery, notes
        )
        VALUES (
            :order_id, :customer_id, :order_date, :status, :total_brl,
            :payment_method, :tracking_code, :estimated_delivery, :notes
        )
        """,
        map(_clean_order, _read_rows(data_dir, "orders")),
    )
    conn.executemany(
        """
        INSERT INTO order_items (order_id, product_id, quantity)
        VALUES (:order_id, :product_id, :quantity)
        """,
        _read_rows(data_dir, "order_items"),
    )
