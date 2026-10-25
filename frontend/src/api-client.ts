// Тонкий клиент к backend. Никакой бизнес-логики: только запросы и типы.
//
// Все URL — относительные, без ведущего '/': за Home Assistant Ingress
// приложение отдаётся под произвольным префиксом, и 'api/state' должен
// резолвиться от него, а не от корня домена.

import type { components } from './api-types';

type Schemas = components['schemas'];

export type AppState = Schemas['AppStateOut'];
export type Recipe = Schemas['RecipeOut'];
export type ValidationError = Schemas['ValidationError'];

export type ActionPayloads = {
  'set-cook-schedule': Schemas['SetCookScheduleIn'];
  'set-shopping-timing': Schemas['SetShoppingTimingIn'];
  'set-anchor-date': Schemas['SetAnchorDateIn'];
};
export type ActionName = keyof ActionPayloads;

/** Диапазон расписания, ISO-даты (YYYY-MM-DD); по умолчанию 14 дней с сегодня. */
export type DateRange = { from?: string; to?: string };

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, detail: unknown) {
    super(errorMessage(status, detail));
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

function errorMessage(status: number, detail: unknown): string {
  // 422: detail — список {loc, msg}, как у FastAPI.
  if (Array.isArray(detail)) {
    return detail.map((e: ValidationError) => e.msg).join('; ');
  }
  return typeof detail === 'string' ? detail : `HTTP ${status}`;
}

function withRange(path: string, range: DateRange = {}): string {
  const params = new URLSearchParams();
  if (range.from) params.set('from', range.from);
  if (range.to) params.set('to', range.to);
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = (await res.json()).detail;
    } catch {
      // тело не JSON — останется только статус
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export function getState(range?: DateRange): Promise<AppState> {
  return request(withRange('api/state', range));
}

export function getRecipes(): Promise<Record<string, Recipe>> {
  return request('api/recipes');
}

export function callAction<N extends ActionName>(
  name: N,
  payload: ActionPayloads[N],
  range?: DateRange,
): Promise<AppState> {
  return request(withRange(`api/actions/${name}`, range), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}
