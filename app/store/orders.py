import sqlite3

_ORDER_WITH_CUSTOMER = """
    SELECT o.order_id, o.order_date, o.status, o.total_brl, o.payment_method,
           o.tracking_code, o.estimated_delivery, o.notes,
           c.customer_id, c.name AS customer_name, c.phone, c.email, c.email_is_shared
    FROM orders o
    JOIN customers c ON c.customer_id = o.customer_id
"""


def get_order_with_customer(conn: sqlite3.Connection, order_id: int) -> dict | None:
    row = conn.execute(f"{_ORDER_WITH_CUSTOMER} WHERE o.order_id = ?", (order_id,)).fetchone()
    return dict(row) if row else None


def find_orders_by_contact(conn: sqlite3.Connection, email: str, phone: str) -> list[dict]:
    """Expects the stored formats: lowercase email, digits-only phone."""
    rows = conn.execute(
        f"{_ORDER_WITH_CUSTOMER} WHERE c.email = ? AND c.phone = ? ORDER BY o.order_id",
        (email, phone),
    )
    return [dict(row) for row in rows]


def get_order_items(conn: sqlite3.Connection, order_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT i.product_id, p.name, i.quantity
        FROM order_items i
        JOIN products p ON p.product_id = i.product_id
        WHERE i.order_id = ?
        ORDER BY p.name
        """,
        (order_id,),
    )
    return [dict(row) for row in rows]
