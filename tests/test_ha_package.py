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

from meal_planner.core.catalog_actions import ASSIST_UNITS
from meal_planner.core.shopping import CATEGORY_ORDER
from meal_planner.core.units import LINE_UNITS

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


def schema_at(path: list[str], root: str = "SummaryOut") -> dict:
    """Схема поля root по пути ["days", "today", …]."""
    schema = resolve(SCHEMAS[root])
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


def template_block(unique_id: str) -> dict:
    """Блок template с сенсором unique_id: у партии и книги рецептов — свои."""
    (block,) = [
        b
        for b in PACKAGE["template"]
        if any(s["unique_id"] == unique_id for s in b["sensor"])
    ]
    return block


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
    block = template_block("meal_planner_cook_batch")
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
    block = template_block("meal_planner_cook_batch")
    events = {t.get("event_type") for t in block["triggers"]}
    assert "meal_planner_changed" in events
    for name in (*COOKING_SCRIPTS, *PORTION_SCRIPTS):
        script = json.dumps(PACKAGE["script"][name], ensure_ascii=False)
        assert '"event": "meal_planner_changed"' in script, name
        # Отказ — тоже событие: причину показывает плитка (last_error).
        assert '"error": "{{ result.error }}"' in script, name
        # …и через 3 с сообщение снимается, не задерживая ответ скрипта.
        assert "script.meal_planner_clear_dish_error" in script, name
    clear = PACKAGE["script"]["meal_planner_clear_dish_error"]
    assert clear["mode"] == "restart"
    assert clear["sequence"] == [{"delay": 3}, {"event": "meal_planner_changed"}]


# Скрипты, что списывают или возвращают продукты: 8.3b и 10.4.
COOKING_SCRIPTS = (
    "meal_planner_mark_cooked",
    "meal_planner_unmark_cooked",
    "meal_planner_use_ingredient",
    "meal_planner_unuse_ingredient",
    "meal_planner_cook_recipe",
    "meal_planner_uncook_recipe",
)
PORTION_SCRIPTS = ("meal_planner_set_dish_portions", "meal_planner_set_recipe_portions")


def stock_template() -> str:
    plan = PACKAGE["script"]["meal_planner_plan"]["sequence"]
    return next(
        s["variables"]["stock"] for s in plan if "stock" in s.get("variables", {})
    )


def test_cooking_scripts_send_stock_and_set_counters():
    # Все счётчики — в запрос: возврат идёт только в переданные (10.3).
    for name in COOKING_SCRIPTS:
        sequence = PACKAGE["script"][name]["sequence"]
        assert sequence[0] == {"variables": {"stock": stock_template()}}, name
        call = sequence[1]
        assert call["data"]["stock"] == "{{ stock }}", name
        counters = json.dumps(sequence[2]["then"][0], ensure_ascii=False)
        assert "resp.content.counters" in counters, name
        assert "input_number.set_value" in counters, name


def test_cooking_scripts_pass_the_scope():
    # Поток (10.6): поле скрипта → тело запроса, по умолчанию вне очереди.
    for name in (*COOKING_SCRIPTS[2:], "meal_planner_set_recipe_portions"):
        script = PACKAGE["script"][name]
        field = script["fields"]["scope"]
        assert field["default"] == "extra", name
        assert field["selector"]["select"]["options"] == ["extra", "plan"], name
        call = next(s for s in script["sequence"] if "action" in s)
        assert call["data"]["scope"] == "{{ scope | default('extra') }}", name
    # «Приготовлено» (8.3b) — всегда партия, поля нет.
    assert "scope" not in PACKAGE["script"]["meal_planner_mark_cooked"]["fields"]


def test_cookbook_sensor_uses_cookbook_fields():
    # Книга рецептов (9.1) — свой запрос, ответ целиком в атрибутах.
    block = template_block("meal_planner_cookbook")
    (stock, call, variables) = block["actions"]
    # С остатками из счётчиков (10.4): нехватка видна заранее.
    assert stock["variables"]["stock"] == stock_template()
    assert call["action"] == "rest_command.meal_planner_cookbook"
    assert call["data"] == {"stock": "{{ stock }}"}
    assert call["response_variable"] == "resp"
    assert "resp.content" in variables["variables"]["book"]
    (sensor,) = block["sensor"]
    templates = json.dumps(sensor, ensure_ascii=False)
    paths = re.findall(r"book((?:\.\w+)+)", templates)
    assert "book.cooking" in [f"book{p}" for p in paths]
    for path in paths:
        schema_at(path.split(".")[1:], root="CookbookOut")
    assert set(sensor["attributes"]) == {
        "cook",
        "lunch",
        "dinner",
        "breakfast",
        "now",
        "cooking_now",
        "cooking",
        "last_error",
    }
    # Отказ действия готовки — как у партии.
    (batch,) = template_block("meal_planner_cook_batch")["sensor"]
    assert sensor["attributes"]["last_error"] == batch["attributes"]["last_error"]
    # Обновляется тогда же, что партия, — в том числе по meal_planner_changed.
    assert block["triggers"] == template_block("meal_planner_cook_batch")["triggers"]


def test_cookbook_sensor_is_not_recorded():
    # ~50 КБ атрибутов: recorder их не пишет и предупреждает в логе.
    assert "sensor.meal_planner_cookbook" in PACKAGE["recorder"]["exclude"]["entities"]


