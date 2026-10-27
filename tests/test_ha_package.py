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


def template_sensors():
    return [s for block in PACKAGE["template"] for s in block["sensor"]]


def entity_id(sensor: dict) -> str:
    return "sensor." + sensor["name"].lower().replace(" ", "_")


@pytest.mark.parametrize("sensor", sensors(), ids=entity_id)
def test_sensor_templates_use_summary_fields(sensor):
    templates = sensor["value_template"] + sensor.get("availability", "")
    for path in re.findall(r"value_json((?:\.\w+)+)", templates):
        schema_at(path.split(".")[1:])
    attrs = schema_at(sensor["json_attributes_path"].removeprefix("$.").split("."))
    assert set(sensor["json_attributes"]) <= set(attrs["properties"])


def test_references_resolve():
    used_commands = set(re.findall(r"rest_command\.(\w+)", TEXT))
    assert used_commands == set(PACKAGE["rest_command"])
    declared = {entity_id(s) for s in sensors() + template_sensors()}
    assert set(re.findall(r"sensor\.meal_planner_\w+", TEXT)) == declared
    assert set(PACKAGE["homeassistant"]["customize"]) == declared
    assert set(PACKAGE["conversation"]["intents"]) == set(PACKAGE["intent_script"])


def test_cook_batch_sensor_uses_summary_fields():
    # Сенсор-шаблон (8.3b) сам запрашивает summary и берёт из него cooking.
    (block,) = PACKAGE["template"]
    (call, variables) = block["actions"]
    assert call["action"] == "rest_command.meal_planner_summary"
    assert call["response_variable"] == "resp"
    for path in re.findall(
        r"resp\.content((?:\.\w+)+)", variables["variables"]["cooking"]
    ):
        schema_at(path.split(".")[1:])
    cooking = schema_at(["cooking"])["properties"]
    (sensor,) = block["sensor"]
    templates = json.dumps(sensor, ensure_ascii=False)
    for field in re.findall(r"cooking\.(\w+)", templates):
        assert field in cooking, field
    # Атрибуты — поля cooking, кроме last_error: его приносит событие отказа.
    assert set(sensor["attributes"]) - {"last_error"} <= set(cooking)
    assert "trigger.event.data" in sensor["attributes"]["last_error"]


def test_actions_refresh_the_cook_batch():
    # Сенсор партии обновляется по событию: его шлют скрипты действий.
    (block,) = PACKAGE["template"]
    events = {t.get("event_type") for t in block["triggers"]}
    assert "meal_planner_changed" in events
    for name in (
        "meal_planner_mark_cooked",
        "meal_planner_unmark_cooked",
        "meal_planner_set_dish_portions",
    ):
        script = json.dumps(PACKAGE["script"][name], ensure_ascii=False)
        assert '"event": "meal_planner_changed"' in script, name
        # Отказ — тоже событие: причину показывает плитка (last_error).
        assert '"error": "{{ result.error }}"' in script, name


def test_scripts_describe_their_fields_for_the_llm():
    for name, script in PACKAGE["script"].items():
        assert script.get("description"), name
        for field_name, field in script.get("fields", {}).items():
            assert field.get("description") and field.get("selector"), (
                name,
                field_name,
            )


def test_script_references_resolve():
    used = set(re.findall(r"script\.(meal_planner_\w+)", TEXT))
    assert used and used <= set(PACKAGE["script"])
    assert all(a.get("id") for a in PACKAGE["automation"])  # иначе нет трассировок


def test_items_key_is_not_an_attribute():
    # В Jinja x.items — метод dict, а не поле «items»: только x['items'].
    assert not re.search(r"(next_shopping|\])\.items\b", TEXT)


def test_sync_targets_only_lists_with_item_description():
    # Свои пункты to-do помечаются описанием «meal-planner <дата>»: список
    # без описания (Shopping List) ломал todo.add_item (8.2).
    script = PACKAGE["script"]["meal_planner_sync_shopping_list"]
    (entity_filter,) = script["fields"]["todo_list"]["selector"]["entity"]["filter"]
    assert entity_filter == {
        "domain": "todo",
        "supported_features": ["todo.TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM"],
    }
    assert "bitwise_and(64)" in json.dumps(script["sequence"], ensure_ascii=False)


def test_stock_template_passes_entity_id():
    # Списание (8.3b) отдаёт новые значения по entity_id счётчика.
    plan = PACKAGE["script"]["meal_planner_plan"]["sequence"]
    stock = next(
        s["variables"]["stock"] for s in plan if "stock" in s.get("variables", {})
    )
    assert '"entity_id": s.entity_id' in stock
    stock_line = resolve(SCHEMAS["StockLineIn"])["properties"]
    assert set(re.findall(r'"(\w+)":', stock)) == set(stock_line)
