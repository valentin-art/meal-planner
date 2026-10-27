// Общие помощники тестов: рендер в jsdom, клики, мок fetch по фикстурам.

import type { ReactElement } from 'react';
import { act } from 'react';
import { type Root, createRoot } from 'react-dom/client';
import { vi } from 'vitest';

import type { AppState, IngredientsCatalog, Recipes } from '../api-client';
import fixtures from './ui-states.json';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

export type Scenario = (typeof fixtures.scenarios)[number];
export const SCENARIOS: Scenario[] = fixtures.scenarios;
export const RECIPES = fixtures.recipes as unknown as Recipes;
export const CATALOG = fixtures.ingredients as IngredientsCatalog;

/** AppState из фикстуры сценария для запроса url. */
export function fullState(scenario: Scenario, url = 'api/state'): AppState {
  const response = (scenario.responses as Record<string, unknown>)[url];
  if (!response) throw new Error(`Нет ответа в фикстуре ${scenario.name} для ${url}`);
  return response as AppState;
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** fetch, который отвечает только на URL из фикстуры сценария. */
export function mockFetchFromScenario(scenario: Scenario) {
  const fetchMock = vi.fn(async (url: string) =>
    jsonResponse(url === 'api/recipes' ? RECIPES : url === 'api/ingredients' ? CATALOG : fullState(scenario, url)),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/** Даёт отработать промисам (fetch, setState) внутри act. */
export async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

// Элементы, после которых в снапшоте начинается новая строка (вёрстки в jsdom нет).
const BLOCK_TAGS = new Set(['DIV', 'SECTION', 'HEADER', 'NAV', 'P', 'H1', 'H2', 'H3', 'UL', 'OL', 'LI', 'LABEL', 'BUTTON', 'OPTION', 'BR']);

/** Текст элемента построчно, по блочным элементам: для снапшотов с читаемым diff. */
export function lines(element: Element): string {
  let out = '';
  const visit = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      out += node.textContent;
      return;
    }
    const block = node instanceof Element && BLOCK_TAGS.has(node.tagName);
    if (block) out += '\n';
    node.childNodes.forEach(visit);
    if (block) out += '\n';
  };
  visit(element);
  return out
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .join('\n');
}

/**
 * Как lines(), но поля формы — значениями: «[текст]», у списка — выбранный
 * пункт, у флажка — [x] / [ ]. Иначе в снапшот попали бы все пункты справочника.
 */
export function formLines(element: Element): string {
  const copy = element.cloneNode(true) as Element;
  const originals = element.querySelectorAll('input, textarea, select');
  copy.querySelectorAll('input, textarea, select').forEach((field, i) => {
    const original = originals[i] as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
    const shown =
      original instanceof HTMLSelectElement
        ? (original.selectedOptions[0]?.textContent ?? '')
        : original instanceof HTMLInputElement && original.type === 'checkbox'
          ? (original.checked ? 'x' : ' ')
          : original.value;
    const span = document.createElement('span');
    span.textContent = ` [${shown}] `;
    field.replaceWith(span);
  });
  return lines(copy);
}

export async function render(element: ReactElement) {
  const container = document.createElement('div');
  document.body.append(container);
  const root: Root = createRoot(container);
  await act(async () => root.render(element));
  await flush();
  return {
    container,
    text: () => container.textContent ?? '',
    lines: () => lines(container),
    unmount: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
}

export function buttons(container: HTMLElement, text: string | RegExp): HTMLButtonElement[] {
  return [...container.querySelectorAll('button')].filter((b) =>
    typeof text === 'string' ? b.textContent?.includes(text) : text.test(b.textContent ?? ''),
  );
}

export async function click(element: HTMLElement | undefined) {
  if (!element) throw new Error('Нет элемента для клика');
  await act(async () => element.click());
  await flush();
}

export async function change(element: HTMLInputElement | HTMLSelectElement, value: string) {
  // React следит за value через свой сеттер — меняем через нативный.
  const proto = element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(element, value);
  await act(async () => {
    element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
  });
  await flush();
}
