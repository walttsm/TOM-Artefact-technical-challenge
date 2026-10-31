from contextlib import closing
from decimal import Decimal

import pytest

from app.policy.availability import product_situation, shows_price
from app.policy.identity import can_view_order, normalize_phone
from app.policy.pricing import (
    format_brl,
    installment_plan,
    is_installment_allowed,
    price_product,
)
from app.store.catalog import search_products
from app.store.database import connect_read_only
from app.store.orders import get_order_with_customer


@pytest.mark.parametrize(
    ("total", "count", "value"),
    [
        (30, 1, "30.00"),
        (120, 2, "60.00"),
        (320, 4, "80.00"),
        (599, 6, "99.83"),
        (700, 7, "100.00"),
        (2199, 12, "183.25"),
    ],
)
def test_longest_installment_plan(total, count, value):
    longest = installment_plan(total)[-1]
    assert longest.count == count
    assert longest.value == Decimal(value)


def test_plan_lists_every_option_and_skips_those_below_the_tier():
    assert [option.count for option in installment_plan(320)] == [1, 2, 3, 4]
    assert [option.count for option in installment_plan(149.99)] == [1, 2]
    assert [option.count for option in installment_plan(150)] == [1, 2, 3]


def test_minimum_uses_the_exact_division_not_the_rounded_value():
    # 479.99 / 6 is 79.998, which would pass the R$ 80 minimum if rounded to cents first.
    assert not is_installment_allowed(479.99, 6)
    assert is_installment_allowed(480, 6)


@pytest.mark.parametrize("total", [0, -10])
def test_plan_rejects_invalid_totals(total):
    with pytest.raises(ValueError):
        installment_plan(total)


def test_installment_count_outside_the_range_is_not_allowed():
    assert not is_installment_allowed(100000, 13)
    assert not is_installment_allowed(100000, 0)


def test_pix_price_without_promotion():
    price = price_product(2199)
    assert price.final_price == Decimal("2199.00")
    assert price.pix_price == Decimal("2089.05")
    assert price.discount_percent is None


def test_promotion_cancels_pix():
    price = price_product(2399, 18, "Desconto Exclusivo")
    assert price.list_price == Decimal("2399.00")
    assert price.final_price == Decimal("1967.18")
    assert price.pix_price is None
    assert price.promotion_name == "Desconto Exclusivo"


def test_stored_float_prices_stay_exact():
    assert price_product(599.9).list_price == Decimal("599.90")
    assert price_product(599.9).pix_price == Decimal("569.91")


def test_format_brl():
    assert format_brl(Decimal("2199")) == "R$ 2.199,00"
    assert format_brl(Decimal("183.25")) == "R$ 183,25"
    assert format_brl(Decimal("1234567.5")) == "R$ 1.234.567,50"


@pytest.mark.parametrize(
    ("status", "stock", "situation"),
    [
        ("active", 5, "disponivel"),
        ("active", 0, "esgotado"),
        ("discontinued", 13, "descontinuado"),
        ("discontinued", 0, "descontinuado"),
        ("coming_soon", 2, "em_breve"),
    ],
)
def test_product_situation(status, stock, situation):
    assert product_situation(status, stock) == situation


def test_unknown_status_is_an_error():
    with pytest.raises(ValueError):
        product_situation("sold", 1)


def test_prices_are_hidden_only_for_products_not_on_sale():
    assert shows_price("disponivel") and shows_price("esgotado")
    assert not shows_price("descontinuado") and not shows_price("em_breve")


def test_available_only_filter_agrees_with_product_situation(real_db):
    with closing(connect_read_only(real_db)) as conn:
        everything = search_products(conn, [], limit=None)
        available = search_products(conn, [], available_only=True, limit=None)
    expected = {
        row["product_id"]
        for row in everything
        if product_situation(row["status"], row["stock_quantity"]) == "disponivel"
    }
    assert {row["product_id"] for row in available} == expected
    assert len(everything) == 65


RECORD = {"email": "ana@jmail.com", "phone": "67996547821", "email_is_shared": 0}
SHARED = {"email": "bruno@jmail.com", "phone": "67985432109", "email_is_shared": 1}


@pytest.mark.parametrize(
    ("email", "phone", "allowed"),
    [
        ("ana@jmail.com", None, True),
        ("  ANA@jmail.com ", None, True),
        (None, "(67) 99654-7821", True),
        (None, "+55 67 99654-7821", True),
        ("outra@jmail.com", "67996547821", True),
        ("outra@jmail.com", None, False),
        (None, "67900000000", False),
        (None, None, False),
        ("", "", False),
    ],
)
def test_order_number_needs_one_matching_contact(email, phone, allowed):
    assert can_view_order(RECORD, email, phone) is allowed


@pytest.mark.parametrize(
    ("email", "phone", "allowed"),
    [
        ("ana@jmail.com", "67996547821", True),
        ("ana@jmail.com", None, False),
        (None, "67996547821", False),
        ("outra@jmail.com", "67996547821", False),
    ],
)
def test_without_order_number_both_contacts_are_required(email, phone, allowed):
    assert can_view_order(RECORD, email, phone, by_order_number=False) is allowed


def test_shared_email_does_not_count_alone():
    assert not can_view_order(SHARED, "bruno@jmail.com", None)
    assert not can_view_order(SHARED, "bruno@jmail.com", "67994563456")
    assert can_view_order(SHARED, "bruno@jmail.com", "67985432109")
    assert can_view_order(SHARED, None, "67985432109")


def test_empty_contact_never_matches_an_empty_value():
    assert not can_view_order({**RECORD, "phone": ""}, None, None)


def test_normalize_phone():
    assert normalize_phone("(67) 99654-7821") == "67996547821"
    assert normalize_phone("5567996547821") == "67996547821"
    assert normalize_phone(None) == ""


def test_order_10_needs_the_phone_of_customer_11(real_db):
    with closing(connect_read_only(real_db)) as conn:
        record = get_order_with_customer(conn, 10)
    assert not can_view_order(record, "bruno.martins@jmail.com", None)
    assert not can_view_order(record, "bruno.martins@jmail.com", "(67) 99456-3456")
    assert can_view_order(record, "bruno.martins@jmail.com", "(67) 98543-2109")
