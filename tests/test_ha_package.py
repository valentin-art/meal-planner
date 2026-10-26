"""Пакет HA (ha/packages/meal_planner.yaml) против API приложения.

HA в тестах не поднять, поэтому сверяется контракт: URL и методы, поля тел
запросов, пути в шаблонах сенсоров — с frontend/openapi.json (его свежесть
проверяет test_openapi_snapshot.py).
"""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8001"


class HALoader(yaml.SafeLoader):
    pass


# Теги HA (!secret, !include…) для сверки не нужны — читаем как None.
HALoader.add_multi_constructor("!", lambda loader, suffix, node: None)

PACKAGE = yaml.load(
    (ROOT / "ha/packages/meal_planner.yaml").read_text(encoding="utf-8"), HALoader
)
OPENAPI = json.loads((ROOT / "frontend/openapi.json").read_text(encoding="utf-8"))
SCHEMAS = OPENAPI["components"]["schemas"]
TEXT = json.dumps(PACKAGE, ensure_ascii=False)


def resolve(schema: dict) -> dict:
    if "$ref" in schema:
        return resolve(SCHEMAS[schema["$ref"].rsplit("/", 1)[1]])
    for option in schema.get("anyOf", ()):  # nullable: X | None
        if option.get("type") != "null":
            return resolve(option)
    return schema


def schema_at(path: list[str]) -> dict:
    """Схема поля SummaryOut по пути ["days", "today", …]."""
    schema = resolve(SCHEMAS["SummaryOut"])
    for key in path:
        assert key in schema.get("properties", {}), f"нет поля {key} в {path}"
        schema = resolve(schema["properties"][key])
    return schema


def request_schema(path: str, method: str) -> dict | None:
    op = OPENAPI["paths"][path][method]
    body = op.get("requestBody")
    return resolve(body["content"]["application/json"]["schema"]) if body else None


def endpoints():
    yield "rest", "get", PACKAGE["rest"][0]["resource"], None
    for name, cmd in PACKAGE["rest_command"].items():
        yield name, cmd["method"], cmd["url"], cmd.get("payload")


@pytest.mark.parametrize(("name", "method", "url", "payload"), list(endpoints()))
def test_urls_and_bodies_match_api(name, method, url, payload):
    assert url.startswith(BASE + "/"), name
    path = urlsplit(url).path
    assert method in OPENAPI["paths"].get(path, {}), f"{name}: нет {method} {path}"
    schema = request_schema(path, method)
    if schema is None:
        assert payload is None, f"{name}: у {path} нет тела"
        return
    assert set(re.findall(r'"(\w+)":', payload)) == set(schema["properties"]), name


def sensors():
    return PACKAGE["rest"][0]["sensor"]


def entity_id(sensor: dict) -> str:
    return "sensor." + sensor["name"].lower().replace(" ", "_")


@pytest.mark.parametrize("sensor", sensors(), ids=entity_id)
def test_sensor_templates_use_summary_fields(sensor):
    for path in re.findall(r"value_json((?:\.\w+)+)", sensor["value_template"]):
        schema_at(path.split(".")[1:])
    attrs = schema_at(sensor["json_attributes_path"].removeprefix("$.").split("."))
    assert set(sensor["json_attributes"]) <= set(attrs["properties"])


def test_references_resolve():
    used_commands = set(re.findall(r"rest_command\.(\w+)", TEXT))
    assert used_commands == set(PACKAGE["rest_command"])
    declared = {entity_id(s) for s in sensors()}
    assert set(re.findall(r"sensor\.meal_planner_\w+", TEXT)) == declared
    assert set(PACKAGE["homeassistant"]["customize"]) == declared
    assert set(PACKAGE["conversation"]["intents"]) == set(PACKAGE["intent_script"])


def test_scripts_describe_their_fields_for_the_llm():
    for name, script in PACKAGE["script"].items():
        assert script.get("description"), name
        for field_name, field in script.get("fields", {}).items():
            assert field.get("description") and field.get("selector"), (
                name,
                field_name,
            )
