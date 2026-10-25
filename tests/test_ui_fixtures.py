import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "frontend" / "src" / "__fixtures__" / "ui-states.json"


def load_generator():
    spec = importlib.util.spec_from_file_location(
        "dump_ui_fixtures", ROOT / "tools" / "dump_ui_fixtures.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ui_fixtures_are_current():
    # На этих ответах держатся снапшоты UI (frontend/src/ui-snapshots.test.tsx).
    fixtures = json.loads(FIXTURES.read_text("utf-8"))
    assert load_generator().build() == fixtures, (
        "Ответы API изменились: cd frontend && npm run gen:fixtures"
    )
