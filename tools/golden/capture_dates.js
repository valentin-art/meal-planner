// Снимает golden-фикстуры функций дат с оригинального meal-plan.jsx (тикет 1.4).
//
//   TZ=UTC node tools/golden/capture_dates.js > tests/golden_dates.py
//
// Вывод не должен зависеть от TZ — проверяется повторным запуском с TZ=Europe/Berlin.
'use strict';

const { loadJsx } = require('./load_jsx');
const { py } = require('./py_literal');

const { js, sha256 } = loadJsx(
  '({ fishPriority, nearestCookDate, previousCookDate, nextDateForWeekday, addDays, toISO })'
);

// [1]…[7] — все дни поодиночке; пары — как в UI при cook_count=2,
// включая обратный порядок и одинаковые дни.
const WEEKDAY_SETS = [[1], [2], [3], [4], [5], [6], [7], [3, 6], [6, 3], [1, 2], [4, 4]];
const FROM = new Date('2026-11-02T00:00:00'); // Пн
const DAYS = 14; // каждый день недели дважды

const fishPriority = {};
for (let k = 1; k <= 5; k++) fishPriority[k] = js.fishPriority(k);

const cases = [];
for (const set of WEEKDAY_SETS) {
  for (let i = 0; i < DAYS; i++) {
    const from = js.addDays(FROM, i);
    const c = {
      cook_weekdays: set,
      from: js.toISO(from),
      nearest: js.toISO(js.nearestCookDate(set, from)),
      previous: js.toISO(js.previousCookDate(set, from)),
    };
    if (set.length === 1) c.next_for_weekday = js.toISO(js.nextDateForWeekday(set[0], from));
    cases.push(c);
  }
}

const out = [];
out.push('"""Golden-фикстуры функций дат, снятые с meal-plan.jsx (тикет 1.4).');
out.push('');
out.push('СГЕНЕРИРОВАНО — не редактировать руками. Перегенерация:');
out.push('    TZ=UTC node tools/golden/capture_dates.js > tests/golden_dates.py');
out.push('');
out.push(`Источник: meal-plan.jsx, sha256 ${sha256}`);
out.push('"""');
out.push('');
out.push('# fmt: off');
out.push(`FISH_PRIORITY = {${Object.keys(fishPriority).map((k) => `${k}: ${fishPriority[k]}`).join(', ')}}`);
out.push('');
out.push('COOK_DATE_CASES = [');
for (const c of cases) out.push(`    ${py(c)},`);
out.push(']');
out.push('# fmt: on');
process.stdout.write(out.join('\n') + '\n');
