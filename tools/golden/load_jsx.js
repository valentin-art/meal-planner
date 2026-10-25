// Общий загрузчик для golden-харнессов: выполняет дословно расчётную часть
// meal-plan.jsx (от `const RECIPES = {` до `const DEFAULT_SETTINGS`) в vm-контексте.
'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const vm = require('vm');

const SOURCE_PATH = path.join(__dirname, '..', '..', 'meal-plan.jsx');
const START_MARKER = 'const RECIPES = {';
const END_MARKER = 'const DEFAULT_SETTINGS';

// expr — JS-выражение над идентификаторами фрагмента, например '({ RECIPES })'.
function loadJsx(expr) {
  const source = fs.readFileSync(SOURCE_PATH, 'utf8');
  const start = source.indexOf(START_MARKER);
  const end = source.indexOf(END_MARKER);
  if (start < 0 || end < 0 || end <= start) {
    throw new Error(`meal-plan.jsx: не найдены маркеры "${START_MARKER}" / "${END_MARKER}"`);
  }
  const ctx = vm.createContext({});
  vm.runInContext(source.slice(start, end), ctx);
  return {
    js: vm.runInContext(expr, ctx),
    sha256: crypto.createHash('sha256').update(source).digest('hex'),
  };
}

module.exports = { loadJsx };
