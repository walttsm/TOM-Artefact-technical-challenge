import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app.config import PROJECT_ROOT
from app.ingest import IngestionError, ingest
from app.store.loader import CSV_NAME, to_search_text

REAL_DATA = PROJECT_ROOT / "data"
needs_real_data = pytest.mark.skipif(
    not (REAL_DATA / CSV_NAME.format(table="products")).exists(),
    reason="data/ is not in the repository",
)

SAMPLE_CSVS = {
    "categories": """category_id,name,description
1,Violões,Violões de nylon e aço
2,Sopros,Sem produtos
""",
    "products": """product_id,price_brl,name,category_id,description,stock_quantity,status,specs,created_at
10,100.5,Violão Aço,1,Tampo sólido,3,active,"{""color"":""Natural""}",2021-03-15
11,200,Violão Novo,1,Em breve,2,coming_soon,{},2021-03-15
""",
    "promotions": """promotion_id,product_id,discount_percent,description,is_active
1,10,10,Black Friday,0
2,10,15,Black Friday,0
""",
    "customers": """customer_id,name,phone,email,city
1,Ana,(67) 99654-7821,Ana@Jmail.com,Campo Grande
2,Bia,(67) 99654-0000,ana@jmail.com,Dourados
""",
    "orders": """order_id,customer_id,order_date,status,total_brl,payment_method,tracking_code,estimated_delivery,notes
1,1,2026-01-05,shipped,100.5,credit_3x,BR123,2026-01-18,Frágil
2,2,2026-01-06,pending,999,pix,,,
""",
    "order_items": """order_id,quantity,product_id
1,1,10
2,1,11
""",
}


def write_csvs(data_dir: Path, **overrides: str) -> Path:
    data_dir.mkdir()
    for table, content in {**SAMPLE_CSVS, **overrides}.items():
        path = data_dir / CSV_NAME.format(table=table)
        path.write_text(content, encoding="utf-8", newline="")
    return data_dir


def read_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def dump(db_path: Path) -> list[str]:
    with closing(sqlite3.connect(db_path)) as conn:
        return list(conn.iterdump())


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Violões", "violoes"), ("AÇO", "aco"), ("Três Lagoas", "tres lagoas")],
)
def test_search_text_drops_accents_and_case(text, expected):
    assert to_search_text(text) == expected


def test_cleans_values(tmp_path):
    db_path = tmp_path / "storage" / "test.db"
    ingest(write_csvs(tmp_path / "data"), db_path)

    with closing(read_db(db_path)) as conn:
        customer = conn.execute("SELECT * FROM customers WHERE customer_id = 1").fetchone()
        assert customer["phone"] == "67996547821"
        assert customer["email"] == "ana@jmail.com"

        order = conn.execute("SELECT * FROM orders WHERE order_id = 2").fetchone()
        assert order["tracking_code"] is None
        assert order["estimated_delivery"] is None
        assert order["notes"] is None
        assert conn.execute("SELECT notes FROM orders WHERE order_id = 1").fetchone()[0] == "Frágil"

        product = conn.execute("SELECT * FROM products WHERE product_id = 10").fetchone()
        assert product["name"] == "Violão Aço"
        assert product["name_search"] == "violao aco"
        assert product["price_brl"] == 100.5
        category = conn.execute("SELECT * FROM categories WHERE category_id = 1").fetchone()
        assert category["name_search"] == "violoes"


