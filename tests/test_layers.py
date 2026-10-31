import ast
from pathlib import Path

import pytest

from app.config import PROJECT_ROOT


def imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize(
    ("layer", "forbidden"),
    [
        ("store", ("app.policy", "app.agent", "app.api")),
        ("policy", ("sqlite3", "app.store", "app.agent", "app.api")),
    ],
)
def test_layer_imports(layer, forbidden):
    files = list((PROJECT_ROOT / "app" / layer).glob("**/*.py"))
    assert files
    for path in files:
        for module in imported_modules(path):
            assert not module.startswith(forbidden), f"{path.name} imports {module}"
