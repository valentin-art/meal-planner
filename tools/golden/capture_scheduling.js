// Снимает golden-фикстуры расписания с оригинального meal-plan.jsx (тикет 1.1).
//
// Расчётная часть meal-plan.jsx (от `const RECIPES = {` до `const DEFAULT_SETTINGS`)
// выполняется дословно в изолированном vm-контексте — без ручного переписывания.
// Результат печатается в stdout как Python-модуль:
//
//   TZ=UTC node tools/golden/capture_scheduling.js > tests/golden_scheduling.py
//
// Вывод не должен зависеть от TZ — проверяется повторным запуском с TZ=Europe/Berlin.
'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const vm = require('vm');

const SOURCE_PATH = path.join(__dirname, '..', '..', 'meal-plan.jsx');
const START_MARKER = 'const RECIPES = {';
const END_MARKER = 'const DEFAULT_SETTINGS';

const source = fs.readFileSync(SOURCE_PATH, 'utf8');
const start = source.indexOf(START_MARKER);
const end = source.indexOf(END_MARKER);
if (start < 0 || end < 0 || end <= start) {
  throw new Error(`meal-plan.jsx: не найдены маркеры "${START_MARKER}" / "${END_MARKER}"`);
}
const ctx = vm.createContext({});
vm.runInContext(source.slice(start, end), ctx);
const js = vm.runInContext('({ mealsForDate, dishIndexForDate, dayTotals, addDays, toISO })', ctx);

// Как в MealPlanApp: anchor = new Date(settings.mealAnchorDate + 'T00:00:00') (стр. 540).
function parseISO(iso) {
  return new Date(iso + 'T00:00:00');
}

// Повтор логики компонента, которая не вынесена в функции оригинала.
function cookWeekdaysOf(s) {
  // стр. 549
  return s.cook_count === 1 ? [s.cook_weekday1] : [s.cook_weekday1, s.cook_weekday2];
}
function isDayZero(date, anchor) {
  // стр. 542 (dayZero = anchor - 1) и стр. 708 (diffDays(dayZero, date) === 0)
  return js.toISO(date) === js.toISO(js.addDays(anchor, -1));
}

function rangeISO(fromISO, days) {
  const from = parseISO(fromISO);
  return Array.from({ length: days }, (_, i) => js.toISO(js.addDays(from, i)));
}

const DEFAULT_2X = {
  meal_anchor_date: '2026-11-05', // Чт
  cook_count: 2,
  cook_weekday1: 3, // Ср
  cook_weekday2: 6, // Сб
  shopping_timing: 'same',
};

// Полный период чередования завтраков (2), ротации блюд (5) и недели (7).
const SWEEP_DAYS = 70;

const SCENARIOS = [
  {
    name: 'default_2x',
    why: 'Готовка Ср/Сб, окна Чт–Сб (3 дня) и Вс–Ср (4 дня). Точечные случаи на перестановку рыбных блюд.',
    settings: DEFAULT_2X,
    cases: [
      { date: '2026-11-04', tags: ['day_zero', 'cook_day'] },
      { date: '2026-11-05', tags: ['anchor', 'first_after_cook', 'fish_reorder'] }, // k=3 (приоритет 2) в начало окна
      { date: '2026-11-07', tags: ['cook_day', 'last_of_window', 'fish_reorder'] },
      { date: '2026-11-08', tags: ['first_after_cook', 'fish_reorder', 'fish_tie'] }, // k=5 и k=2 — оба приоритет 1
      { date: '2026-11-09', tags: ['regular', 'fish_reorder', 'fish_tie'] },
      { date: '2026-11-10', tags: ['regular', 'fish_reorder'] },
      { date: '2026-11-11', tags: ['cook_day', 'last_of_window', 'fish_reorder'] },
      { date: '2026-11-12', tags: ['first_after_cook', 'natural_order'] },
      { date: '2026-11-13', tags: ['regular', 'fish_reorder'] },
      { date: '2026-11-14', tags: ['cook_day', 'last_of_window', 'fish_reorder'] },
      { date: '2026-11-15', tags: ['first_after_cook', 'fish_reorder'] }, // k=3 из середины окна в начало
      { date: '2026-11-17', tags: ['regular', 'fish_reorder'] },
    ],
  },
  {
    name: 'default_2x_sweep',
    why: '70 дней подряд: переход на зимнее время в Европе (2026-10-25), день 0, якорь, оба окна.',
    settings: DEFAULT_2X,
    cases: rangeISO('2026-10-20', SWEEP_DAYS).map((date) => ({ date, tags: ['sweep'] })),
  },
  {
    name: 'single_1x_sunday',
    why: 'cook_count=1, готовка Вс — окно 7 дней. cook_weekday2=6 задан, но должен игнорироваться.',
    settings: {
      meal_anchor_date: '2026-11-02', // Пн
      cook_count: 1,
      cook_weekday1: 7,
      cook_weekday2: 6,
      shopping_timing: 'same',
    },
    cases: rangeISO('2026-10-26', SWEEP_DAYS).map((date) => ({ date, tags: ['sweep'] })),
  },
  {
    name: 'adjacent_2x',
    why: 'Готовка Пн/Вт — окна 1 и 6 дней; 70 дней через переход на летнее время (2027-03-28).',
    settings: {
      meal_anchor_date: '2027-03-25', // Чт
      cook_count: 2,
      cook_weekday1: 1,
      cook_weekday2: 2,
      shopping_timing: 'before',
    },
    cases: rangeISO('2027-03-15', SWEEP_DAYS).map((date) => ({ date, tags: ['sweep'] })),
  },
  {
    name: 'anchor_mid_window',
    why: 'Якорь Пн посреди окна: день 0 (Вс) — не день готовки; даты до якоря (отрицательная разница).',
    settings: {
      meal_anchor_date: '2026-11-09', // Пн
      cook_count: 2,
      cook_weekday1: 3,
      cook_weekday2: 6,
      shopping_timing: 'same',
    },
    cases: rangeISO('2026-10-28', 14).map((date) => ({
      date,
      tags: date === '2026-11-08' ? ['day_zero'] : date < '2026-11-08' ? ['before_anchor'] : ['regular'],
    })),
  },
  {
    name: 'same_weekday_twice',
    why: 'cook_count=2 с одинаковыми днями (Чт/Чт) — достижимо из UI, окно вырождается в 7 дней.',
    settings: {
      meal_anchor_date: '2026-11-05',
      cook_count: 2,
      cook_weekday1: 4,
      cook_weekday2: 4,
      shopping_timing: 'same',
    },
    cases: rangeISO('2026-11-04', 14).map((date) => ({ date, tags: ['sweep'] })),
  },
];

