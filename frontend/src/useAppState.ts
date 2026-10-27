// Состояние приложения — это AppState от сервера целиком. Хук только
// загружает его для видимого диапазона дат и отправляет действия; ответ
// действия заменяет state полностью, локально ничего не пересчитывается.
// Справочник рецептов грузится один раз (GET api/recipes); после правки
// рецепта (5.5) в него кладётся рецепт из ответа сервера. Справочник
// ингредиентов — лениво, когда открывается редактор.

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  type ActionName,
  type ActionPayloads,
  ApiError,
  type AppState,
  type CatalogIngredient,
  type DateRange,
  type RecipeActionName,
  type RecipeActionPayloads,
  type Recipes,
  type ValidationError,
  callAction,
  callRecipeAction,
  getIngredients,
  getRecipes,
  getState,
} from './api-client';
import { addDaysISO } from './format';

/** Видимый диапазон расписания; null — дефолт сервера (14 дней с сегодня). */
export type ViewRange = { from: string; days: number } | null;

function toQuery(range: ViewRange): DateRange | undefined {
  return range ? { from: range.from, to: addDaysISO(range.from, range.days - 1) } : undefined;
}

/** Ошибка последнего действия — UI показывает её у контролов этого действия. */
export type SaveError = { action: ActionName; message: string };

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** Ошибки формы по пути к полю: 'name', 'ingredients.0.qty'; '' — не у поля. */
export type FieldErrors = Record<string, string>;

function fieldErrors(e: unknown): FieldErrors {
  if (!(e instanceof ApiError) || e.status !== 422 || !Array.isArray(e.detail)) {
    return { '': message(e) };
  }
  const errors: FieldErrors = {};
  for (const { loc, msg } of e.detail as ValidationError[]) {
    const key = (loc[0] === 'body' ? loc.slice(1) : loc).join('.');
    errors[key] = errors[key] ? `${errors[key]}; ${msg}` : msg;
  }
  return errors;
}

export function useAppState() {
  const [state, setState] = useState<AppState | null>(null);
  const [recipes, setRecipes] = useState<Recipes | null>(null);
  // Отдельно от loadError: успешный GET state не должен скрыть ошибку справочника.
  const [recipesError, setRecipesError] = useState<string | null>(null);
  const [ingredients, setIngredients] = useState<CatalogIngredient[] | null>(null);
  const [range, setRange] = useState<ViewRange>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<SaveError | null>(null);
  // Номер последнего запроса: ответ на устаревший запрос отбрасывается,
  // чтобы медленный GET не затёр более свежий результат действия.
  const latest = useRef(0);

  const load = useCallback(async (next: ViewRange) => {
    const id = ++latest.current;
    try {
      const fresh = await getState(toQuery(next));
      if (id !== latest.current) return;
      setState(fresh);
      setLoadError(null);
    } catch (e) {
      if (id === latest.current) setLoadError(message(e));
    }
  }, []);

  const loadRecipes = useCallback(async () => {
    try {
      setRecipes(await getRecipes());
      setRecipesError(null);
    } catch (e) {
      setRecipesError(message(e));
    }
  }, []);

  useEffect(() => {
    load(range);
  }, [load, range]);

  useEffect(() => {
    loadRecipes();
  }, [loadRecipes]);

  const loadIngredients = useCallback(async () => {
    try {
      setIngredients(await getIngredients());
    } catch {
      // редактор покажет «нет справочника» и кнопку повтора
      setIngredients(null);
    }
  }, []);

  const showRange = useCallback((from: string, days: number) => setRange({ from, days }), []);

  const runAction = useCallback(
    async <N extends ActionName>(name: N, payload: ActionPayloads[N]) => {
      const id = ++latest.current;
      try {
        const fresh = await callAction(name, payload, toQuery(range));
        if (id !== latest.current) return;
        setState(fresh);
        setSaveError(null);
      } catch (e) {
        if (id === latest.current) setSaveError({ action: name, message: message(e) });
      }
    },
    [range],
  );

  /** Действие с рецептом; null — сохранено, иначе ошибки для формы. */
  const recipeAction = useCallback(
    async <N extends RecipeActionName>(name: N, payload: RecipeActionPayloads[N]): Promise<FieldErrors | null> => {
      const id = ++latest.current;
      try {
        const { state: fresh, recipe } = await callRecipeAction(name, payload, toQuery(range));
        setRecipes((all) => (all ? { ...all, [recipe.id]: recipe } : all));
        if (id === latest.current) setState(fresh);
        return null;
      } catch (e) {
        return fieldErrors(e);
      }
    },
    [range],
  );

  return {
    state,
    recipes,
    ingredients,
    loadIngredients,
    recipeAction,
    range,
    loadError: loadError ?? recipesError,
    saveError,
    showRange,
    runAction,
    retry: () => {
      if (!recipes) loadRecipes();
      load(range);
    },
  };
}
