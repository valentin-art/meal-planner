// Поведение нового MealPlanApp на замоканном fetch: какие запросы уходят на
// действия пользователя и что делается с ответом. Что именно показывается —
// см. ui-snapshots.test.tsx.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { AppState } from './api-client';
import MealPlanApp from './MealPlanApp';
import { act } from 'react';

import { CATALOG, RECIPES, SCENARIOS, buttons, change, click, flush, fullState, jsonResponse, render } from './__fixtures__/harness';

const scenario = SCENARIOS.find((s) => s.name === 'default_2x')!; // сегодня Ср 2026-11-11
const BASE = fullState(scenario);

type Call = { url: string; method: string; body: unknown };
let calls: Call[];
let handler: (url: string, init?: RequestInit) => Promise<Response> | Response;
let recipesResponse: () => Response;

/** Выпадающие списки дней готовки — в секции «Готовок в неделю». */
function cookSelects(container: HTMLElement): HTMLSelectElement[] {
  const section = buttons(container, '1 раз')[0].closest('section')!;
  return [...section.querySelectorAll('select')];
}

function withSettings(patch: Partial<AppState['settings']>): AppState {
  return { ...BASE, settings: { ...BASE.settings, ...patch }, version: BASE.version + 1 };
}

beforeEach(() => {
  // Часы браузера намеренно не совпадают с «сегодня» сервера.
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2031-01-01T12:00:00'));
  calls = [];
  handler = () => jsonResponse(BASE);
  recipesResponse = () => jsonResponse(RECIPES);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({
        url,
        method: init?.method ?? 'GET',
        body: init?.body ? JSON.parse(init.body as string) : undefined,
      });
      if (url === 'api/recipes') return recipesResponse();
      if (url === 'api/ingredients') return jsonResponse(CATALOG);
      return handler(url, init);
    }),
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it('грузит state и справочник рецептов по разу, «сегодня» — с сервера', async () => {
  const ui = await render(<MealPlanApp />);
  expect(calls).toEqual([
    { url: 'api/state', method: 'GET', body: undefined },
    { url: 'api/recipes', method: 'GET', body: undefined },
  ]);
  expect(ui.text()).toContain('сегодня — Среда');
  expect(buttons(ui.container, 'СЕГОДНЯ')[0].textContent).toContain('11 нояб.');
  ui.unmount();
});

it('все настройки — на своей вкладке, в «Блюдах» весь справочник', async () => {
  const ui = await render(<MealPlanApp />);
  expect(ui.container.querySelectorAll('input[type=date]')).toHaveLength(1); // «Перейти к дате»

  await click(buttons(ui.container, 'Закупки')[0]);
  expect(ui.container.querySelectorAll('select, input')).toHaveLength(0);
  expect(buttons(ui.container, 'За день до')).toHaveLength(0);
  expect(ui.text()).toContain('Ближайшая закупка');

  await click(buttons(ui.container, 'Настройки')[0]);
  expect(ui.container.querySelector<HTMLInputElement>('input[type=date]')!.value).toBe('2026-11-05');
  expect(cookSelects(ui.container)).toHaveLength(2);
  expect(buttons(ui.container, 'За день до')).toHaveLength(1);
  const dishes = [...ui.container.querySelectorAll('section')].find((sec) => sec.querySelector('h2')?.textContent === 'Блюда')!;
  expect([...dishes.querySelectorAll('li')].map((li) => li.firstChild?.textContent?.trim()).sort()).toEqual(
    Object.values(RECIPES).map((r) => r.name).sort(),
  );
  ui.unmount();
});

