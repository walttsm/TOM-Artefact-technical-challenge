import sqlite3

from app.store.loader import to_search_text


def _term_variants(term: str) -> list[str]:
    # Substring matching already covers regular plurals once the "s" is gone.
    # "-ão" words need both forms, because "violao" is not inside "violoes".
    text = to_search_text(term)
    for ending in ("oes", "aes", "aos"):
        if text.endswith(ending):
            return [text[:-3] + "ao", text]
    if text.endswith("ao"):
        return [text, text[:-2] + "oes", text[:-2] + "aes"]
    if len(text) > 3 and text.endswith("s") and not text.endswith("ss"):
        return [text[:-1]]
    return [text]


def _like_pattern(variant: str) -> str:
    escaped = variant.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _match_clause(terms: list[str], searched: str) -> tuple[str, list[str]]:
    """Every word must match, in any of its variants."""
    clauses: list[str] = []
    params: list[str] = []
    for word in (word for term in terms for word in term.split()):
        patterns = [_like_pattern(variant) for variant in _term_variants(word)]
        clauses.append("(" + " OR ".join(f"{searched} LIKE ? ESCAPE '\\'" for _ in patterns) + ")")
        params.extend(patterns)
    return " AND ".join(clauses) or "1 = 1", params


def search_products(
    conn: sqlite3.Connection,
    terms: list[str],
    category: str | None = None,
    available_only: bool = False,
    limit: int | None = 10,
) -> list[dict]:
    """Matches on name, description and category. limit=None returns every match."""
    searched = "(p.name_search || ' ' || p.description_search || ' ' || c.name_search)"
    where, params = _match_clause(terms, searched)
    if category:
        category_where, category_params = _match_clause([category], "c.name_search")
        where += f" AND {category_where}"
        params += category_params
    if available_only:
        where += " AND p.status = 'active' AND p.stock_quantity > 0"
    rows = conn.execute(
        f"""
        SELECT p.product_id, p.name, c.name AS category, p.description, p.price_brl,
               p.stock_quantity, p.status, p.specs,
               pr.discount_percent, pr.description AS promotion_name
        FROM products p
        JOIN categories c ON c.category_id = p.category_id
        LEFT JOIN promotions pr ON pr.product_id = p.product_id AND pr.is_active = 1
        WHERE {where}
        ORDER BY p.name
        LIMIT ?
        """,
        [*params, -1 if limit is None else limit],
    )
    return [dict(row) for row in rows]


def find_promotions(
    conn: sqlite3.Connection, term: str | None = None, include_expired: bool = False
) -> list[dict]:
    searched = "(p.name_search || ' ' || pr.description_search)"
    where, params = _match_clause([term] if term else [], searched)
    if not include_expired:
        where += " AND pr.is_active = 1"
    rows = conn.execute(
        f"""
        SELECT pr.promotion_id, pr.description AS promotion_name, pr.discount_percent,
               pr.is_active, p.product_id, p.name AS product_name, p.price_brl,
               p.stock_quantity, p.status,
               active.discount_percent AS active_discount_percent,
               active.description AS active_promotion_name
        FROM promotions pr
        JOIN products p ON p.product_id = pr.product_id
        LEFT JOIN promotions active
            ON active.product_id = p.product_id AND active.is_active = 1
        WHERE {where}
        ORDER BY pr.is_active DESC, pr.description, p.name, pr.promotion_id
        """,
        params,
    )
    return [dict(row) for row in rows]
