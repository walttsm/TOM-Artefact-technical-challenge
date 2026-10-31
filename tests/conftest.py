from pathlib import Path

import pytest

from app.config import PROJECT_ROOT
from app.ingest import ingest
from app.store.loader import CSV_NAME

REAL_DATA = PROJECT_ROOT / "data"


@pytest.fixture(scope="session")
def real_db(tmp_path_factory) -> Path:
    if not (REAL_DATA / CSV_NAME.format(table="products")).exists():
        pytest.skip("data/ is not in the repository")
    db_path = tmp_path_factory.mktemp("real") / "emporio.db"
    ingest(REAL_DATA, db_path)
    return db_path
