// Parity: новый MealPlanApp (данные с сервера) показывает ровно тот же текст,
// что замороженный артефакт meal-plan.jsx (всё считает сам в JS), на одних и
// тех же настройках и «сегодня». Ответы сервера — фикстуры от настоящего
// backend (tools/dump_ui_fixtures.py), их свежесть проверяет pytest.

import type { ComponentType } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SCENARIOS, type Scenario, buttons, click, mockFetchFromScenario, render } from './__fixtures__/harness';

type Rendered = Awaited<ReturnType<typeof render>>;

// Окружение артефакта Claude: настройки он читает и пишет через window.storage.
// Новое приложение его не знает — оно есть только здесь, для старого UI.
type StorageEntry = { key: string; value: string };

declare global {
  interface Window {
    storage?: {
      get(key: string): Promise<StorageEntry | null>;
      set(key: string, value: string): Promise<StorageEntry>;
    };
  }
}

function installArtifactStorage(settings: object | null) {
  const data = new Map<string, string>();
  if (settings) data.set('settings', JSON.stringify(settings));
  window.storage = {
    async get(key) {
      const value = data.get(key);
      return value === undefined ? null : { key, value };
    },
    async set(key, value) {
      data.set(key, value);
      return { key, value };
    },
  };
}

/** Одинаковый для обоих UI сценарий кликов; текст после каждого шага. */
async function walk(ui: Rendered): Promise<Record<string, string>> {
  const steps: Record<string, string> = {};
  steps['расписание'] = ui.text();

  await click(buttons(ui.container, 'Показать ещё')[0]);
  steps['расписание +14'] = ui.text();

  await click(buttons(ui.container, 'СЕГОДНЯ')[0]); // карточка дня → Меню
  steps['меню: сегодня'] = ui.text();

  // Каждый рецепт (блоки дня и карточки «Все …») по очереди: ингредиенты,
  // количества, хранение.
  const count = buttons(ui.container, /ккал/).length;
  for (let i = 0; i < count; i++) {
    const toggle = buttons(ui.container, /ккал/)[i];
    const name = toggle.textContent;
    await click(toggle);
    steps[`меню: рецепт ${i} ${name}`] = ui.text();
  }

  await click(buttons(ui.container, 'Закупки')[0]);
  steps['закупки'] = ui.text();
  return steps;
}

async function renderLegacy(scenario: Scenario): Promise<Rendered> {
  // Артефакт берёт «сегодня» из часов браузера, а DEFAULT_SETTINGS считает
  // при импорте модуля — поэтому часы ставим до импорта, модуль — заново.
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date(`${scenario.today}T12:00:00`));
  installArtifactStorage(scenario.legacy_settings); // у defaults — ничего не сохранено
  vi.resetModules();
  const { default: Legacy } = (await import('../../meal-plan.jsx')) as { default: ComponentType };
  return render(<Legacy />);
}

async function renderNew(scenario: Scenario): Promise<Rendered> {
  // Часы браузера намеренно врут: «сегодня» должно прийти с сервера.
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2031-01-01T12:00:00'));
  delete window.storage; // новому UI окружение артефакта не нужно
  mockFetchFromScenario(scenario);
  vi.resetModules();
  const { default: MealPlanApp } = await import('./MealPlanApp');
  return render(<MealPlanApp />);
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe.each(SCENARIOS)('$name (сегодня $today)', (scenario) => {
  it('новый UI показывает то же, что артефакт', async () => {
    const legacy = await renderLegacy(scenario);
    const expected = await walk(legacy);
    legacy.unmount();
    vi.useRealTimers();

    const fresh = await renderNew(scenario);
    const actual = await walk(fresh);
    fresh.unmount();

    expect(Object.keys(actual)).toEqual(Object.keys(expected));
    for (const step of Object.keys(expected)) {
      expect(actual[step], step).toBe(expected[step]);
    }
  });
});
