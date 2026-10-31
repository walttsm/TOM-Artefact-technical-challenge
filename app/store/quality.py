import sqlite3
from dataclasses import dataclass

# The justifications are in decisions.md.
TREATMENTS = {
    "order_total_differs": "keep the recorded total; never show item prices on an order",
    "unavailable_with_stock": "keep the stock; status decides availability",
    "empty_category": "keep the category; say it has no products right now",
    "shared_email": "keep both customers; the email alone proves no identity, so require the phone",
    "ordered_before_release": "keep the order; report its recorded status and promise no delivery",
    "repeated_promotion": "keep both rows; show expired promotions once, with no percentage",
    "below_installment_minimum": "keep the order; the current minimums apply only to new quotes",
}


@dataclass(frozen=True)
class Inconsistency:
    kind: str
    record_id: int | str
    detail: str

    @property
    def treatment(self) -> str:
        return TREATMENTS[self.kind]


def _order_total_differs(conn: sqlite3.Connection) -> list[Inconsistency]:
    rows = conn.execute(
        """
        SELECT o.order_id, o.total_brl, COALESCE(SUM(i.quantity * p.price_brl), 0) AS items_total
        FROM orders o
        LEFT JOIN order_items i ON i.order_id = o.order_id
        LEFT JOIN products p ON p.product_id = i.product_id
        GROUP BY o.order_id
        HAVING ABS(o.total_brl - items_total) >= 0.01
        ORDER BY o.order_id
        """
    )
    return [
        Inconsistency(
            "order_total_differs",
            row["order_id"],
            f"total {row['total_brl']:.2f}, items at current prices {row['items_total']:.2f}",
        )
        for row in rows
    ]


def _unavailable_with_stock(conn: sqlite3.Connection) -> list[Inconsistency]:
    rows = conn.execute(
        """
        SELECT product_id, status, stock_quantity
        FROM products
        WHERE status <> 'active' AND stock_quantity > 0
        ORDER BY product_id
        """
    )
    return [
        Inconsistency(
            "unavailable_with_stock",
            row["product_id"],
            f"status {row['status']}, stock {row['stock_quantity']}",
        )
        for row in rows
    ]


def _empty_categories(conn: sqlite3.Connection) -> list[Inconsistency]:
    rows = conn.execute(
        """
        SELECT category_id, name
        FROM categories
        WHERE category_id NOT IN (SELECT category_id FROM products)
        ORDER BY category_id
        """
    )
    return [Inconsistency("empty_category", row["category_id"], row["name"]) for row in rows]


def _shared_emails(conn: sqlite3.Connection) -> list[Inconsistency]:
    rows = conn.execute(
        """
        SELECT email, GROUP_CONCAT(customer_id, ', ') AS customer_ids
        FROM customers
        GROUP BY email
        HAVING COUNT(*) > 1
        ORDER BY email
        """
    )
    return [
        Inconsistency("shared_email", row["email"], f"customers {row['customer_ids']}")
        for row in rows
    ]


def _ordered_before_release(conn: sqlite3.Connection) -> list[Inconsistency]:
    rows = conn.execute(
        """
        SELECT i.order_id, i.product_id
        FROM order_items i
        JOIN products p ON p.product_id = i.product_id
        WHERE p.status = 'coming_soon'
        ORDER BY i.order_id, i.product_id
        """
    )
    return [
        Inconsistency(
            "ordered_before_release", row["order_id"], f"product {row['product_id']} is coming_soon"
        )
        for row in rows
    ]


def _repeated_promotions(conn: sqlite3.Connection) -> list[Inconsistency]:
    rows = conn.execute(
        """
        SELECT product_id, description, GROUP_CONCAT(discount_percent, '%, ') AS discounts
        FROM promotions
        GROUP BY product_id, description
        HAVING COUNT(*) > 1
        ORDER BY product_id
        """
    )
    return [
        Inconsistency(
            "repeated_promotion", row["product_id"], f"{row['description']}: {row['discounts']}%"
        )
        for row in rows
    ]


def list_credit_orders(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT order_id, total_brl, payment_method
        FROM orders
        WHERE payment_method LIKE 'credit\\_%' ESCAPE '\\'
        ORDER BY order_id
        """
    )
    return [dict(row) for row in rows]


def find_inconsistencies(conn: sqlite3.Connection) -> list[Inconsistency]:
    return [
        *_order_total_differs(conn),
        *_unavailable_with_stock(conn),
        *_empty_categories(conn),
        *_shared_emails(conn),
        *_ordered_before_release(conn),
        *_repeated_promotions(conn),
    ]
