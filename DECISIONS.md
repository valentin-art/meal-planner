# Решения

Решения и их причины с фазы 4 smarthome; решения стека HA — в
`~/projects/smarthome/docs/DECISIONS.md`, ранние решения — в `implementation-plan.md`.

## D-1. Чтение «ингредиенты без позиции»

2026-11-14, smarthome фаза 4 (smarthome D-37, D-39). `POST /api/ingredients/unstocked` —
только чтение: остатки не хранятся, версия settings не меняется; POST из-за тела, это не
действие. Тело — `ShoppingRemainingIn`, как у `/api/shopping/remaining`. В ответе —
ингредиенты рецептов со `status == "active"` любого `kind` (`draft` и `archived` не
участвуют), чей `id` не встречается среди `ingredient_id` тела при любом `qty` (0 и
отрицательные тоже) и любом `unit`; неизвестные id пропускаются, без дублей, порядок —
индекс `CATEGORY_ORDER` (чужая категория в конец), затем `name`. Каждый ингредиент —
`{id, name, unit, category, units, fixes}`: `units` — единицы из `LINE_UNITS` и
`catalog_actions.UNITS`, для которых `stock.convert` не `None`; `fixes` —
`[{field: "store_piece" | "grams_per_unit", units}]`, единицы, которые станут переводимыми,
если незаполненное поле задать (тот же `convert` над копией ингредиента). Почему: правило
перевода одно, панель HA только сравнивает строки и не дублирует его.

## D-2. Сенсор пакета `sensor.meal_planner_unstocked`

2026-11-14, smarthome фаза 4 (smarthome D-40). `rest_command.meal_planner_unstocked_ingredients`
и trigger-шаблон: состояние — длина атрибута `ingredients` (ответ D-1 как есть, вне
recorder), пересчёт при старте, перезагрузке шаблонов, раз в 15 минут, по
`kitchen_stock_items_changed` и `meal_planner_changed`. Скрипт trigger-шаблона работает в
режиме `single`: срабатывание во время запроса отбрасывается с WARNING и поправляется
следующим триггером (принято пользователем).

## D-3. Книга рецептов и партия обновляются по `kitchen_stock_items_changed`

2026-11-14, smarthome фаза 4. Событие добавлено в якорь `refresh_triggers`: добавление,
привязка и удаление плитки обновляют книгу и партию сразу; правка значений — по-прежнему
до 15 минут.