function capture(scenario) {
  const anchor = parseISO(scenario.settings.meal_anchor_date);
  const cookWeekdays = cookWeekdaysOf(scenario.settings);
  return scenario.cases.map(({ date, tags }) => {
    const d = parseISO(date);
    const meals = js.mealsForDate(d, anchor, cookWeekdays);
    const totals = js.dayTotals(meals);
    const result = {
      date,
      tags,
      cycle_index: meals.k,
      natural_cycle_index: js.dishIndexForDate(d, anchor),
      breakfast: meals.breakfast,
      lunch: meals.lunch,
      dinner: meals.dinner,
      is_day_zero: isDayZero(d, anchor),
      kcal_total: totals.kcal,
      protein_total: totals.protein,
    };
    if (tags.includes('fish_reorder') && result.cycle_index === result.natural_cycle_index) {
      throw new Error(`${scenario.name} ${date}: помечен fish_reorder, но перестановки нет`);
    }
    if (tags.includes('day_zero') && !result.is_day_zero) {
      throw new Error(`${scenario.name} ${date}: помечен day_zero, но это не anchor - 1`);
    }
    return result;
  });
}

function py(value) {
  if (value === null || value === undefined) return 'None';
  if (value === true) return 'True';
  if (value === false) return 'False';
  if (typeof value === 'number' || typeof value === 'string') return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(py).join(', ') + ']';
  return '{' + Object.keys(value).map((k) => `${JSON.stringify(k)}: ${py(value[k])}`).join(', ') + '}';
}

const sha256 = crypto.createHash('sha256').update(source).digest('hex');
const out = [];
out.push('"""Golden-фикстуры расписания, снятые с meal-plan.jsx (тикет 1.1).');
out.push('');
out.push('СГЕНЕРИРОВАНО — не редактировать руками. Перегенерация:');
out.push('    TZ=UTC node tools/golden/capture_scheduling.js > tests/golden_scheduling.py');
out.push('');
out.push(`Источник: meal-plan.jsx, sha256 ${sha256}`);
out.push('');
out.push('Поля случая: cycle_index — meals.k после сортировки по fishPriority;');
out.push('natural_cycle_index — dishIndexForDate (до сортировки); is_day_zero — anchor - 1,');
out.push('UI не показывает меню, но mealsForDate для этой даты всё равно считается.');
out.push('"""');
out.push('');
out.push('# fmt: off');
out.push('SCENARIOS = [');
for (const scenario of SCENARIOS) {
  const cases = capture(scenario);
  out.push('    {');
  out.push(`        "name": ${py(scenario.name)},`);
  out.push(`        "why": ${py(scenario.why)},`);
  out.push(`        "settings": ${py(scenario.settings)},`);
  out.push('        "cases": [');
  for (const c of cases) out.push(`            ${py(c)},`);
  out.push('        ],');
  out.push('    },');
}
out.push(']');
out.push('# fmt: on');
process.stdout.write(out.join('\n') + '\n');
