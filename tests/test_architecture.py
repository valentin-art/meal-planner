"""Граница слоя core/: чистый Python без FastAPI/Pydantic/SQLite и без api/db."""

import ast
from pathlib import Path

import pytest

import meal_planner.core

FORBIDDEN = ("fastapi", "pydantic", "sqlite3", "meal_planner.api", "meal_planner.db")
CORE_DIR = Path(meal_planner.core.__file__).parent
CORE_FILES = sorted(CORE_DIR.rglob("*.py"))


def module_name(path: Path) -> str:
    parts = path.relative_to(CORE_DIR).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("meal_planner", "core", *parts))


def is_forbidden(name: str) -> bool:
    return any(name == f or name.startswith(f + ".") for f in FORBIDDEN)


def forbidden_imports(source: str, module: str, is_package: bool = False) -> list[str]:
    """Запрещённые импорты в исходнике модуля module, как "строка: модуль"."""
    package = module if is_package else module.rpartition(".")[0]
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # from ..db import x → meal_planner.db
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                base = f"{base}.{node.module}" if node.module else base
            else:
                base = node.module or ""
            # from meal_planner import db: запрещённое — в имени, а не в пути
            names = [base, *(f"{base}.{alias.name}" for alias in node.names)]
        else:
            continue
        found += [f"{node.lineno}: {name}" for name in names if is_forbidden(name)]
    return found


@pytest.mark.parametrize("path", CORE_FILES, ids=lambda p: p.name)
def test_core_has_no_forbidden_imports(path):
    source = path.read_text("utf-8")
    violations = forbidden_imports(
        source, module_name(path), is_package=path.name == "__init__.py"
    )
    assert not violations, f"{path.name}: {violations}"


def test_core_modules_are_found():
    names = {p.stem for p in CORE_FILES}
    assert {"models", "recipes_data", "scheduling", "shopping", "planner"} <= names


@pytest.mark.parametrize(
    "source",
    [
        "import fastapi",
        "import fastapi.responses",
        "from pydantic import BaseModel",
        "import sqlite3 as db",
        "from meal_planner.db.repository import get_settings",
        "from meal_planner import db",
        "from ..api import main",
        "from .. import db",
        "def f():\n    import sqlite3",
    ],
)
def test_checker_detects_violations(source):
    assert forbidden_imports(source, "meal_planner.core.x")


@pytest.mark.parametrize(
    "source",
    [
        "from datetime import date",
        "from . import models",
        "from .models import Recipe",
        "from meal_planner.core.models import Recipe",
        "import pydanticish",
        "import fastapi_utils",
    ],
)
def test_checker_allows_clean_imports(source):
    assert forbidden_imports(source, "meal_planner.core.x") == []
