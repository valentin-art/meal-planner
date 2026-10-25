// Smoke-тест: копия артефакта рендерится на shim'е. Сетка безопасности
// для переделки в 3.2.

import './storage-shim';

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it } from 'vitest';

import MealPlanApp from './MealPlanApp.jsx';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  container = document.createElement('div');
  document.body.append(container);
});

afterEach(() => {
  container.remove();
});

async function render() {
  const root = createRoot(container);
  await act(async () => root.render(<MealPlanApp />));
  return root;
}

it('рендерит шапку и вкладки', async () => {
  const root = await render();
  const text = container.textContent ?? '';
  expect(text).toContain('Цикл из 5 блюд');
  for (const tab of ['Расписание', 'Меню', 'Закупки']) {
    expect(text).toContain(tab);
  }
  act(() => root.unmount());
});

function openTab(label: string) {
  const button = [...container.querySelectorAll('button')].find((b) =>
    b.textContent?.includes(label),
  );
  act(() => button!.click());
}

it('берёт сохранённые настройки из window.storage', async () => {
  await window.storage.set('settings', JSON.stringify({ cookCount: 1, cookWeekday1: 5 }));
  const root = await render();
  openTab('Закупки');
  const selects = container.querySelectorAll('select');
  expect(selects).toHaveLength(1); // cookCount 1 — второго дня нет
  expect(selects[0].value).toBe('5');
  act(() => root.unmount());
});

it('изменение настроек сохраняется через shim в localStorage', async () => {
  const root = await render();
  openTab('Закупки');
  const select = container.querySelector('select')!;
  await act(async () => {
    select.value = '2';
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
  expect(JSON.parse(localStorage.getItem('settings')!)).toMatchObject({ cookWeekday1: 2 });
  act(() => root.unmount());
});
