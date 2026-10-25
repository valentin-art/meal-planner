import json
from pathlib import Path

from meal_planner.api.main import create_app

SNAPSHOT = Path(__file__).parents[1] / "frontend" / "openapi.json"


def test_openapi_snapshot_is_current():
    # Типы фронтенда (src/api-types.ts) генерируются из этого снапшота.
    snapshot = json.loads(SNAPSHOT.read_text("utf-8"))
    assert create_app().openapi() == snapshot, (
        "Схема API изменилась: cd frontend && npm run gen:api"
    )
