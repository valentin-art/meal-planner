// Снимает golden-фикстуры списка покупок с оригинального meal-plan.jsx (тикеты 1.6, 1.7).
//
//   TZ=UTC node tools/golden/capture_shopping.js > tests/golden_shopping.py
//
// aggregate/groupByCategory/fmtQty/mealsForDate вызываются из JSX дословно.
// Код ShoppingTab (стр. 1029-1058) живёт в компоненте и повторён здесь со ссылками.
// Вывод не должен зависеть от TZ — проверяется повторным запуском с TZ=Europe/Berlin.
'use strict';

const { loadJsx } = require('./load_jsx');
const { py } = require('./py_literal');

const { js, sha256 } = loadJsx(
  '({ aggregate, groupByCategory, fmtQty, mealsForDate, nearestCookDate, addDays, diffDays, toISO, LUNCH_IDS, DINNER_IDS, RECIPES })'
);

function parseISO(iso) {
  return new Date(iso + 'T00:00:00');
}

// Те же конфигурации, что в golden_scheduling (без дубля default_2x_sweep).
const CONFIGS = [
  { name: 'default_2x', settings: { meal_anchor_date: '2026-11-05', cook_count: 2, cook_weekday1: 3, cook_weekday2: 6, shopping_timing: 'same' } },
  { name: 'single_1x_sunday', settings: { meal_anchor_date: '2026-11-02', cook_count: 1, cook_weekday1: 7, cook_weekday2: 6, shopping_timing: 'same' } },
  { name: 'adjacent_2x', settings: { meal_anchor_date: '2027-03-25', cook_count: 2, cook_weekday1: 1, cook_weekday2: 2, shopping_timing: 'before' } },
  { name: 'anchor_mid_window', settings: { meal_anchor_date: '2026-11-09', cook_count: 2, cook_weekday1: 3, cook_weekday2: 6, shopping_timing: 'same' } },
  { name: 'same_weekday_twice', settings: { meal_anchor_date: '2026-11-05', cook_count: 2, cook_weekday1: 4, cook_weekday2: 4, shopping_timing: 'same' } },
];

function cookWeekdaysOf(s) {
  // стр. 549
  return s.cook_count === 1 ? [s.cook_weekday1] : [s.cook_weekday1, s.cook_weekday2];
}

// Суммы до округления — повтор стр. 374-380 (aggregate отдаёт только округлённые).
function rawSums(entries) {
  const map = {};
  entries.forEach(({ id, portions }) => {
    js.RECIPES[id].ingredients.forEach((ing) => {
      const key = ing.name + '|' + ing.unit + '|' + ing.cat;
      map[key] = (map[key] || 0) + ing.qty * portions;
    });
  });
  return map;
}

