import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, callAction, getRecipes, getState } from './api-client';

function mockFetch(status = 200, body: unknown = {}) {
  const fetchMock = vi.fn(
    async (_url: string, _init?: RequestInit) =>
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('URL — только относительные (HA Ingress)', () => {
  it('getState без диапазона', async () => {
    const fetchMock = mockFetch();
    await getState();
    expect(fetchMock).toHaveBeenCalledWith('api/state', undefined);
  });

  it('getState с диапазоном', async () => {
    const fetchMock = mockFetch();
    await getState({ from: '2026-11-01', to: '2026-11-14' });
    expect(fetchMock.mock.calls[0][0]).toBe('api/state?from=2026-11-01&to=2026-11-14');
  });

  it('getRecipes', async () => {
    const fetchMock = mockFetch();
    await getRecipes();
    expect(fetchMock.mock.calls[0][0]).toBe('api/recipes');
  });

  it('ни один запрос не идёт от корня', async () => {
    const fetchMock = mockFetch();
    await getState();
    await getState({ to: '2026-11-14' });
    await getRecipes();
    await callAction('set-shopping-timing', { shopping_timing: 'before' });
    await callAction('set-anchor-date', { meal_anchor_date: '2026-11-05' }, { from: '2026-11-01' });
    for (const [url] of fetchMock.mock.calls) {
      expect(url).toMatch(/^api\//);
    }
  });
});

describe('callAction', () => {
  it('POST JSON в api/actions/<имя> с тем же диапазоном', async () => {
    const fetchMock = mockFetch(200, { version: 3 });
    const state = await callAction(
      'set-cook-schedule',
      { cook_count: 2, cook_weekday1: 3, cook_weekday2: 6 },
      { from: '2026-11-01', to: '2026-11-03' },
    );
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('api/actions/set-cook-schedule?from=2026-11-01&to=2026-11-03');
    expect(init?.method).toBe('POST');
    expect(init?.headers).toEqual({ 'Content-Type': 'application/json' });
    expect(JSON.parse(init?.body as string)).toEqual({
      cook_count: 2,
      cook_weekday1: 3,
      cook_weekday2: 6,
    });
    expect(state).toEqual({ version: 3 });
  });
});

describe('ошибки', () => {
  it('422 → ApiError с сообщениями из detail', async () => {
    const detail = [
      {
        type: 'value_error',
        loc: ['body', 'cook_weekday2'],
        msg: 'При двух готовках в неделю нужен второй день готовки',
      },
    ];
    mockFetch(422, { detail });
    const error = await callAction('set-cook-schedule', { cook_count: 2, cook_weekday1: 3 }).catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 422,
      detail,
      message: 'При двух готовках в неделю нужен второй день готовки',
    });
  });

  it('не-JSON ответ → ApiError со статусом', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('Bad Gateway', { status: 502 })));
    await expect(getState()).rejects.toMatchObject({ status: 502, message: 'HTTP 502' });
  });
});
