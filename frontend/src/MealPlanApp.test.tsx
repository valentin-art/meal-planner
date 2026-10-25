// Поведение нового MealPlanApp на замоканном fetch: какие запросы уходят на
// действия пользователя и что делается с ответом. Что именно показывается —
// см. legacy-parity.test.tsx.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { AppState } from './api-client';
import MealPlanApp from './MealPlanApp';
import { SCENARIOS, buttons, change, click, fullState, jsonResponse, render } from './__fixtures__/harness';

const scenario = SCENARIOS.find((s) => s.name === 'default_2x')!; // сегодня Ср 2026-11-11
const BASE = fullState(scenario);

type Call = { url: string; method: string; body: unknown };
let calls: Call[];
let handler: (url: string, init?: RequestInit) => Promise<Response> | Response;

function withSettings(patch: Partial<AppState['settings']>): AppState {
  return { ...BASE, settings: { ...BASE.settings, ...patch }, version: BASE.version + 1 };
}

beforeEach(() => {
  // Часы браузера намеренно не совпадают с «сегодня» сервера.
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2031-01-01T12:00:00'));
  calls = [];
  handler = () => jsonResponse(BASE);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({
        url,
        method: init?.method ?? 'GET',
        body: init?.body ? JSON.parse(init.body as string) : undefined,
      });
      return handler(url, init);
    }),
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it('грузит state один раз и берёт «сегодня» с сервера', async () => {
  const ui = await render(<MealPlanApp />);
  expect(calls).toEqual([{ url: 'api/state', method: 'GET', body: undefined }]);
  expect(ui.text()).toContain('сегодня — Среда');
  expect(buttons(ui.container, 'СЕГОДНЯ')[0].textContent).toContain('11 нояб.');
  ui.unmount();
});

describe('настройки уходят в action-эндпоинты, ответ заменяет state', () => {
  it('«1 раз» — без второго дня, сервер оставит сохранённый', async () => {
    handler = (url) => (url.startsWith('api/actions') ? jsonResponse(withSettings({ cook_count: 1 })) : jsonResponse(BASE));
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Закупки')[0]);
    expect(ui.container.querySelectorAll('select')).toHaveLength(2);

    await click(buttons(ui.container, '1 раз')[0]);
    expect(calls.at(-1)).toEqual({
      url: 'api/actions/set-cook-schedule',
      method: 'POST',
      body: { cook_count: 1, cook_weekday1: 3 },
    });
    expect(ui.container.querySelectorAll('select')).toHaveLength(1);
    ui.unmount();
  });

  it('дни готовки — полный набор', async () => {
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Закупки')[0]);
    const [day1, day2] = ui.container.querySelectorAll('select');

    await change(day1, '5');
    expect(calls.at(-1)?.body).toEqual({ cook_count: 2, cook_weekday1: 5, cook_weekday2: 6 });
    await change(day2, '7');
    expect(calls.at(-1)).toMatchObject({
      url: 'api/actions/set-cook-schedule',
      body: { cook_count: 2, cook_weekday1: 3, cook_weekday2: 7 },
    });
    ui.unmount();
  });

  it('закупка', async () => {
    handler = (url) =>
      url.startsWith('api/actions') ? jsonResponse(withSettings({ shopping_timing: 'before' })) : jsonResponse(BASE);
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Закупки')[0]);
    await click(buttons(ui.container, 'За день до')[0]);
    expect(calls.at(-1)).toEqual({
      url: 'api/actions/set-shopping-timing',
      method: 'POST',
      body: { shopping_timing: 'before' },
    });
    ui.unmount();
  });

  it('якорь цикла', async () => {
    handler = (url) =>
      url.startsWith('api/actions') ? jsonResponse(withSettings({ meal_anchor_date: '2026-11-20' })) : jsonResponse(BASE);
    const ui = await render(<MealPlanApp />);
    const anchor = ui.container.querySelector<HTMLInputElement>('input[type=date]')!;
    expect(anchor.value).toBe('2026-11-05');

    await change(anchor, '2026-11-20');
    expect(calls.at(-1)).toEqual({
      url: 'api/actions/set-anchor-date',
      method: 'POST',
      body: { meal_anchor_date: '2026-11-20' },
    });
    expect(anchor.value).toBe('2026-11-20');
    ui.unmount();
  });

  it('после «Показать ещё» действие просит тот же диапазон', async () => {
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Показать ещё')[0]);
    expect(calls.at(-1)?.url).toBe('api/state?from=2026-11-11&to=2026-12-08');

    await click(buttons(ui.container, 'Закупки')[0]);
    await click(buttons(ui.container, 'За день до')[0]);
    expect(calls.at(-1)?.url).toBe('api/actions/set-shopping-timing?from=2026-11-11&to=2026-12-08');
    ui.unmount();
  });
});

