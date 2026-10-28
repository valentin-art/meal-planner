// Тонкий клиент к backend. Никакой бизнес-логики: только запросы и типы.
//
// Все URL — относительные, без ведущего '/': за Home Assistant Ingress
// приложение отдаётся под произвольным префиксом, и 'api/state' должен
// резолвиться от него, а не от корня домена.

import type { components } from './api-types';

type Schemas = components['schemas'];

export type AppState = Schemas['AppStateOut'];
export type Recipe = Schemas['RecipeOut'];
/** Справочник рецептов по id — GET api/recipes, отдельно от AppState. */
export type Recipes = Record<string, Recipe>;
export type ValidationError = Schemas['ValidationError'];
export type CatalogIngredient = Schemas['CatalogIngredientOut'];
/** Справочник ингредиентов с вариантами единиц и категорий для формы (5.6). */
export type IngredientsCatalog = Schemas['IngredientsOut'];
export type IngredientPayload = Schemas['SaveIngredientIn'];
export type IngredientActionResult = Schemas['IngredientActionOut'];
export type RecipePayload = Schemas['SaveRecipeIn'];
export type RecipeActionResult = Schemas['RecipeActionOut'];
export type Nutrition = Schemas['NutritionOut'];
export type PreviewLine = Schemas['RecipePreviewLineIn'];

export type ActionPayloads = {
  'set-cook-schedule': Schemas['SetCookScheduleIn'];
  'set-shopping-timing': Schemas['SetShoppingTimingIn'];
  'set-anchor-date': Schemas['SetAnchorDateIn'];
  'set-cycle-position': Schemas['SetCyclePositionIn'];
  'set-cycle-slot': Schemas['SetCycleSlotIn'];
  'set-nutrition-target': Schemas['SetNutritionTargetIn'];
  // без тела: что пропускать, решает сервер (ближайшая готовка)
  'skip-next-cook': Record<string, never>;
  'unskip-cook': Record<string, never>;
  // без тела: зерно выбирает сервер, с какого цикла — тоже (8.4a)
  'reshuffle-cycle': Record<string, never>;
  'reset-cycle-order': Record<string, never>;
};
export type ActionName = keyof ActionPayloads;

/** Действия с рецептом (5.4): ответ — новое состояние и рецепт от сервера. */
export type RecipeActionPayloads = {
  'save-recipe': RecipePayload;
  'archive-recipe': Schemas['RecipeIdIn'];
  'restore-recipe': Schemas['RecipeIdIn'];
};
export type RecipeActionName = keyof RecipeActionPayloads;

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

export function getRecipes(): Promise<Recipes> {
  return request('api/recipes');
}

export function getIngredients(): Promise<IngredientsCatalog> {
  return request('api/ingredients');
}

/** Ккал и белок черновика рецепта — считает сервер, ничего не сохраняется. */
export function previewRecipe(ingredients: PreviewLine[]): Promise<Nutrition> {
  return post('api/recipes/preview', { ingredients });
}

function post<T>(url: string, payload: unknown): Promise<T> {
  return request(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export function callRecipeAction<N extends RecipeActionName>(
  name: N,
  payload: RecipeActionPayloads[N],
  range?: DateRange,
): Promise<RecipeActionResult> {
  return post(withRange(`api/actions/${name}`, range), payload);
}

export function callIngredientAction(payload: IngredientPayload, range?: DateRange): Promise<IngredientActionResult> {
  return post(withRange('api/actions/save-ingredient', range), payload);
}

export function callAction<N extends ActionName>(
  name: N,
  payload: ActionPayloads[N],
  range?: DateRange,
): Promise<AppState> {
  return post(withRange(`api/actions/${name}`, range), payload);
}