describe('настройки уходят в action-эндпоинты, ответ заменяет state', () => {
  it('«1 раз» — без второго дня, сервер оставит сохранённый', async () => {
    handler = (url) => (url.startsWith('api/actions') ? jsonResponse(withSettings({ cook_count: 1 })) : jsonResponse(BASE));
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Настройки')[0]);
    expect(cookSelects(ui.container)).toHaveLength(2);

    await click(buttons(ui.container, '1 раз')[0]);
    expect(calls.at(-1)).toEqual({
      url: 'api/actions/set-cook-schedule',
      method: 'POST',
      body: { cook_count: 1, cook_weekday1: 3 },
    });
    expect(cookSelects(ui.container)).toHaveLength(1);
    ui.unmount();
  });

  it('дни готовки — полный набор', async () => {
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Настройки')[0]);
    const [day1, day2] = cookSelects(ui.container);

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
    await click(buttons(ui.container, 'Настройки')[0]);
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
    await click(buttons(ui.container, 'Настройки')[0]);
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

  it('цель на день: пустое поле — null, отклонение в итоге дня с сервера', async () => {
    const targeted = fullState(SCENARIOS.find((s) => s.name === 'nutrition_target')!);
    handler = (url) => (url.startsWith('api/actions') ? jsonResponse(targeted) : jsonResponse(BASE));
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Настройки')[0]);
    const kcal = ui.container.querySelector<HTMLInputElement>('input[aria-label="Ккал"]')!;
    expect(kcal.value).toBe('');

    await change(kcal, '2000');
    await click(buttons(ui.container, 'Применить')[0]);
    expect(calls.at(-1)).toEqual({
      url: 'api/actions/set-nutrition-target',
      method: 'POST',
      body: { daily_kcal_target: 2000, daily_protein_target: null },
    });
    // поля — из ответа сервера
    expect(ui.container.querySelector<HTMLInputElement>('input[aria-label="Белок, г"]')!.value).toBe('100');

    await click(buttons(ui.container, 'Расписание')[0]);
    expect(ui.container.textContent).toContain('Итого: ≈1860 ккал (−140) · 134г белка (+34)');
    ui.unmount();
  });

  it('порции под цель (8.5): флажок шлёт действие, множитель и граммы — с сервера', async () => {
    const scaled = fullState(SCENARIOS.find((s) => s.name === 'portion_scaling')!);
    handler = (url) => (url === 'api/actions/set-portion-scaling' ? jsonResponse(scaled) : jsonResponse(BASE));
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Настройки')[0]);
    const box = ui.container.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
    expect(box.checked).toBe(false);
    expect(ui.text()).toContain('Порции — как в рецептах.');

    await click(box);
    expect(calls.at(-1)).toEqual({ url: 'api/actions/set-portion-scaling', method: 'POST', body: { enabled: true } });
    expect(ui.text()).toContain('Порции — под 2400 ккал. Изменение — с Вс 15 нояб., купленное не меняется.');

    await click(buttons(ui.container, 'Меню')[0]);
    const lunch = scaled.schedule[0];
    const portion = scaled.scaled_recipes.find((r) => r.recipe_id === lunch.lunch_id && r.scale === lunch.lunch_scale)!;
    expect(ui.text()).toContain(`≈${portion.kcal} ккал · ${portion.protein}г белка · порция ×${lunch.lunch_scale}`);
    ui.unmount();
  });

  it('«Сегодня» — варианты с сервера, выбор уходит в set-cycle-position', async () => {
    const { options, current } = BASE.today_position;
    const moved = { ...withSettings({ meal_anchor_date: options[1].anchor }), today_position: { options, current: options[1].dish } };
    handler = (url) => (url.startsWith('api/actions') ? jsonResponse(moved) : jsonResponse(BASE));
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Настройки')[0]);
    const today = ui.container.querySelector('section')!.querySelector('select')!;
    expect([...today.options].map((o) => Number(o.value))).toEqual(options.map((o) => o.dish));
    expect(today.value).toBe(String(current));
    expect(today.options[0].textContent).toBe('день 0 · готовка, цикл с завтра');

    await change(today, String(options[1].dish));
    expect(calls.at(-1)).toEqual({
      url: 'api/actions/set-cycle-position',
      method: 'POST',
      body: { dish: options[1].dish },
    });
    // оба поля показывают одно состояние
    expect(today.value).toBe(String(options[1].dish));
    expect(ui.container.querySelector<HTMLInputElement>('input[type=date]')!.value).toBe(options[1].anchor);
    ui.unmount();
  });

  it('после «Показать ещё» действие просит тот же диапазон', async () => {
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Показать ещё')[0]);
    expect(calls.at(-1)?.url).toBe('api/state?from=2026-11-11&to=2026-12-08');

    await click(buttons(ui.container, 'Настройки')[0]);
    await click(buttons(ui.container, 'За день до')[0]);
    expect(calls.at(-1)?.url).toBe('api/actions/set-shopping-timing?from=2026-11-11&to=2026-12-08');
    ui.unmount();
  });
});

it('пропуск готовки: кнопка в «Закупках», отмена — в баннере', async () => {
  const skipped = fullState(SCENARIOS.find((s) => s.name === 'skipped_2x')!);
  handler = (url) => (url === 'api/actions/skip-next-cook' ? jsonResponse(skipped) : jsonResponse(BASE));
  const ui = await render(<MealPlanApp />);
  expect(ui.text()).not.toContain('пропущена');
  await click(buttons(ui.container, 'Закупки')[0]);

  await click(buttons(ui.container, 'Пропустить эту готовку')[0]);
  expect(calls.at(-1)).toEqual({ url: 'api/actions/skip-next-cook', method: 'POST', body: {} });
  expect(ui.text()).toContain('пропущена');
  expect(buttons(ui.container, 'Пропустить эту готовку')).toHaveLength(0); // один пропуск

  await click(buttons(ui.container, 'Отменить пропуск')[0]);
  expect(calls.at(-1)).toEqual({ url: 'api/actions/unskip-cook', method: 'POST', body: {} });
  expect(ui.text()).not.toContain('пропущена');
  ui.unmount();
});

it('перемешивание цикла: кнопки в «Цикле из 5 блюд», подпись — с сервера', async () => {
  const shuffled = fullState(SCENARIOS.find((s) => s.name === 'cycle_shuffled')!);
  handler = (url) => (url === 'api/actions/reshuffle-cycle' ? jsonResponse(shuffled) : jsonResponse(BASE));
  const ui = await render(<MealPlanApp />);
  await click(buttons(ui.container, 'Настройки')[0]);
  expect(ui.text()).toContain('Блюда идут по кругу 1→5.');
  expect(buttons(ui.container, 'Вернуть порядок 1→5')).toHaveLength(0);

  await click(buttons(ui.container, 'Перемешать порядок')[0]);
  expect(calls.at(-1)).toEqual({ url: 'api/actions/reshuffle-cycle', method: 'POST', body: {} });
  expect(ui.text()).toContain('Порядок перемешан');
  expect(ui.text()).toContain('Изменение — с Чт 19 нояб.');

  await click(buttons(ui.container, 'Вернуть порядок 1→5')[0]);
  expect(calls.at(-1)).toEqual({ url: 'api/actions/reset-cycle-order', method: 'POST', body: {} });
  ui.unmount();
});

describe('ошибки', () => {
  it('422 — сообщение сервера на экране, state прежний', async () => {
    const detail = [{ type: 'value_error', loc: ['body', 'cook_weekday2'], msg: 'При двух готовках в неделю нужен второй день готовки' }];
    handler = (url) => (url.startsWith('api/actions') ? jsonResponse({ detail }, 422) : jsonResponse(BASE));
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Настройки')[0]);
    await click(buttons(ui.container, '2 раза')[0]);

    // у контролов готовки, а не у закупки
    const cook = cookSelects(ui.container)[0].closest('section')!;
    const shopping = buttons(ui.container, 'За день до')[0].closest('section')!;
    expect(cook.querySelector('[role=alert]')?.textContent).toBe(
      'Не удалось сохранить: При двух готовках в неделю нужен второй день готовки',
    );
    expect(shopping.querySelector('[role=alert]')).toBeNull();
    expect(ui.container.querySelectorAll('[role=alert]')).toHaveLength(1);
    expect(cookSelects(ui.container)).toHaveLength(2); // по-прежнему 2 раза
    ui.unmount();
  });

  it('пустая дата якоря — 422, поле показывает сохранённую', async () => {
    const detail = [{ type: 'date_from_datetime_parsing', loc: ['body', 'meal_anchor_date'], msg: 'Input should be a valid date' }];
    handler = (url) => (url.startsWith('api/actions') ? jsonResponse({ detail }, 422) : jsonResponse(BASE));
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Настройки')[0]);
    const anchor = ui.container.querySelector<HTMLInputElement>('input[type=date]')!;
    await change(anchor, '');
    expect(calls.at(-1)?.body).toEqual({ meal_anchor_date: '' });
    expect(anchor.closest('section')!.querySelector('[role=alert]')?.textContent).toBe(
      'Не удалось сохранить: Input should be a valid date',
    );
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

it('справочник не загрузился — ошибка видна, «Повторить» догружает его', async () => {
  // state отвечает позже справочника: его успех не должен скрыть ошибку
  recipesResponse = () => new Response('Bad Gateway', { status: 502 });
  handler = async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    return jsonResponse(BASE);
  };
  const ui = await render(<MealPlanApp />);
  await flush();
  expect(ui.text()).toContain('Не удалось загрузить план: HTTP 502');

  recipesResponse = () => jsonResponse(RECIPES);
  await click(buttons(ui.container, 'Повторить')[0]);
  await flush();
  expect(calls.filter((c) => c.url === 'api/recipes')).toHaveLength(2);
  expect(ui.text()).toContain('сегодня — Среда');
  ui.unmount();
});

describe('даты', () => {
  it('дата вне загруженного диапазона — новый диапазон с неё', async () => {
    const ui = await render(<MealPlanApp />);
    const goTo = ui.container.querySelectorAll<HTMLInputElement>('input[type=date]')[0];
    await change(goTo, '2027-01-15');
    expect(calls.at(-1)).toEqual({ url: 'api/state?from=2027-01-15&to=2027-01-28', method: 'GET', body: undefined });
    ui.unmount();
  });

  it('дата внутри диапазона — без запроса', async () => {
    const ui = await render(<MealPlanApp />);
    const goTo = ui.container.querySelectorAll<HTMLInputElement>('input[type=date]')[0];
    await change(goTo, '2026-11-14');
    expect(calls.map((c) => c.url)).toEqual(['api/state', 'api/recipes']);
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
  await click(buttons(ui.container, 'Настройки')[0]);
  await click(buttons(ui.container, 'За день до')[0]);
  releaseGet();
  await click(buttons(ui.container, 'Настройки')[0]); // перерисовать после ответа GET

  const before = buttons(ui.container, 'За день до')[0];
  expect(before.style.color).toBe('rgb(255, 255, 255)'); // выбрано — значит, ответ действия
  ui.unmount();
});

// ---------- Рецепты в «Блюдах» (5.5) ----------

type Rendered = Awaited<ReturnType<typeof render>>;
type Recipe = (typeof RECIPES)[string];

function findRow(ui: Rendered, name: string): HTMLLIElement | undefined {
  return [...ui.container.querySelectorAll('li')].find((li) => li.firstChild?.textContent?.trim() === name);
}

/** Строка рецепта в «Блюдах» — по названию. */
function dishRow(ui: Rendered, name: string): HTMLLIElement {
  const row = findRow(ui, name);
  if (!row) throw new Error(`Нет рецепта «${name}» в «Блюдах»`);
  return row;
}

async function openEditor(ui: Rendered, name: string) {
  await click(buttons(ui.container, 'Настройки')[0]);
  await click(buttons(dishRow(ui, name), 'Изменить')[0]);
}

/** Поле формы по началу подписи или по aria-label. */
function field<T extends HTMLElement = HTMLInputElement>(ui: Rendered, label: string): T {
  const byAria = ui.container.querySelector<T>(`[aria-label="${label}"]`);
  if (byAria) return byAria;
  const wrap = [...ui.container.querySelectorAll('label')].find((l) => l.textContent?.startsWith(label));
  const control = wrap?.querySelector<T>('input, textarea, select');
  if (!control) throw new Error(`Нет поля «${label}»`);
  return control;
}

async function type(element: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(element, value);
  await act(async () => {
    element.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await flush();
}

/** Задержка предпросмотра ккал в редакторе — 300 мс. */
async function waitPreview() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 350));
  });
}

function saved(recipe: Recipe, state: AppState = BASE) {
  return jsonResponse({ state, recipe });
}

describe('редактор рецепта', () => {
  const l1 = RECIPES.l1;

  it('правка уходит в save-recipe целиком, ответ — в справочник', async () => {
    handler = (url) => (url === 'api/actions/save-recipe' ? saved({ ...l1, name: 'Курица по-новому' }) : jsonResponse(BASE));
    const ui = await render(<MealPlanApp />);
    await openEditor(ui, l1.name);
    expect(field(ui, 'Название').value).toBe(l1.name);

    await type(field(ui, 'Название'), 'Курица по-новому');
    await type(field(ui, 'Количество 1'), '200,5');
    await click(buttons(ui.container, 'Сохранить')[0]);

    expect(calls.find((c) => c.url === 'api/actions/save-recipe')).toEqual({
      url: 'api/actions/save-recipe',
      method: 'POST',
      body: {
        id: 'l1',
        name: 'Курица по-новому',
        kind: 'lunch',
        is_fish: false,
        ingredients: l1.ingredients.map((i, n) => ({ ingredient_id: i.ingredient_id, qty: n === 0 ? 200.5 : i.qty })),
        steps: l1.steps,
        storage: { ...l1.storage, after: l1.storage!.after ?? null },
      },
    });
    // редактор закрыт, в «Блюдах» — рецепт из ответа сервера
    expect(buttons(ui.container, 'Сохранить')).toHaveLength(0);
    expect(dishRow(ui, 'Курица по-новому')).toBeTruthy();
    ui.unmount();
  });

  it('ошибки 422 — у своих полей, форма остаётся открытой', async () => {
    handler = (url) =>
      url === 'api/actions/save-recipe'
        ? jsonResponse(
            {
              detail: [
                { type: 'value_error', loc: ['body', 'ingredients', 0, 'qty'], msg: 'Количество должно быть больше нуля' },
                { type: 'value_error', loc: ['body', 'name'], msg: 'Название не может быть пустым' },
              ],
            },
            422,
          )
        : jsonResponse(BASE);
    const ui = await render(<MealPlanApp />);
    await openEditor(ui, l1.name);
    await type(field(ui, 'Количество 1'), '');
    await click(buttons(ui.container, 'Сохранить')[0]);

    const qtyLine = field(ui, 'Количество 1').closest('div')!.parentElement!;
    expect(qtyLine.textContent).toContain('Количество должно быть больше нуля');
    expect(field(ui, 'Название').closest('section')!.textContent).toContain('Название не может быть пустым');
    expect(buttons(ui.container, 'Сохранить')).toHaveLength(1);
    expect(calls.find((c) => c.url === 'api/actions/save-recipe')!.body).toMatchObject({
      ingredients: expect.arrayContaining([{ ingredient_id: l1.ingredients[0].ingredient_id, qty: 0 }]),
    });
    ui.unmount();
  });

  it('новый ужин — без id, тип из кнопки', async () => {
    handler = (url) =>
      url === 'api/actions/save-recipe' ? saved({ ...RECIPES.d1, id: 'd6', name: 'Тофу с рисом' }) : jsonResponse(BASE);
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Настройки')[0]);
    await click(buttons(ui.container, 'Новый ужин')[0]);
    expect(ui.text()).toContain('Ужин: новый рецепт');
    expect(ui.text()).toContain('— ккал');

    await type(field(ui, 'Название'), 'Тофу с рисом');
    await change(field<HTMLSelectElement>(ui, 'Ингредиент 1'), String(CATALOG.ingredients[0].id));
    await type(field(ui, 'Количество 1'), '2');
    await type(field<HTMLTextAreaElement>(ui, 'Шаг 1'), 'Обжарьте.');
    await click(buttons(ui.container, 'Сохранить')[0]);

    expect(calls.find((c) => c.url === 'api/actions/save-recipe')!.body).toEqual({
      name: 'Тофу с рисом',
      kind: 'dinner',
      is_fish: false,
      ingredients: [{ ingredient_id: CATALOG.ingredients[0].id, qty: 2 }],
      steps: ['Обжарьте.'],
      storage: { keep: '', pan: '', micro: '', after: null },
    });
    expect(dishRow(ui, 'Тофу с рисом')).toBeTruthy();
    ui.unmount();
  });

  it('ккал — живой предпросмотр сервера, один запрос на серию правок', async () => {
    handler = (url) => (url === 'api/recipes/preview' ? jsonResponse({ kcal: 1234, protein: 99 }) : jsonResponse(BASE));
    const ui = await render(<MealPlanApp />);
    await openEditor(ui, l1.name);
    expect(ui.text()).toContain(`≈${l1.kcal} ккал · ${l1.protein}г белка`);

    for (const qty of ['1', '15', '150']) await type(field(ui, 'Количество 1'), qty);
    await waitPreview();
    const previews = calls.filter((c) => c.url === 'api/recipes/preview');
    expect(previews).toEqual([
      {
        url: 'api/recipes/preview',
        method: 'POST',
        body: { ingredients: l1.ingredients.map((i, n) => ({ ingredient_id: i.ingredient_id, qty: n === 0 ? 150 : i.qty })) },
      },
    ]);
    expect(ui.text()).toContain('≈1234 ккал · 99г белка');
    ui.unmount();
  });

  it('предупреждает, если блюдо уже куплено или в ближайшей закупке', async () => {
    const ui = await render(<MealPlanApp />);
    await openEditor(ui, RECIPES.l1.name); // bought в default_2x
    expect(ui.text()).toContain('Продукты на это блюдо уже куплены');

    await click(buttons(ui.container, 'Назад')[0]);
    await click(buttons(dishRow(ui, RECIPES.l3.name), 'Изменить')[0]); // next_trip
    expect(ui.text()).toContain('Блюдо в ближайшей закупке: список покупок изменится');

    await click(buttons(ui.container, 'Назад')[0]);
    await click(buttons(dishRow(ui, RECIPES.l2.name), 'Изменить')[0]); // не в плане
    expect(ui.container.querySelector('[role=status]')).toBeNull();
    ui.unmount();
  });
});

describe('архив рецептов', () => {
  const b3 = RECIPES.b3;

  it('«В архив» и «Вернуть» — действия, ответ сервера двигает рецепт', async () => {
    handler = (url) =>
      url === 'api/actions/archive-recipe'
        ? saved({ ...b3, status: 'archived' })
        : url === 'api/actions/restore-recipe'
          ? saved(b3)
          : jsonResponse(BASE);
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Настройки')[0]);
    expect(buttons(ui.container, 'Архив (')).toHaveLength(0);

    await click(buttons(dishRow(ui, b3.name), 'В архив')[0]);
    expect(calls.at(-1)).toEqual({ url: 'api/actions/archive-recipe', method: 'POST', body: { id: 'b3' } });
    expect(findRow(ui, b3.name)).toBeUndefined(); // в свёрнутом архиве

    await click(buttons(ui.container, 'Архив (1)')[0]);
    await click(buttons(dishRow(ui, b3.name), 'Вернуть')[0]);
    expect(calls.at(-1)).toEqual({ url: 'api/actions/restore-recipe', method: 'POST', body: { id: 'b3' } });
    expect(buttons(dishRow(ui, b3.name), 'Изменить')).toHaveLength(1);
    ui.unmount();
  });

  it('ошибка архива — у своего рецепта', async () => {
    const msg = '«Тёплый салат с креветками» стоит в цикле, позиция 3 — сначала замените его';
    handler = (url) =>
      url === 'api/actions/archive-recipe'
        ? jsonResponse({ detail: [{ type: 'value_error', loc: ['body', 'id'], msg }] }, 422)
        : jsonResponse(BASE);
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Настройки')[0]);
    await click(buttons(dishRow(ui, RECIPES.d3.name), 'В архив')[0]);
    expect(dishRow(ui, RECIPES.d3.name).textContent).toContain(msg);
    ui.unmount();
  });

  it('архивного рецепта нет в «Меню»', async () => {
    recipesResponse = () => jsonResponse({ ...RECIPES, b3: { ...b3, status: 'archived' } });
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Меню')[0]);
    expect(ui.text()).not.toContain(b3.name);
    await click(buttons(ui.container, 'Настройки')[0]);
    expect(buttons(ui.container, 'Архив (1)')).toHaveLength(1);
    ui.unmount();
  });
});

// ---------- Цикл и справочник ингредиентов (5.6) ----------

describe('позиции цикла', () => {
  it('выбор ужина сразу уходит в set-cycle-slot со списками позиции', async () => {
    const moved = { ...BASE, cycle_slots: BASE.cycle_slots.map((c) => (c.slot === 2 ? { ...c, dinner_ids: ['d5'] } : c)) };
    handler = (url) => (url === 'api/actions/set-cycle-slot' ? jsonResponse(moved) : jsonResponse(BASE));
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Настройки')[0]);

    const dinner = field<HTMLSelectElement>(ui, 'Ужин №2');
    expect([...dinner.options].map((o) => o.value)).toEqual(['d1', 'd2', 'd3', 'd4', 'd5']);
    await change(dinner, 'd5');
    expect(calls.at(-1)).toEqual({
      url: 'api/actions/set-cycle-slot',
      method: 'POST',
      body: { slot: 2, lunch_ids: ['l2'], dinner_ids: ['d5'] },
    });
    expect(field<HTMLSelectElement>(ui, 'Ужин №2').value).toBe('d5'); // из ответа сервера
    ui.unmount();
  });

  it('варианты (8.4b): «+ обед» добавляет первый свободный, «×» убирает, дата — с сервера', async () => {
    const pool = fullState(SCENARIOS.find((s) => s.name === 'slot_pool')!);
    handler = () => jsonResponse(pool);
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Настройки')[0]);
    const card = field(ui, 'Обед №1, вариант 1').closest('section')!;
    expect(card.textContent).toContain('Изменение — с Чт 19 нояб., купленное не меняется.');
    expect(field<HTMLSelectElement>(ui, 'Обед №1, вариант 2').value).toBe('l4');
    expect(buttons(ui.container, '×')).toHaveLength(2); // только у позиции с двумя обедами

    await click(buttons(ui.container, '+ обед')[0]);
    expect(calls.at(-1)!.body).toEqual({ slot: 1, lunch_ids: ['l1', 'l4', 'l2'], dinner_ids: ['d1'] });
    await click(buttons(ui.container, '×')[0]);
    expect(calls.at(-1)!.body).toEqual({ slot: 1, lunch_ids: ['l4'], dinner_ids: ['d1'] });
    ui.unmount();
  });

  it('ошибка сервера — в карточке цикла', async () => {
    handler = (url) =>
      url === 'api/actions/set-cycle-slot'
        ? jsonResponse({ detail: [{ type: 'value_error', loc: ['body', 'slot'], msg: 'Рыба — не больше чем в 3 позициях цикла из 5' }] }, 422)
        : jsonResponse(BASE);
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Настройки')[0]);
    expect(field(ui, 'Обед №1').closest('section')!.textContent).not.toContain('куплены');

    await change(field<HTMLSelectElement>(ui, 'Ужин №1'), 'd2');
    const card = field(ui, 'Обед №1').closest('section')!;
    expect(card.textContent).toContain('Не удалось сохранить: Рыба — не больше чем в 3 позициях цикла из 5');
    ui.unmount();
  });
});

describe('справочник ингредиентов', () => {
  const eggs = CATALOG.ingredients.find((i) => i.name === 'яйца')!;

  async function openCatalog(ui: Rendered) {
    await click(buttons(ui.container, 'Настройки')[0]);
    await click(buttons(ui.container, 'Справочник')[0]);
  }

  it('грузится, только когда раскрыт', async () => {
    const ui = await render(<MealPlanApp />);
    await click(buttons(ui.container, 'Настройки')[0]);
    expect(calls.filter((c) => c.url === 'api/ingredients')).toHaveLength(0);
    await click(buttons(ui.container, 'Справочник')[0]);
    expect(calls.filter((c) => c.url === 'api/ingredients')).toHaveLength(1);
    expect(buttons(ui.container, `Справочник (${CATALOG.ingredients.length})`)).toHaveLength(1);
    ui.unmount();
  });

  it('новый: пустые числа — null, после сохранения рецепты перезапрошены', async () => {
    const tofu = { ...eggs, id: 99, name: 'тофу', unit: 'г', category: 'Белок', used_in: [] };
    handler = (url) => (url === 'api/actions/save-ingredient' ? jsonResponse({ state: BASE, ingredient: tofu }) : jsonResponse(BASE));
    const ui = await render(<MealPlanApp />);
    await openCatalog(ui);
    await click(buttons(ui.container, 'Новый ингредиент')[0]);
    expect(field<HTMLSelectElement>(ui, 'Единица').disabled).toBe(false);

    await type(field(ui, 'Название'), 'тофу');
    await change(field<HTMLSelectElement>(ui, 'Категория'), 'Белок');
    await type(field(ui, 'Ккал на 100 г'), '76');
    const recipesBefore = calls.filter((c) => c.url === 'api/recipes').length;
    await click(buttons(ui.container, 'Сохранить')[0]);

    expect(calls.find((c) => c.url === 'api/actions/save-ingredient')!.body).toEqual({
      name: 'тофу',
      unit: 'г',
      category: 'Белок',
      kcal_100g: 76,
      protein_100g: null,
      grams_per_unit: null,
      store_piece: null,
      source: null,
    });
    expect(calls.filter((c) => c.url === 'api/recipes')).toHaveLength(recipesBefore + 1);
    expect(buttons(ui.container, `Справочник (${CATALOG.ingredients.length + 1})`)).toHaveLength(1);
    ui.unmount();
  });

  it('правка: с id, единица у ингредиента из рецептов недоступна, ошибка — у поля', async () => {
    handler = (url) =>
      url === 'api/actions/save-ingredient'
        ? jsonResponse({ detail: [{ type: 'value_error', loc: ['body', 'protein_100g'], msg: 'Белок на 100 г — от 0 до 100' }] }, 422)
        : jsonResponse(BASE);
    const ui = await render(<MealPlanApp />);
    await openCatalog(ui);
    const row = [...ui.container.querySelectorAll('li')].find((li) => li.firstChild?.textContent?.trim() === 'яйца')!;
    await click(buttons(row, 'Изменить')[0]);

    expect(ui.text()).toContain(`В рецептах: ${eggs.used_in.map((id) => RECIPES[id].name).join(', ')}`);
    expect(field<HTMLSelectElement>(ui, 'Единица').disabled).toBe(true);
    await type(field(ui, 'Белок на 100 г, г'), '200');
    await click(buttons(ui.container, 'Сохранить')[0]);

    expect(calls.find((c) => c.url === 'api/actions/save-ingredient')!.body).toMatchObject({ id: eggs.id, protein_100g: 200 });
    expect(field(ui, 'Белок на 100 г, г').closest('label')!.textContent).toContain('Белок на 100 г — от 0 до 100');
    ui.unmount();
  });
});