describe('ошибки', () => {
  it('422 — сообщение сервера на экране, state прежний', async () => {
    const detail = [{ type: 'value_error', loc: ['body', 'cook_weekday2'], msg: 'При двух готовках в неделю нужен второй день готовки' }];
    handler = (url) => (url.startsWith('api/actions') ? jsonResponse({ detail }, 422) : jsonResponse(BASE));
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Закупки')[0]);
    await click(buttons(ui.container, '2 раза')[0]);

    expect(ui.text()).toContain('Не удалось сохранить: При двух готовках в неделю нужен второй день готовки');
    expect(ui.container.querySelectorAll('select')).toHaveLength(2); // по-прежнему 2 раза
    ui.unmount();
  });

  it('пустая дата якоря — 422, поле показывает сохранённую', async () => {
    const detail = [{ type: 'date_from_datetime_parsing', loc: ['body', 'meal_anchor_date'], msg: 'Input should be a valid date' }];
    handler = (url) => (url.startsWith('api/actions') ? jsonResponse({ detail }, 422) : jsonResponse(BASE));
    const ui = await render(<MealPlanApp />);
    const anchor = ui.container.querySelector<HTMLInputElement>('input[type=date]')!;
    await change(anchor, '');
    expect(calls.at(-1)?.body).toEqual({ meal_anchor_date: '' });
    expect(ui.text()).toContain('Не удалось сохранить: Input should be a valid date');
    expect(anchor.value).toBe('2026-11-05');
    ui.unmount();
  });

  it('ошибка загрузки — «Повторить»', async () => {
    handler = () => new Response('Bad Gateway', { status: 502 });
    const ui = await render(<MealPlanApp />);
    expect(ui.text()).toContain('Не удалось загрузить план: HTTP 502');

    handler = () => jsonResponse(BASE);
    await click(buttons(ui.container, 'Повторить')[0]);
    expect(ui.text()).toContain('сегодня — Среда');
    ui.unmount();
  });
});

describe('даты', () => {
  it('дата вне загруженного диапазона — новый диапазон с неё', async () => {
    const ui = await render(<MealPlanApp />);
    const goTo = ui.container.querySelectorAll<HTMLInputElement>('input[type=date]')[1];
    await change(goTo, '2027-01-15');
    expect(calls.at(-1)).toEqual({ url: 'api/state?from=2027-01-15&to=2027-01-28', method: 'GET', body: undefined });
    ui.unmount();
  });

  it('дата внутри диапазона — без запроса', async () => {
    const ui = await render(<MealPlanApp />);
    const goTo = ui.container.querySelectorAll<HTMLInputElement>('input[type=date]')[1];
    await change(goTo, '2026-11-14');
    expect(calls).toHaveLength(1);
    expect(buttons(ui.container, 'ВЫБРАНО')[0].textContent).toContain('14 нояб.');
    ui.unmount();
  });
});

it('медленный GET не затирает более свежий ответ действия', async () => {
  let releaseGet!: () => void;
  handler = (url) => {
    if (url.startsWith('api/actions')) return jsonResponse(withSettings({ shopping_timing: 'before' }));
    if (url === 'api/state') return jsonResponse(BASE);
    // «Показать ещё» отвечает после действия
    return new Promise<Response>((resolve) => {
      releaseGet = () => resolve(jsonResponse(BASE));
    });
  };
  const ui = await render(<MealPlanApp />);
  await click(buttons(ui.container, 'Показать ещё')[0]);
  await click(buttons(ui.container, 'Закупки')[0]);
  await click(buttons(ui.container, 'За день до')[0]);
  releaseGet();
  await click(buttons(ui.container, 'Закупки')[0]); // перерисовать после ответа GET

  const before = buttons(ui.container, 'За день до')[0];
  expect(before.style.color).toBe('rgb(255, 255, 255)'); // выбрано — значит, ответ действия
  ui.unmount();
});
