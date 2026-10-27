// Только отображение дат, которые пришли с сервера (ISO, YYYY-MM-DD).
// Никаких правил планирования: какие дни — дни готовки, что в какой день
// едим и когда закупка, решает backend.
//
// Даты разбираются как UTC-полночь — день недели и подпись не зависят от
// часового пояса и перехода на летнее время у пользователя.

export const WEEKDAY_NAMES = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
export const WEEKDAY_FULL = [
  'Понедельник',
  'Вторник',
  'Среда',
  'Четверг',
  'Пятница',
  'Суббота',
  'Воскресенье',
];

const DAY_MS = 86_400_000;

function parseISO(iso: string): Date {
  return new Date(`${iso}T00:00:00Z`);
}

/** Номер дня недели 1 (пн) … 7 (вс) — как cook_weekday в настройках. */
export function isoWeekday(iso: string): number {
  return parseISO(iso).getUTCDay() || 7;
}

export function weekdayShort(iso: string): string {
  return WEEKDAY_NAMES[isoWeekday(iso) - 1];
}

export function weekdayFull(iso: string): string {
  return WEEKDAY_FULL[isoWeekday(iso) - 1];
}

/** «9 нояб.» */
export function fmtDate(iso: string): string {
  return parseISO(iso).toLocaleDateString('ru-RU', {
    day: 'numeric',
    month: 'short',
    timeZone: 'UTC',
  });
}

/** Конец окна просмотра для ?to= (пагинация «Показать ещё»), не правило планирования. */
export function addDaysISO(iso: string, days: number): string {
  return new Date(parseISO(iso).getTime() + days * DAY_MS).toISOString().slice(0, 10);
}

/** « (−210)» к итогу дня; отклонение от цели считает сервер, null — без цели. */
export function fmtDelta(delta: number | null): string {
  if (delta === null) return '';
  const sign = delta === 0 ? '±' : delta > 0 ? '+' : '−';
  return ` (${sign}${Math.abs(delta)})`;
}
