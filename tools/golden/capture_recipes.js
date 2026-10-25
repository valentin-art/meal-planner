// Снимает справочник рецептов с оригинального meal-plan.jsx (тикет 1.3).
//
//   node tools/golden/capture_recipes.js > tests/golden_recipes.json
//
// RECIPES и FISH_*_IDS берутся дословно из JSX; tests/test_recipes_data.py
// сверяет с этим файлом src/meal_planner/core/recipes_data.py поле за полем.
'use strict';

const { loadJsx } = require('./load_jsx');

const { js, sha256 } = loadJsx('({ RECIPES, FISH_LUNCH_IDS, FISH_DINNER_IDS })');

const out = {
  source_sha256: sha256,
  fish_ids: [...Object.keys(js.FISH_LUNCH_IDS), ...Object.keys(js.FISH_DINNER_IDS)],
  recipes: js.RECIPES,
};
process.stdout.write(JSON.stringify(out, null, 2) + '\n');
