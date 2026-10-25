// Граница фронтенда (аналог tests/test_architecture.py для core/): в коде
// приложения нет расчётов из старого артефакта и нет абсолютных URL к API.
// Расписание, дни готовки, суммы и список покупок считает только backend.

import { describe, expect, it } from 'vitest';

// Исходники приложения — без тестов и тестовых помощников.
const sources = import.meta.glob(['./**/*.{ts,tsx}', '!./**/*.test.*', '!./__fixtures__/**'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

const FORBIDDEN = [
  // функции и данные расчёта из meal-plan.jsx
  'mealsForDate',
  'dishIndexForDate',
  'breakfastForDate',
  'fishPriority',
  'nearestCookDate',
  'previousCookDate',
  'nextDateForWeekday',
  'dayTotals',
  'aggregate',
  'groupByCategory',
  'fmtQty',
  'RECIPES',
  'LUNCH_IDS',
  'DINNER_IDS',
  'FISH_LUNCH_IDS',
  'FISH_DINNER_IDS',
  'DEFAULT_SETTINGS',
  // состояние — на сервере
  'window.storage',
  'localStorage',
];

// Ведущий '/' ломает приложение за HA Ingress: только 'api/...'.
const ABSOLUTE_API = /['"`]\/api\b/;

function violations(source: string): string[] {
  const found = FORBIDDEN.filter((name) => new RegExp(`\\b${name.replace('.', '\\.')}\\b`).test(source));
  if (ABSOLUTE_API.test(source)) found.push("абсолютный URL '/api'");
  return found;
}

// api-types — сгенерированные типы, пути OpenAPI в них — ключи, не запросы.
const EXCLUDED = ['./api-types.ts'];
const APP_SOURCES = Object.entries(sources).filter(([path]) => !EXCLUDED.includes(path));

it('исходники найдены', () => {
  expect(APP_SOURCES.map(([path]) => path)).toEqual(
    expect.arrayContaining(['./MealPlanApp.tsx', './api-client.ts', './useAppState.ts', './main.tsx']),
  );
});

describe.each(APP_SOURCES)('%s', (_path, source) => {
  it('без расчётов артефакта и абсолютных URL', () => {
    expect(violations(source)).toEqual([]);
  });
});

describe('проверка ловит нарушения', () => {
  it.each([
    'const m = mealsForDate(date, anchor, cookWeekdays);',
    'const t = dayTotals(meals);',
    'RECIPES[id].name',
    "await window.storage.get('settings')",
    "fetch('/api/state')",
    'fetch(`/api/actions/${name}`)',
  ])('%s', (source) => {
    expect(violations(source)).not.toEqual([]);
  });

  it.each(["fetch('api/state')", 'const recipes = state.recipes;', 'day.kcal_total'])('чисто: %s', (source) => {
    expect(violations(source)).toEqual([]);
  });
});