def test_refresh_script_only_fires_the_event():
    # Касание заголовка «рецепты» на дашборде: перечитать книгу сразу.
    script = PACKAGE["script"]["meal_planner_refresh"]
    assert script["sequence"] == [{"event": "meal_planner_changed"}]


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
    # Отсекает их проверка в скрипте, а не фильтр селектора — см. ниже.
    script = PACKAGE["script"]["meal_planner_sync_shopping_list"]
    (entity_filter,) = script["fields"]["todo_list"]["selector"]["entity"]["filter"]
    assert entity_filter == {"domain": "todo"}
    assert "bitwise_and(64)" in json.dumps(script["sequence"], ensure_ascii=False)


def test_selectors_have_no_supported_features():
    # HA 2026.10: supported_features в фильтре селектора валит сборку tools
    # для LLM («expected str at filter[0].supported_features[0][0]») — и
    # Assist не получает ни одного скрипта.
    assert "supported_features" not in json.dumps(
        {name: s.get("fields", {}) for name, s in PACKAGE["script"].items()}
    )


def test_stock_template_passes_entity_id():
    # Списание (8.3b) отдаёт новые значения по entity_id счётчика.
    plan = PACKAGE["script"]["meal_planner_plan"]["sequence"]
    stock = next(
        s["variables"]["stock"] for s in plan if "stock" in s.get("variables", {})
    )
    assert '"entity_id": s.entity_id' in stock
    stock_line = resolve(SCHEMAS["StockLineIn"])["properties"]
    assert set(re.findall(r'"(\w+)":', stock)) == set(stock_line)


def object_fields(script: dict, field: str) -> dict:
    return script["fields"][field]["selector"]["object"]["fields"]


def options(field: dict) -> list[str]:
    return [
        o["value"] if isinstance(o, dict) else o
        for o in field["selector"]["select"]["options"]
    ]


def test_propose_recipe_fields_match_api():
    # Рецепт от Assist (8.6c): поля скрипта — тело propose-recipe.
    script = PACKAGE["script"]["meal_planner_propose_recipe"]
    body = resolve(SCHEMAS["ProposeRecipeIn"])["properties"]
    storage = set(resolve(SCHEMAS["RecipeStorageIn"])["properties"])
    # storage — отдельными полями: модель заполняет их по одному.
    # draft_id — id для update-draft (правка своего черновика).
    update = resolve(SCHEMAS["UpdateDraftIn"])["properties"]
    assert set(update) == set(body) | {"id"}
    assert set(script["fields"]) == (set(body) - {"storage"}) | storage | {"draft_id"}
    line = object_fields(script, "ingredients")
    assert set(line) == set(resolve(SCHEMAS["ProposedLineIn"])["properties"])
    new = object_fields(script, "new_ingredients")
    assert set(new) == set(resolve(SCHEMAS["NewIngredientIn"])["properties"])
    assert options(script["fields"]["kind"]) == ["lunch", "dinner"]


def test_propose_recipe_rules_match_core():
    # Подсказки модели — те же правила, что проверяет core (8.6b).
    script = PACKAGE["script"]["meal_planner_propose_recipe"]
    assert options(object_fields(script, "ingredients")["unit"]) == list(LINE_UNITS)
    new = object_fields(script, "new_ingredients")
    assert options(new["unit"]) == list(ASSIST_UNITS)
    assert options(new["category"]) == list(CATEGORY_ORDER)
    text = json.dumps(script, ensure_ascii=False)
    assert "meal_planner_ingredients" in text and "«В план»" in text
    # Остатки и справочник — не повод для выбора блюда (поправка к 8.6c).
    assert "на выбор блюда не влияют" in text
    ingredients = json.dumps(
        PACKAGE["script"]["meal_planner_ingredients"]["description"],
        ensure_ascii=False,
    )
    assert "а не запасы дома" in ingredients


def test_ingredients_script_uses_catalog_fields():
    script = json.dumps(
        PACKAGE["script"]["meal_planner_ingredients"], ensure_ascii=False
    )
    used = set(re.findall(r"i\['(\w+)'\]", script))
    assert used and used <= set(resolve(SCHEMAS["CatalogIngredientOut"])["properties"])
    assert set(re.findall(r"\bresp\.content\.(\w+)", script)) <= set(
        resolve(SCHEMAS["IngredientsOut"])["properties"]
    )
    # Существующие блюда (8.6c): GET /api/recipes — словарь RecipeOut по id.
    assert "recipes_resp.content.values()" in script
    used = set(re.findall(r"r\['(\w+)'\]", script))
    assert used and used <= set(resolve(SCHEMAS["RecipeOut"])["properties"])
    assert "map(attribute='name')" in script


def test_propose_recipe_reads_recipe_fields():
    script = json.dumps(
        PACKAGE["script"]["meal_planner_propose_recipe"], ensure_ascii=False
    )
    recipe = resolve(SCHEMAS["RecipeOut"])["properties"]
    assert set(re.findall(r"recipe\.(\w+)", script)) <= set(recipe)
    line = resolve(SCHEMAS["IngredientOut"])["properties"]
    assert set(re.findall(r"\bi\.(\w+)", script)) <= set(line)
    assert "from_assist" in line