function capture(config, todayISO) {
  const s = config.settings;
  const anchor = parseISO(s.meal_anchor_date);
  const cookWeekdays = cookWeekdaysOf(s);
  const today = parseISO(todayISO);

  // стр. 1029-1032
  const cookDate = js.nearestCookDate(cookWeekdays, today);
  const nextCookDate = js.nearestCookDate(cookWeekdays, js.addDays(cookDate, 1));
  const windowLen = js.diffDays(cookDate, nextCookDate);
  const windowDates = Array.from({ length: windowLen }, (_, i) => js.addDays(cookDate, i + 1));

  // стр. 1034-1048
  const lunchCount = {};
  const dinnerCount = {};
  const breakfastCount = {};
  windowDates.forEach((d) => {
    const m = js.mealsForDate(d, anchor, cookWeekdays);
    lunchCount[m.lunch] = (lunchCount[m.lunch] || 0) + 1;
    dinnerCount[m.dinner] = (dinnerCount[m.dinner] || 0) + 1;
    breakfastCount[m.breakfast] = (breakfastCount[m.breakfast] || 0) + 1;
  });
  const entries = [
    ...Object.entries(lunchCount).map(([id, portions]) => ({ id, portions })),
    ...Object.entries(dinnerCount).map(([id, portions]) => ({ id, portions })),
    ...Object.entries(breakfastCount).map(([id, portions]) => ({ id, portions })),
  ];
  const grouped = js.groupByCategory(js.aggregate(entries));
  const raw = rawSums(entries);
  if (grouped.reduce((n, g) => n + g.items.length, 0) !== Object.keys(raw).length) {
    throw new Error(`${config.name} ${todayISO}: rawSums не совпадает с aggregate по ключам`);
  }

  // стр. 1052-1056: текст "A + B" и тег "xN" — отображение, снимаем id и число.
  const cookItems = Object.entries(lunchCount).map(([id, count]) => {
    const k = js.LUNCH_IDS.indexOf(id) + 1;
    return { lunch: id, dinner: js.DINNER_IDS[k - 1], portions: count };
  });

  // стр. 1058
  const shoppingDate = s.shopping_timing === 'before' ? js.addDays(cookDate, -1) : cookDate;

  return {
    header: {
      config: config.name,
      settings: s,
      today: todayISO,
      cook_date: js.toISO(cookDate),
      next_cook_date: js.toISO(nextCookDate),
      window_start: js.toISO(windowDates[0]),
      window_end: js.toISO(windowDates[windowDates.length - 1]),
      shopping_date: js.toISO(shoppingDate),
      cook_items: cookItems,
      entries: entries.map((e) => [e.id, e.portions]),
    },
    groups: grouped.map((g) => ({
      category: g.cat,
      items: g.items.map((it) => ({
        name: it.name,
        unit: it.unit,
        raw_qty: raw[it.name + '|' + it.unit + '|' + it.cat],
        js_qty: it.qty,
      })),
    })),
  };
}

const cases = [];
for (const config of CONFIGS) {
  const anchor = parseISO(config.settings.meal_anchor_date);
  for (let i = -3; i <= 3; i++) cases.push(capture(config, js.toISO(js.addDays(anchor, i))));
}

const names = [...new Set(Object.values(js.RECIPES).flatMap((r) => r.ingredients.map((i) => i.name)))];
const nameOrder = [...names].sort((a, b) => a.localeCompare(b, 'ru'));

const fmtInputs = new Set();
Object.values(js.RECIPES).forEach((r) => r.ingredients.forEach((i) => fmtInputs.add(i.qty)));
cases.forEach((c) => c.groups.forEach((g) => g.items.forEach((it) => fmtInputs.add(it.js_qty))));
const fmtCases = [...fmtInputs].sort((a, b) => a - b).map((q) => [q, String(js.fmtQty(q))]);

const out = [];
out.push('"""Golden-фикстуры списка покупок, снятые с meal-plan.jsx (тикеты 1.6, 1.7).');
out.push('');
out.push('СГЕНЕРИРОВАНО — не редактировать руками. Перегенерация:');
out.push('    TZ=UTC node tools/golden/capture_shopping.js > tests/golden_shopping.py');
out.push('');
out.push(`Источник: meal-plan.jsx, sha256 ${sha256}`);
out.push('');
out.push('raw_qty — сумма в JS до округления; js_qty — после Math.round(qty*10)/10');
out.push('(справочно: Python округляет вверх, ceil до 0.1). cook_items и даты — для 1.7.');
out.push('"""');
out.push('');
out.push('# fmt: off');
out.push('SHOPPING_CASES = [');
for (const c of cases) {
  const header = py(c.header);
  out.push(`    {${header.slice(1, -1)}, "groups": [`);
  for (const g of c.groups) out.push(`        ${py(g)},`);
  out.push('    ]},');
}
out.push(']');
out.push('');
out.push(`NAME_ORDER = ${py(nameOrder)}`);
out.push('');
out.push('FMT_QTY_CASES = [');
for (const [q, s] of fmtCases) out.push(`    (${py(q)}, ${py(s)}),`);
out.push(']');
out.push('# fmt: on');
process.stdout.write(out.join('\n') + '\n');
