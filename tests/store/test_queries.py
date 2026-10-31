import sqlite3
from contextlib import closing

import pytest

from app.config import PROJECT_ROOT
from app.ingest import ingest
from app.store.catalog import find_promotions, search_products
from app.store.database import connect_for_writing, connect_read_only
from app.store.handoffs import create_handoff
from app.store.orders import find_orders_by_contact, get_order_items, get_order_with_customer

REAL_DATA = PROJECT_ROOT / "data"


@pytest.fixture
def conn(real_db):
    with closing(connect_read_only(real_db)) as connection:
        yield connection


@pytest.fixture
def writable_db(real_db, tmp_path):
    db_path = tmp_path / "test.db"
    ingest(REAL_DATA, db_path)
    return db_path


def names(rows):
    return {row["name"] for row in rows}


def test_read_connection_rejects_writes(conn):
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("DELETE FROM customers")


def test_read_connection_does_not_create_a_missing_database(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        connect_read_only(tmp_path / "missing.db")
    assert not (tmp_path / "missing.db").exists()


def test_accent_and_plural_forms_find_the_same_products(conn):
    results = [
        names(search_products(conn, [term], limit=None))
        for term in ("violao", "Violão", "violões", "VIOLOES")
    ]
    assert len(results[0]) >= 35
    assert all(result == results[0] for result in results)


def test_regular_plural_and_accent_free_name(conn):
    assert names(search_products(conn, ["guitarras"], limit=None)) == names(
        search_products(conn, ["guitarra"], limit=None)
    )
    assert "Tagima Dallas Tuner Aço Natural" in names(search_products(conn, ["dallas", "aco"]))


def test_all_words_must_match(conn):
    assert names(search_products(conn, ["takamine gd20"])) == {"Takamine GD20 Dreadnought Natural"}
    assert names(search_products(conn, ["takamine", "gibson"])) == set()


def test_category_filter(conn):
    rows = search_products(conn, [], category="ukulele", limit=None)
    assert len(rows) == 15
    assert {row["category"] for row in rows} == {"Ukuleles"}


def test_available_only_hides_unavailable_products(conn):
    assert names(search_products(conn, ["Giannini GF-3D"])) == {
        "Giannini GF-3D Dreadnought Sunburst"
    }
    assert search_products(conn, ["Giannini GF-3D"], available_only=True) == []
    assert search_products(conn, ["Shelby SN-7C"], available_only=True) == []


def test_limit_and_special_characters_in_terms(conn):
    assert len(search_products(conn, ["violao"], limit=3)) == 3
    assert search_products(conn, ["gib%on"]) == []
    assert search_products(conn, ["gibso_"]) == []
    assert search_products(conn, ["gibson"])
    assert search_products(conn, ["' OR 1=1 --"]) == []


def test_search_joins_only_the_active_discount(conn):
    active = search_products(conn, ["Crafter HT-100"])[0]
    assert (active["discount_percent"], active["promotion_name"]) == (18, "Desconto Exclusivo")
    expired = search_products(conn, ["Martin D-28 Dreadnought"])[0]
    assert expired["discount_percent"] is None
    assert expired["price_brl"] == 11499


def test_promotions_active_by_default(conn):
    rows = find_promotions(conn)
    assert len(rows) == 4
    assert all(row["is_active"] == 1 for row in rows)


def test_promotions_by_term(conn):
    assert find_promotions(conn, "Black Friday") == []
    expired = find_promotions(conn, "Black Friday", include_expired=True)
    assert {row["product_id"] for row in expired} == {102, 91, 113}
    assert all(row["is_active"] == 0 for row in expired)
    assert find_promotions(conn, "pascoa", include_expired=True)


def test_order_with_customer(conn):
    order = get_order_with_customer(conn, 8)
    assert order["email"] == "anacarol.ferreira@coldmail.com"
    assert order["phone"] == "67996547821"
    assert order["email_is_shared"] == 0
    assert get_order_with_customer(conn, 999) is None
    assert get_order_with_customer(conn, 10)["email_is_shared"] == 1


def test_orders_by_contact_needs_both_values_to_match(conn):
    email, phone = "pedro.oliveira@jmail.com", "67984321098"
    assert [row["order_id"] for row in find_orders_by_contact(conn, email, phone)] == [1, 19]
    assert find_orders_by_contact(conn, email, "67996547821") == []
    assert find_orders_by_contact(conn, "bruno.martins@jmail.com", "67994563456") == []


def test_order_items_have_no_prices(conn):
    items = get_order_items(conn, 15)
    assert {item["product_id"] for item in items} == {82, 106}
    assert all(set(item) == {"product_id", "name", "quantity"} for item in items)


def test_handoffs_survive_reingestion(writable_db):
    with closing(connect_for_writing(writable_db)) as conn:
        first = create_handoff(conn, "reclamacao", "Pedido atrasou", "ana@jmail.com")
    ingest(REAL_DATA, writable_db)
    with closing(connect_for_writing(writable_db)) as conn:
        assert conn.execute("SELECT summary FROM handoffs").fetchone()[0] == "Pedido atrasou"
        assert create_handoff(conn, "atendimento_humano", "Falar com alguém", "6799") == first + 1


def test_handoff_rejects_unknown_kind(writable_db):
    with closing(connect_for_writing(writable_db)) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            create_handoff(conn, "outro", "x", "y")
