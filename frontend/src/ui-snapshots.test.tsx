// Что показывает MealPlanApp на ответах настоящего backend
// (__fixtures__/ui-states.json, tools/dump_ui_fixtures.py; свежесть проверяет
// pytest). Снапшоты — в git: изменение разметки видно в diff .snap и
// принимается через `npx vitest run -u` в том же коммите.
// Первая версия снапшотов снята, пока новый UI совпадал с meal-plan.jsx.

import { afterEach, describe, expect, it, vi } from 'vitest';

import MealPlanApp from './MealPlanApp';
import { SCENARIOS, type Scenario, buttons, click, lines, mockFetchFromScenario, render } from './__fixtures__/harness';

type Rendered = Awaited<ReturnType<typeof render>>;

async function renderApp(scenario: Scenario): Promise<Rendered> {
  // Часы браузера намеренно врут: «сегодня» должно прийти с сервера.
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2031-01-01T12:00:00'));
  mockFetchFromScenario(scenario);
  return render(<MealPlanApp />);
}

/** Меню на сегодня: переход с карточки дня в «Расписании». */
async function openTodayMenu(ui: Rendered) {
  await click(buttons(ui.container, 'СЕГОДНЯ')[0]);
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe.each(SCENARIOS)('$name (сегодня $today)', (scenario) => {
  it('вкладки', async () => {
    const ui = await renderApp(scenario);
    const steps: Record<string, string> = {};
    steps['расписание'] = ui.lines();

    await click(buttons(ui.container, 'Показать ещё')[0]);
    steps['расписание +14'] = ui.lines();

    await openTodayMenu(ui);
    steps['меню: сегодня'] = ui.lines();

    await click(buttons(ui.container, 'Закупки')[0]);
    steps['закупки'] = ui.lines();

    await click(buttons(ui.container, 'Настройки')[0]);
    steps['настройки'] = ui.lines();
    ui.unmount();

    expect(steps).toMatchSnapshot();
  });
});

// Справочник рецептов в фикстурах один на все сценарии, поэтому рецепты
// раскрываются один раз: блоки дня и карточки «Все …» по очереди.
it('рецепты: ингредиенты, шаги, хранение', async () => {
  const ui = await renderApp(SCENARIOS[0]);
  await openTodayMenu(ui);

  const recipes: Record<string, string> = {};
  const count = buttons(ui.container, /ккал/).length;
  for (let i = 0; i < count; i++) {
    const toggle = buttons(ui.container, /ккал/)[i];
    const name = toggle.textContent;
    await click(toggle);
    recipes[`${i} ${name}`] = lines(toggle.parentElement!); // открыт всегда один
  }
  ui.unmount();

  expect(recipes).toMatchSnapshot();
});