def test_reports_inconsistencies_without_changing_data(tmp_path):
    db_path = tmp_path / "test.db"
    report = ingest(write_csvs(tmp_path / "data"), db_path)

    found = {(item.kind, item.record_id) for item in report}
    assert found == {
        ("order_total_differs", 2),
        ("unavailable_with_stock", 11),
        ("empty_category", 2),
        ("shared_email", "ana@jmail.com"),
        ("ordered_before_release", 2),
        ("repeated_promotion", 10),
    }
    assert all(item.treatment for item in report)
    with closing(read_db(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM promotions").fetchone()[0] == 2
        assert conn.execute("SELECT total_brl FROM orders WHERE order_id = 2").fetchone()[0] == 999
        assert conn.execute("SELECT stock_quantity FROM products WHERE product_id = 11").fetchone()[0] == 2


def test_marks_customers_sharing_an_email(tmp_path):
    customers = SAMPLE_CSVS["customers"] + "3,Caio,(67) 99654-1111,caio@jmail.com,Dourados\n"
    db_path = tmp_path / "test.db"
    ingest(write_csvs(tmp_path / "data", customers=customers), db_path)

    with closing(read_db(db_path)) as conn:
        rows = conn.execute("SELECT customer_id, email_is_shared FROM customers ORDER BY 1")
        assert [tuple(row) for row in rows] == [(1, 1), (2, 1), (3, 0)]


def test_customers_cannot_share_a_phone(tmp_path):
    customers = """customer_id,name,phone,email,city
1,Ana,(67) 99654-7821,ana@jmail.com,Campo Grande
2,Bia,67996547821,bia@jmail.com,Dourados
"""
    with pytest.raises(sqlite3.IntegrityError):
        ingest(write_csvs(tmp_path / "data", customers=customers), tmp_path / "test.db")


def test_running_twice_gives_the_same_result(tmp_path):
    data_dir = write_csvs(tmp_path / "data")
    db_path = tmp_path / "test.db"

    first_report = ingest(data_dir, db_path)
    first_dump = dump(db_path)

    assert ingest(data_dir, db_path) == first_report
    assert dump(db_path) == first_dump


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"order_items": "order_id,quantity,product_id\n1,1,999\n"}, IngestionError),
        ({"products": SAMPLE_CSVS["products"].replace("active", "sold")}, sqlite3.IntegrityError),
    ],
    ids=["broken_foreign_key", "failed_check"],
)
def test_failed_ingestion_keeps_previous_data(tmp_path, overrides, error):
    db_path = tmp_path / "test.db"
    ingest(write_csvs(tmp_path / "good"), db_path)
    before = dump(db_path)

    with pytest.raises(error):
        ingest(write_csvs(tmp_path / "bad", **overrides), db_path)

    assert dump(db_path) == before


def test_missing_file_keeps_previous_data(tmp_path):
    db_path = tmp_path / "test.db"
    data_dir = write_csvs(tmp_path / "data")
    ingest(data_dir, db_path)
    before = dump(db_path)
    (data_dir / CSV_NAME.format(table="order_items")).unlink()

    with pytest.raises(FileNotFoundError):
        ingest(data_dir, db_path)

    assert dump(db_path) == before


def test_only_one_active_promotion_per_product(tmp_path):
    promotions = "promotion_id,product_id,discount_percent,description,is_active\n1,10,10,A,1\n2,10,20,B,1\n"
    with pytest.raises(sqlite3.IntegrityError):
        ingest(write_csvs(tmp_path / "data", promotions=promotions), tmp_path / "test.db")


@needs_real_data
def test_real_data_counts(tmp_path):
    db_path = tmp_path / "test.db"
    ingest(REAL_DATA, db_path)

    with closing(read_db(db_path)) as conn:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("categories", "products", "promotions", "customers", "orders", "order_items")
        }
        active = conn.execute("SELECT COUNT(*) FROM promotions WHERE is_active = 1").fetchone()[0]
        names = {row[0] for row in conn.execute("SELECT name FROM products")}

    assert counts == {
        "categories": 9,
        "products": 65,
        "promotions": 25,
        "customers": 50,
        "orders": 20,
        "order_items": 22,
    }
    assert active == 4
    assert "Tagima Dallas Tuner Aço Natural" in names


@needs_real_data
def test_real_data_quality_report(tmp_path):
    report = ingest(REAL_DATA, tmp_path / "test.db")

    by_kind: dict[str, set] = {}
    for item in report:
        by_kind.setdefault(item.kind, set()).add(item.record_id)

    assert by_kind["order_total_differs"] == {3, 20}
    assert by_kind["unavailable_with_stock"] == {130, 136}
    assert by_kind["empty_category"] == {6, 7, 8}
    assert by_kind["shared_email"] == {"bruno.martins@jmail.com"}
    assert by_kind["ordered_before_release"] == {3, 14}
    assert by_kind["repeated_promotion"] == {93, 96}


@needs_real_data
def test_real_data_ingestion_is_repeatable(tmp_path):
    db_path = tmp_path / "test.db"
    ingest(REAL_DATA, db_path)
    first = dump(db_path)
    ingest(REAL_DATA, db_path)
    assert dump(db_path) == first
