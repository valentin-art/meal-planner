import { type CSSProperties, useCallback, useEffect, useRef, useState } from 'react';
import {
  Calendar,
  UtensilsCrossed,
  ShoppingCart,
  Snowflake,
  ChefHat,
  ChevronDown,
  ChevronRight,
  Flame,
  Plus,
  Settings as SettingsIcon,
  ArrowLeft,
  X,
} from 'lucide-react';

import {
  type ActionName,
  type ActionPayloads,
  type AppState,
  type IngredientPayload,
  type IngredientsCatalog,
  type Nutrition,
  type Recipe,
  type RecipeActionName,
  type RecipeActionPayloads,
  type RecipePayload,
  type Recipes,
  previewRecipe,
} from './api-client';
import { WEEKDAY_FULL, fmtDate, fmtDelta, weekdayFull, weekdayShort } from './format';
import { type FieldErrors, type SaveError, useAppState } from './useAppState';

// Презентационный слой: всё, что показано, — поля AppState от сервера.
// Расписание, дни готовки, суммы ккал/белка, поездка в магазин и количества
// считает backend (core/); здесь — только разметка и отправка действий.

// ---------- Design tokens ----------
const COLORS = {
  bg: '#EEF0E4',
  panel: '#FFFFFF',
  ink: '#242B1D',
  inkSoft: '#5B6350',
  primary: '#37452A',
  primarySoft: '#4E5E3C',
  accent: '#C7962E',
  accentSoft: '#F4E4BC',
  flame: '#3E6E72',
  flameSoft: '#DCEAEA',
  line: '#DEDFCF',
};

// Пагинация списка дней — состояние просмотра, не правило планирования.
const PAGE_SIZE = 14;
const LOAD_STEP = 14;

type Day = AppState['schedule'][number];
type Settings = AppState['settings'];
type Trip = AppState['next_shopping_trip'];
type SkippedCook = NonNullable<AppState['skipped_cook']>;
type MealDay = Day & { cycle_index: number; breakfast_id: string; lunch_id: string; dinner_id: string };
type RunAction = <N extends ActionName>(name: N, payload: ActionPayloads[N]) => Promise<void>;
type RecipeAction = <N extends RecipeActionName>(name: N, payload: RecipeActionPayloads[N]) => Promise<FieldErrors | null>;
type Kind = Recipe['kind'];
type PlanUse = AppState['recipe_plan_use'][string];
/** Что открыто в редакторе рецепта: id — правка, null — новый рецепт. */
type Editing = { id: string | null; kind: Kind };
type Tab = 'schedule' | 'menu' | 'shopping' | 'settings';

const KINDS = [
  { kind: 'lunch', title: 'Обеды', one: 'Обед', add: 'Новый обед' },
  { kind: 'dinner', title: 'Ужины', one: 'Ужин', add: 'Новый ужин' },
  { kind: 'breakfast', title: 'Завтраки', one: 'Завтрак', add: 'Новый завтрак' },
] as const;
const KIND_NAME: Record<Kind, string> = { lunch: 'Обед', dinner: 'Ужин', breakfast: 'Завтрак' };

export default function MealPlanApp() {
  const {
    state,
    recipes,
    catalog,
    loadIngredients,
    recipeAction,
    ingredientAction,
    range,
    loadError,
    saveError,
    showRange,
    runAction,
    retry,
  } = useAppState();
  const [tab, setTab] = useState<Tab>('schedule');
  // Редактор рецепта (5.5) — на весь экран вместо вкладки; другая вкладка его закрывает.
  const [editing, setEditing] = useState<Editing | null>(null);
  // Редактор ингредиента (5.6): id или 'new'.
  const [editingIngredient, setEditingIngredient] = useState<number | 'new' | null>(null);
  const [selectedDateStr, setSelectedDateStr] = useState<string | null>(null);
  const [openRecipe, setOpenRecipe] = useState<string | null>(null);

  const today = state?.today ?? null;
  const selected = selectedDateStr ?? today;

  // Single source of truth for "which date is focused" — used by both tabs,
  // so picking a date on either one moves both in lockstep.
  const goToDate = useCallback(
    (iso: string) => {
      if (!iso || !state) return; // очищенное поле даты
      setSelectedDateStr(iso);
      if (!state.schedule.some((d) => d.date === iso)) showRange(iso, PAGE_SIZE);
    },
    [state, showRange],
  );

  const goToToday = useCallback(() => {
    if (today) goToDate(today);
  }, [goToDate, today]);

  const loadMore = useCallback(() => {
    if (!today) return;
    showRange(range?.from ?? today, (range?.days ?? PAGE_SIZE) + LOAD_STEP);
  }, [range, today, showRange]);

  return (
    <div style={{ background: COLORS.bg, minHeight: '100%', color: COLORS.ink, fontFamily: 'ui-sans-serif, system-ui, sans-serif' }}>
      <header style={{ background: COLORS.primary, padding: '18px 16px 44px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#fff' }}>
          <ChefHat size={22} />
          <h1 style={{ fontFamily: 'ui-serif, Georgia, serif', fontSize: 20, fontWeight: 600, margin: 0 }}>
            Питание на неделю
          </h1>
        </div>
        <p style={{ color: COLORS.accentSoft, fontSize: 13, margin: '4px 0 0 30px' }}>
          Цикл из 5 блюд{today && <> · сегодня — {weekdayFull(today)}</>}
        </p>
      </header>

      <nav
        style={{
          display: 'flex',
          margin: '-28px 12px 0',
          background: COLORS.panel,
          borderRadius: 14,
          boxShadow: '0 4px 14px rgba(36,43,29,0.12)',
          overflow: 'hidden',
        }}
      >
        {(
          [
            { id: 'schedule', label: 'Расписание', Icon: Calendar },
            { id: 'menu', label: 'Меню', Icon: UtensilsCrossed },
            { id: 'shopping', label: 'Закупки', Icon: ShoppingCart },
            { id: 'settings', label: 'Настройки', Icon: SettingsIcon },
          ] as const
        ).map(({ id, label, Icon }) => (
          <button
            key={id}
            onClick={() => {
              setTab(id);
              setEditing(null);
              setEditingIngredient(null);
            }}
            style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 4,
              padding: '10px 4px',
              border: 'none',
              background: tab === id ? COLORS.accentSoft : 'transparent',
              color: tab === id ? COLORS.primary : COLORS.inkSoft,
              fontWeight: tab === id ? 700 : 500,
              fontSize: 12,
              cursor: 'pointer',
            }}
          >
            <Icon size={17} />
            {label}
          </button>
        ))}
      </nav>

      <main style={{ padding: '16px 12px 40px' }}>
        {!state || !recipes || !selected ? (
          loadError ? (
            <div style={{ textAlign: 'center', marginTop: 40 }}>
              <p style={{ color: '#8a3b2b' }}>Не удалось загрузить план: {loadError}</p>
              <button onClick={retry} style={ghostBtnStyle}>Повторить</button>
            </div>
          ) : (
            <p style={{ color: COLORS.inkSoft, textAlign: 'center', marginTop: 40 }}>Загрузка…</p>
          )
        ) : editingIngredient !== null ? (
          <IngredientEditor
            key={editingIngredient}
            id={editingIngredient === 'new' ? null : editingIngredient}
            catalog={catalog}
            recipes={recipes}
            loadIngredients={loadIngredients}
            ingredientAction={ingredientAction}
            onClose={() => setEditingIngredient(null)}
          />
        ) : editing ? (
          <RecipeEditor
            key={editing.id ?? `new-${editing.kind}`}
            editing={editing}
            recipe={editing.id ? recipes[editing.id] : undefined}
            planUse={editing.id ? state.recipe_plan_use[editing.id] : undefined}
            catalog={catalog}
            loadIngredients={loadIngredients}
            recipeAction={recipeAction}
            onClose={() => setEditing(null)}
          />
        ) : tab === 'schedule' ? (
          <ScheduleTab
            state={state}
            recipes={recipes}
            selectedDateStr={selected}
            goToDate={goToDate}
            goToToday={goToToday}
            loadMore={loadMore}
            saveError={saveError}
            runAction={runAction}
            onOpenDay={(dateStr) => {
              goToDate(dateStr);
              setTab('menu');
            }}
          />
        ) : tab === 'menu' ? (
          <MenuTab
            state={state}
            recipes={recipes}
            selectedDateStr={selected}
            goToDate={goToDate}
            openRecipe={openRecipe}
            setOpenRecipe={setOpenRecipe}
            loadMore={loadMore}
          />
        ) : tab === 'shopping' ? (
          <ShoppingTab state={state} recipes={recipes} saveError={saveError} runAction={runAction} />
        ) : (
          <SettingsTab
            settings={state.settings}
            position={state.today_position}
            skipped={state.skipped_cook}
            recipes={recipes}
            saveError={saveError}
            runAction={runAction}
            onEdit={setEditing}
            recipeAction={recipeAction}
            slots={state.cycle_slots}
            cycleOrder={state.cycle_order}
            portionScaling={state.portion_scaling}
            catalog={catalog}
            loadIngredients={loadIngredients}
            onEditIngredient={setEditingIngredient}
          />
        )}
        {state && loadError && (
          <p style={{ color: '#8a3b2b', fontSize: 12, textAlign: 'center', marginTop: 12 }}>
            Не удалось обновить план: {loadError}{' '}
            <button onClick={retry} style={{ ...ghostBtnStyle, padding: '2px 8px', fontSize: 12 }}>Повторить</button>
          </p>
        )}
      </main>
    </div>
  );
}

/** До якоря (день 0 и раньше) и в паузе блюд нет — сервер присылает null. */
function hasMeals(day: Day): day is MealDay {
  return day.cycle_index !== null && day.breakfast_id !== null && day.lunch_id !== null && day.dinner_id !== null;
}

function CycleStartsNote({ anchor, style }: { anchor: string; style: CSSProperties }) {
  return (
    <p style={{ color: COLORS.inkSoft, ...style }}>
      Цикл начнётся {weekdayShort(anchor)} {fmtDate(anchor)}
    </p>
  );
}

const PAUSE_NOTE = 'Пауза — готовка пропущена, блюд нет';

/** Пропущенная готовка (7.7): даты паузы и «можно ли отменить» — от сервера. */
function SkippedCookBanner({ skipped, saveError, runAction }: { skipped: SkippedCook; saveError: SaveError | null; runAction: RunAction }) {
  return (
    <section style={{ ...cardStyle, borderLeft: `4px solid ${COLORS.flame}`, marginBottom: 14 }}>
      <p style={{ fontSize: 13, margin: 0 }}>
        <b>Готовка {weekdayShort(skipped.cook_date)} {fmtDate(skipped.cook_date)} пропущена.</b> Пауза с{' '}
        {weekdayShort(skipped.pause_start)} {fmtDate(skipped.pause_start)} по {weekdayShort(skipped.pause_end)}{' '}
        {fmtDate(skipped.pause_end)} — в этот день снова готовка, цикл продолжится с того же блюда.
      </p>
      {skipped.can_undo && (
        <button onClick={() => runAction('unskip-cook', {})} style={{ ...ghostBtnStyle, marginTop: 8 }}>
          Отменить пропуск
        </button>
      )}
      <FieldError error={saveError} action="unskip-cook" />
    </section>
  );
}

// ---------- Schedule Tab ----------
type ScheduleTabProps = {
  state: AppState;
  recipes: Recipes;
  selectedDateStr: string;
  goToDate: (iso: string) => void;
  goToToday: () => void;
  loadMore: () => void;
  saveError: SaveError | null;
  runAction: RunAction;
  onOpenDay: (iso: string) => void;
};

function ScheduleTab({ state, recipes, selectedDateStr, goToDate, goToToday, loadMore, saveError, runAction, onOpenDay }: ScheduleTabProps) {
  const { schedule, today, settings } = state;
  const isToday = selectedDateStr === today;
  return (
    <div>
      {state.skipped_cook && <SkippedCookBanner skipped={state.skipped_cook} saveError={saveError} runAction={runAction} />}
      <section style={{ ...cardStyle, display: 'flex', alignItems: 'flex-end', gap: 10 }}>
        <div style={{ flex: 1 }}>
          <label style={labelStyle}>Перейти к дате</label>
          <input type="date" value={selectedDateStr} onChange={(e) => goToDate(e.target.value)} style={inputStyle} />
        </div>
        {!isToday && (
          <button onClick={goToToday} style={ghostBtnStyle}>Сегодня</button>
        )}
      </section>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 14 }}>
        {schedule.map((day) => {
          const dateStr = day.date;
          const isToday = dateStr === today;
          const isSelected = dateStr === selectedDateStr;
          const isZero = day.is_day_zero;
          return (
            <button
              key={dateStr}
              onClick={() => onOpenDay(dateStr)}
              style={{
                ...cardStyle,
                textAlign: 'left',
                border: isToday ? `2px solid ${COLORS.accent}` : isSelected ? `2px solid ${COLORS.primary}` : `1px solid ${COLORS.line}`,
                background: isSelected && !isToday ? COLORS.flameSoft : COLORS.panel,
                cursor: 'pointer',
                width: '100%',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <span style={{ fontFamily: 'ui-serif, Georgia, serif', fontWeight: 700, fontSize: 15 }}>
                  {weekdayShort(dateStr)}
                  {day.is_cook_day && <Flame size={13} style={{ marginLeft: 6, verticalAlign: '-2px', color: COLORS.accent }} />}
                </span>
                <span style={{ fontSize: 12, color: COLORS.inkSoft }}>{fmtDate(dateStr)}{day.cycle_index !== null && ` · блюдо ${day.cycle_index}`}</span>
              </div>
              {isToday && (
                <span
                  style={{
                    display: 'inline-block',
                    background: COLORS.accentSoft,
                    color: COLORS.primary,
                    fontSize: 10,
                    fontWeight: 700,
                    borderRadius: 6,
                    padding: '1px 6px',
                    margin: '4px 0',
                  }}
                >
                  СЕГОДНЯ
                </span>
              )}
              {isSelected && !isToday && (
                <span
                  style={{
                    display: 'inline-block',
                    background: COLORS.primary,
                    color: '#fff',
                    fontSize: 10,
                    fontWeight: 700,
                    borderRadius: 6,
                    padding: '1px 6px',
                    margin: '4px 0',
                  }}
                >
                  ВЫБРАНО
                </span>
              )}
              {isZero ? (
                <p style={{ fontSize: 13, color: COLORS.flame, fontWeight: 600, margin: '6px 0 0' }}>
                  День 0 · готовка — меню начнётся завтра
                </p>
              ) : day.is_skipped ? (
                <p style={{ fontSize: 13, color: COLORS.inkSoft, margin: '6px 0 0' }}>{PAUSE_NOTE}</p>
              ) : !hasMeals(day) ? (
                <CycleStartsNote anchor={settings.meal_anchor_date} style={{ fontSize: 13, margin: '6px 0 0' }} />
              ) : (
                <div style={{ fontSize: 13, marginTop: 6, lineHeight: 1.7 }}>
                  <div><b>Завтрак:</b> {recipes[day.breakfast_id].name}</div>
                  <div><b>Обед:</b> {recipes[day.lunch_id].name}{scaleNote(day.lunch_scale)}{recipes[day.lunch_id].is_fish && <span style={{ color: COLORS.flame }}> · рыба/морепродукты</span>}</div>
                  <div><b>Ужин:</b> {recipes[day.dinner_id].name}{scaleNote(day.dinner_scale)}{recipes[day.dinner_id].is_fish && <span style={{ color: COLORS.flame }}> · рыба/морепродукты</span>}</div>
                  <div style={{ fontSize: 12, color: COLORS.inkSoft, marginTop: 4 }}>
                    Итого: ≈{day.kcal_total} ккал{fmtDelta(day.kcal_delta)} · {day.protein_total}г белка{fmtDelta(day.protein_delta)}
                  </div>
                </div>
              )}
            </button>
          );
        })}
      </div>

      <button onClick={loadMore} style={{ ...ghostBtnStyle, width: '100%', marginTop: 12, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
        <Plus size={15} /> Показать ещё {LOAD_STEP_LABEL}
      </button>
    </div>
  );
}
const LOAD_STEP_LABEL = '14 дней';

// ---------- Menu Tab ----------
type MenuTabProps = {
  state: AppState;
  recipes: Recipes;
  selectedDateStr: string;
  goToDate: (iso: string) => void;
  openRecipe: string | null;
  setOpenRecipe: (id: string | null) => void;
  loadMore: () => void;
};

function MenuTab({ state, recipes, selectedDateStr, goToDate, openRecipe, setOpenRecipe, loadMore }: MenuTabProps) {
  const [showStoveInfo, setShowStoveInfo] = useState(false);
  const [showStorageInfo, setShowStorageInfo] = useState(false);
  const { schedule, today } = state;
  const day: Day | undefined = schedule.find((d) => d.date === selectedDateStr);
  const inRange = day !== undefined;
  const byKind = (kind: Recipe['kind']) => recipesOfKind(recipes, kind);

  return (
    <div>
      <button
        onClick={() => setShowStorageInfo((v) => !v)}
        style={{ ...ghostBtnStyle, width: '100%', marginBottom: 10, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
      >
        <span>Хранение и разогрев: общие правила</span>
        {showStorageInfo ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
      </button>
      {showStorageInfo && (
        <section style={{ ...cardStyle, marginBottom: 10, fontSize: 13, lineHeight: 1.6 }}>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            <li style={{ marginBottom: 4 }}>Готовое блюдо остудить и убрать в холодильник в течение 1-2 часов (рис — в течение часа, слоем 2-3 см). Не оставлять остывать на столе дольше.</li>
            <li style={{ marginBottom: 4 }}>Хранить в герметичных контейнерах при температуре холодильника не выше 4 °C. Курица и говядина — до 4 дней, рыба и креветки — лучше съесть в первые 2-3 дня.</li>
            <li style={{ marginBottom: 4 }}>Разогревать только ту порцию, которую съедите сразу. Повторно разогретое блюдо снова разогревать не стоит.</li>
            <li style={{ marginBottom: 4 }}>Блюдо должно быть горячим насквозь: над ним идёт пар, а середина самого плотного куска (курица, креветка, говядина) горячая. Для проверки разрежьте один кусок пополам.</li>
            <li style={{ marginBottom: 4 }}>Микроволновка: средняя мощность (50-60%), накрыть крышкой или тарелкой, добавить 1-2 ст.л. воды, перемешивать каждые 40-60 секунд. Греть интервалами, а не на полной мощности: края пересыхают раньше, чем прогреется середина.</li>
            <li style={{ marginBottom: 4 }}>Сковорода: уровень 3-4, 1-2 ст.л. воды, крышка, помешивать каждые 40-60 секунд. Без воды крупы и паста пересыхают.</li>
            <li style={{ marginBottom: 4 }}>Всегда добавлять после разогрева или перед едой: авокадо, свежую зелень, помидоры, лимонный сок, заправку, сыр и орехи.</li>
            <li>Если у блюда кислый или резкий запах либо слизистая плёнка на поверхности, его не едят, даже если срок хранения не вышел.</li>
          </ul>
        </section>
      )}
      <button
        onClick={() => setShowStoveInfo((v) => !v)}
        style={{ ...ghostBtnStyle, width: '100%', marginBottom: 10, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
      >
        <span>О плите: уровни нагрева (0-9)</span>
        {showStoveInfo ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
      </button>
      {showStoveInfo && (
        <section style={{ ...cardStyle, marginBottom: 10, fontSize: 13, lineHeight: 1.6 }}>
          <p style={{ margin: '0 0 6px' }}>Индукционная плита греется почти мгновенно и так же быстро реагирует на переключение уровня. Сковороду достаточно прогреть пустой 30-60 секунд, дольше не нужно — она может перегреться. Посуда должна быть магнитной (к дну притягивается магнит).</p>
          <ul style={{ margin: '0 0 8px', paddingLeft: 18 }}>
            <li><b>1-2</b> — подогрев, растопить масло, крупы под крышкой</li>
            <li><b>2-3</b> — тушение, томление под крышкой</li>
            <li><b>3-4</b> — яичница, мягкое обжаривание, разогрев готовых блюд под крышкой с водой</li>
            <li><b>4-5</b> — овощи, тосты</li>
            <li><b>5-6</b> — обжарка мяса/рыбы до корочки</li>
            <li><b>7-8</b> — доведение воды до кипения, сильная обжарка креветок (6-7)</li>
            <li><b>Boost / P</b> — только для воды в кастрюле, не для сковороды с маслом</li>
          </ul>
          <p style={{ margin: 0 }}>
            <b>Проверка каплей воды:</b> капните на разогретую сухую сковороду. Капля спокойно шипит и медленно испаряется 4-5 секунд — рано, подождите ещё. Капля собирается в шарик и «бегает» по поверхности 1-2 секунды перед испарением — то, что нужно для обжарки. Капля исчезает почти мгновенно с резким хлопком — перегрели, убавьте на 1-2 уровня (на индукции остывание происходит сразу, снимать сковороду с конфорки не нужно). В первую готовку на новой плите начинайте на 1 уровень ниже указанного и добавляйте по необходимости: шкалы у разных плит отличаются.
          </p>
        </section>
      )}
      <div style={{ display: 'flex', gap: 6, overflowX: 'auto', paddingBottom: 4 }}>
        {!inRange && (
          <span style={{ flexShrink: 0, alignSelf: 'center', fontSize: 11, color: COLORS.inkSoft }}>
            {fmtDate(selectedDateStr)} — вне списка ниже
          </span>
        )}
        {schedule.map((d) => {
          const dateStr = d.date;
          const isToday = dateStr === today;
          const isSelected = dateStr === selectedDateStr;
          const noMenu = d.is_day_zero || d.is_before_start || d.is_skipped;
          return (
            <button
              key={dateStr}
              onClick={() => goToDate(dateStr)}
              style={{
                flexShrink: 0,
                padding: '6px 12px',
                borderRadius: 20,
                border: isSelected ? `1px solid ${COLORS.primary}` : noMenu ? `1px dashed ${COLORS.inkSoft}` : `1px solid ${COLORS.line}`,
                background: isSelected ? COLORS.primary : COLORS.panel,
                color: isSelected ? '#fff' : noMenu ? COLORS.inkSoft : COLORS.ink,
                fontSize: 12,
                fontWeight: isToday ? 700 : 500,
                cursor: 'pointer',
              }}
            >
              {weekdayShort(dateStr)} {fmtDate(dateStr)}{isToday ? ' · сегодня' : ''}
            </button>
          );
        })}
        <button
          onClick={loadMore}
          style={{ flexShrink: 0, padding: '6px 10px', borderRadius: 20, border: `1px dashed ${COLORS.line}`, background: 'transparent', color: COLORS.inkSoft, fontSize: 12, cursor: 'pointer' }}
        >
          +14
        </button>
      </div>

      <div style={{ marginTop: 8 }}>
        <label style={{ ...labelStyle, display: 'inline-block', marginRight: 8 }}>Или дата:</label>
        <input
          type="date"
          value={selectedDateStr}
          onChange={(e) => goToDate(e.target.value)}
          style={{ ...inputStyle, width: 'auto', display: 'inline-block', padding: '4px 8px' }}
        />
      </div>

      <section style={{ ...cardStyle, marginTop: 12 }}>
        <p style={{ fontSize: 12, color: COLORS.inkSoft, margin: '0 0 8px' }}>
          {weekdayShort(selectedDateStr)}, {fmtDate(selectedDateStr)}{day && day.cycle_index !== null && ` · блюдо №${day.cycle_index}`}
        </p>
        {!day ? (
          <p style={{ fontSize: 13, color: COLORS.inkSoft, margin: 0 }}>Загрузка…</p>
        ) : day.is_day_zero ? (
          <p style={{ fontSize: 14, color: COLORS.flame, fontWeight: 600, margin: 0 }}>
            День 0 · готовка — меню начнётся завтра
          </p>
        ) : day.is_skipped ? (
          <p style={{ fontSize: 14, color: COLORS.inkSoft, margin: 0 }}>{PAUSE_NOTE}</p>
        ) : !hasMeals(day) ? (
          <CycleStartsNote anchor={state.settings.meal_anchor_date} style={{ fontSize: 14, margin: 0 }} />
        ) : (
          <>
            <p style={{ fontSize: 13, margin: '0 0 10px' }}>
              <span style={{ fontSize: 11, color: COLORS.accent, fontWeight: 700 }}>ЗАВТРАК</span><br />
              {recipes[day.breakfast_id].name}{' '}
              <span style={{ fontSize: 12, color: COLORS.inkSoft }}>(≈{recipes[day.breakfast_id].kcal} ккал · {recipes[day.breakfast_id].protein}г)</span>
            </p>
            <RecipeBlock recipe={portionOf(state, recipes, day.lunch_id, day.lunch_scale)} slot="Обед" openRecipe={openRecipe} setOpenRecipe={setOpenRecipe} />
            <div style={{ height: 10 }} />
            <RecipeBlock recipe={portionOf(state, recipes, day.dinner_id, day.dinner_scale)} slot="Ужин" openRecipe={openRecipe} setOpenRecipe={setOpenRecipe} />
            <p style={{ fontSize: 13, fontWeight: 600, color: COLORS.primarySoft, marginTop: 12, marginBottom: 0, borderTop: `1px solid ${COLORS.line}`, paddingTop: 8 }}>
              Итого за день: ≈{day.kcal_total} ккал{fmtDelta(day.kcal_delta)} · {day.protein_total}г белка{fmtDelta(day.protein_delta)}
            </p>
            <TargetNote settings={state.settings} />
          </>
        )}
      </section>

      <h2 style={sectionHeading}>Все обеды</h2>
      {byKind('lunch').map((r) => (
        <RecipeCard key={r.id} recipe={r} openRecipe={openRecipe} setOpenRecipe={setOpenRecipe} />
      ))}

      <h2 style={sectionHeading}>Все ужины</h2>
      {byKind('dinner').map((r) => (
        <RecipeCard key={r.id} recipe={r} openRecipe={openRecipe} setOpenRecipe={setOpenRecipe} />
      ))}

      <h2 style={sectionHeading}>Завтраки</h2>
      {byKind('breakfast').map((r) => (
        <RecipeCard key={r.id} recipe={r} openRecipe={openRecipe} setOpenRecipe={setOpenRecipe} />
      ))}
    </div>
  );
}

/** Рецепты типа с этим статусом; архивные — только в «Блюдах» (5.5). */
function recipesOfKind(recipes: Recipes, kind: Kind, status: Recipe['status'] = 'active'): Recipe[] {
  return Object.values(recipes).filter((r) => r.kind === kind && r.status === status);
}

type RecipeToggleProps = {
  recipe: Recipe;
  openRecipe: string | null;
  setOpenRecipe: (id: string | null) => void;
};

/** Рецепт в порции под цель (8.5): множитель, ккал и граммы — с сервера. */
type Portion = Recipe & { scale: number };

function portionOf(state: AppState, recipes: Recipes, id: string, scale: number): Portion {
  const scaled = scale === 1 ? undefined : state.scaled_recipes.find((r) => r.recipe_id === id && r.scale === scale);
  return scaled
    ? { ...recipes[id], kcal: scaled.kcal, protein: scaled.protein, ingredients: scaled.ingredients, scale }
    : { ...recipes[id], scale: 1 };
}

/** « · порция ×1.45» у масштабированного обеда или ужина; иначе пусто. */
function scaleNote(scale: number): string {
  return scale === 1 ? '' : ` · порция ×${scale}`;
}

function RecipeBlock({ recipe: r, slot, openRecipe, setOpenRecipe }: Omit<RecipeToggleProps, 'recipe'> & { recipe: Portion; slot: string }) {
  const isOpen = openRecipe === r.id + '-inline';
  return (
    <div>
      <button
        onClick={() => setOpenRecipe(isOpen ? null : r.id + '-inline')}
        style={{ display: 'flex', justifyContent: 'space-between', width: '100%', background: 'none', border: 'none', padding: 0, cursor: 'pointer' }}
      >
        <span>
          <span style={{ fontSize: 11, color: COLORS.accent, fontWeight: 700 }}>{slot.toUpperCase()}</span>
          <br />
          <span style={{ fontFamily: 'ui-serif, Georgia, serif', fontWeight: 700, fontSize: 15 }}>{r.name}</span>
          <br />
          <span style={{ fontSize: 12, color: COLORS.inkSoft }}>≈{r.kcal} ккал · {r.protein}г белка{scaleNote(r.scale)}</span>
        </span>
        {isOpen ? <ChevronDown size={18} color={COLORS.inkSoft} /> : <ChevronRight size={18} color={COLORS.inkSoft} />}
      </button>
      {isOpen && <RecipeDetails recipe={r} scale={r.scale} />}
    </div>
  );
}

function RecipeCard({ recipe: r, openRecipe, setOpenRecipe }: RecipeToggleProps) {
  const isOpen = openRecipe === r.id;
  return (
    <div style={{ ...cardStyle, marginBottom: 8 }}>
      <button
        onClick={() => setOpenRecipe(isOpen ? null : r.id)}
        style={{ display: 'flex', justifyContent: 'space-between', width: '100%', background: 'none', border: 'none', padding: 0, cursor: 'pointer' }}
      >
        <span>
          <span style={{ fontWeight: 600, fontSize: 14 }}>{r.name}</span>
          <br />
          <span style={{ fontSize: 12, color: COLORS.inkSoft }}>≈{r.kcal} ккал · {r.protein}г белка</span>
        </span>
        {isOpen ? <ChevronDown size={18} color={COLORS.inkSoft} /> : <ChevronRight size={18} color={COLORS.inkSoft} />}
      </button>
      {isOpen && <RecipeDetails recipe={r} />}
    </div>
  );
}

function RecipeDetails({ recipe, scale = 1 }: { recipe: Recipe; scale?: number }) {
  return (
    <div style={{ marginTop: 10, fontSize: 13 }}>
      <b>Ингредиенты ({scale === 1 ? '1 порция' : `порция ×${scale}`}):</b>
      <ul style={{ margin: '4px 0 8px', paddingLeft: 18 }}>
        {recipe.ingredients.map((ing) => (
          <li key={`${ing.name}|${ing.unit}`}>
            {ing.name} — {ing.qty_display} {ing.unit}
          </li>
        ))}
      </ul>
      <b>Приготовление:</b>
      <ol style={{ margin: '4px 0 0', paddingLeft: 18 }}>
        {recipe.steps.map((s, i) => (
          <li key={i} style={{ marginBottom: 2 }}>{s}</li>
        ))}
      </ol>
      {recipe.storage && (
        <div style={{ marginTop: 12, padding: 10, background: COLORS.flameSoft, borderRadius: 10 }}>
          <b>Хранение и разогрев</b>
          <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            <li style={{ marginBottom: 4 }}><b>Хранение:</b> {recipe.storage.keep}</li>
            <li style={{ marginBottom: 4 }}><b>Сковорода:</b> {recipe.storage.pan}</li>
            <li style={{ marginBottom: 4 }}><b>Микроволновка:</b> {recipe.storage.micro}</li>
            {recipe.storage.after && (
              <li style={{ marginBottom: 0 }}><b>Добавить после разогрева:</b> {recipe.storage.after}</li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}

// ---------- Shopping Tab ----------
type ShoppingTabProps = {
  state: AppState;
  recipes: Recipes;
  saveError: SaveError | null;
  runAction: RunAction;
};

function ShoppingTab({ state, recipes, saveError, runAction }: ShoppingTabProps) {
  // Окно, что готовить и что купить — целиком из next_shopping_trip.
  const trip: Trip = state.next_shopping_trip;
  return (
    <div>
      {state.skipped_cook && <SkippedCookBanner skipped={state.skipped_cook} saveError={saveError} runAction={runAction} />}
      <section style={{ ...cardStyle, borderLeft: `4px solid ${COLORS.accent}` }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <span style={{ fontFamily: 'ui-serif, Georgia, serif', fontWeight: 700, fontSize: 16 }}>Ближайшая закупка</span>
          <span style={{ fontSize: 12, color: COLORS.inkSoft }}>{fmtDate(trip.shopping_date)}</span>
        </div>
        <p style={{ fontSize: 12, color: COLORS.inkSoft, margin: '4px 0 0' }}>
          Готовка — {weekdayShort(trip.cook_date)} {fmtDate(trip.cook_date)}. Едим с {weekdayShort(trip.window_start)} {fmtDate(trip.window_start)} по {weekdayShort(trip.window_end)} {fmtDate(trip.window_end)} включительно ({trip.window_days} {trip.window_days === 1 ? 'день' : 'дня/дней'}).
        </p>
        {state.can_skip_next_cook && (
          <button onClick={() => runAction('skip-next-cook', {})} style={{ ...ghostBtnStyle, marginTop: 8, padding: '4px 10px', fontSize: 12 }}>
            Пропустить эту готовку
          </button>
        )}
        <FieldError error={saveError} action="skip-next-cook" />

        <h3 style={{ fontSize: 13, color: COLORS.flame, margin: '12px 0 6px' }}>
          <ChefHat size={14} style={{ verticalAlign: '-2px', marginRight: 4 }} />
          Что готовить
        </h3>
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
          {trip.cook_items.map((it, i) => (
            <li key={i} style={{ marginBottom: 2 }}>
              {`${recipes[it.lunch_id].name}${scaleNote(it.lunch_scale)} + ${recipes[it.dinner_id].name}${scaleNote(it.dinner_scale)}`} <span style={{ color: COLORS.inkSoft }}>— x{it.portions}</span>
            </li>
          ))}
        </ul>

        <h3 style={{ fontSize: 13, color: COLORS.flame, margin: '14px 0 6px' }}>
          <ShoppingCart size={14} style={{ verticalAlign: '-2px', marginRight: 4 }} />
          Что купить
        </h3>
        {trip.shopping_groups.map(({ category, items }) => (
          <div key={category} style={{ marginBottom: 8 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: COLORS.primarySoft, marginBottom: 2 }}>{category}</div>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
              {items.map((it) => (
                // у цукини есть и «г», и «шт» — ключ по имени и единице
                <li key={`${it.name}|${it.unit}`}>
                  {it.name} — {it.qty_display} {it.unit}
                  {it.pieces !== null && <span style={{ color: COLORS.inkSoft }}> (≈ {it.pieces} шт)</span>}
                </li>
              ))}
            </ul>
          </div>
        ))}
        <p style={{ fontSize: 11, color: COLORS.inkSoft, marginTop: 6, marginBottom: 0 }}>
          <Snowflake size={11} style={{ verticalAlign: '-1px' }} /> Если блюдо нужно &gt;1 раза за период — сразу разложите по порциям (часть в холодильник, часть в морозилку).
        </p>
      </section>
    </div>
  );
}

// ---------- Settings Tab ----------
type SettingsTabProps = {
  settings: Settings;
  position: AppState['today_position'];
  skipped: AppState['skipped_cook'];
  recipes: Recipes;
  saveError: SaveError | null;
  runAction: RunAction;
  onEdit: (editing: Editing) => void;
  recipeAction: RecipeAction;
  slots: AppState['cycle_slots'];
  cycleOrder: AppState['cycle_order'];
  portionScaling: AppState['portion_scaling'];
  catalog: IngredientsCatalog | null;
  loadIngredients: () => Promise<void>;
  onEditIngredient: (id: number | 'new') => void;
};

function SettingsTab({
  settings,
  position,
  skipped,
  recipes,
  saveError,
  runAction,
  onEdit,
  recipeAction,
  slots,
  cycleOrder,
  portionScaling,
  catalog,
  loadIngredients,
  onEditIngredient,
}: SettingsTabProps) {
  const setCookSchedule = (change: Partial<ActionPayloads['set-cook-schedule']>) =>
    runAction('set-cook-schedule', {
      cook_count: settings.cook_count === 1 ? 1 : 2,
      cook_weekday1: settings.cook_weekday1,
      cook_weekday2: settings.cook_weekday2,
      ...change,
    });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <section style={cardStyle}>
        {/* Оба поля — одна настройка (якорь): варианты и якорь к ним считает сервер. */}
        <label style={labelStyle}>Сегодня</label>
        <select
          value={position.current ?? ''}
          onChange={(e) => runAction('set-cycle-position', { dish: Number(e.target.value) })}
          style={inputStyle}
        >
          {position.current === null && (
            <option value="" disabled>
              {skipped
                ? `Пауза до ${weekdayShort(skipped.pause_end)} ${fmtDate(skipped.pause_end)}`
                : `Цикл начнётся ${weekdayShort(settings.meal_anchor_date)} ${fmtDate(settings.meal_anchor_date)}`}
            </option>
          )}
          {position.options.map((o) => (
            <option key={o.dish} value={o.dish}>
              {o.lunch_id && o.dinner_id
                ? `блюдо №${o.dish} · ${recipes[o.lunch_id].name} + ${recipes[o.dinner_id].name}`
                : 'день 0 · готовка, цикл с завтра'}
            </option>
          ))}
        </select>
        <FieldError error={saveError} action="set-cycle-position" />

        <label style={{ ...labelStyle, marginTop: 10 }}>Первый день цикла</label>
        <input
          type="date"
          value={settings.meal_anchor_date}
          onChange={(e) => runAction('set-anchor-date', { meal_anchor_date: e.target.value })}
          style={inputStyle}
        />
        <p style={{ fontSize: 12, color: COLORS.inkSoft, margin: '8px 0 0' }}>
          От этой даты отсчитывается цикл из 5 блюд на обед и ужин. Рыбные блюда в каждой партии едятся первыми,
          поэтому сегодня можно выбрать не любое блюдо.
        </p>
        <FieldError error={saveError} action="set-anchor-date" />
      </section>

      <section style={cardStyle}>
        <label style={labelStyle}>Готовок в неделю</label>
        <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
          {([1, 2] as const).map((n) => (
            <button
              key={n}
              // второй день не передаём — сервер оставит сохранённый (как в JS)
              onClick={() => runAction('set-cook-schedule', { cook_count: n, cook_weekday1: settings.cook_weekday1 })}
              style={{
                flex: 1,
                padding: '8px 0',
                borderRadius: 10,
                border: settings.cook_count === n ? `1px solid ${COLORS.primary}` : `1px solid ${COLORS.line}`,
                background: settings.cook_count === n ? COLORS.primary : COLORS.panel,
                color: settings.cook_count === n ? '#fff' : COLORS.ink,
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              {n} раз{n === 1 ? '' : 'а'}
            </button>
          ))}
        </div>

        <label style={labelStyle}>День готовки{settings.cook_count === 2 ? ' №1' : ''}</label>
        <select
          value={settings.cook_weekday1}
          onChange={(e) => setCookSchedule({ cook_weekday1: Number(e.target.value) })}
          style={inputStyle}
        >
          {WEEKDAY_FULL.map((name, i) => (
            <option key={i} value={i + 1}>{name}</option>
          ))}
        </select>

        {settings.cook_count === 2 && (
          <>
            <label style={{ ...labelStyle, marginTop: 10 }}>День готовки №2</label>
            <select
              value={settings.cook_weekday2 ?? ''}
              onChange={(e) => setCookSchedule({ cook_weekday2: Number(e.target.value) })}
              style={inputStyle}
            >
              {WEEKDAY_FULL.map((name, i) => (
                <option key={i} value={i + 1}>{name}</option>
              ))}
            </select>
          </>
        )}

        <FieldError error={saveError} action="set-cook-schedule" />
      </section>

      <section style={cardStyle}>
        <label style={labelStyle}>Закупка</label>
        <div style={{ display: 'flex', gap: 8 }}>
          {(
            [
              { id: 'same', label: 'В день готовки' },
              { id: 'before', label: 'За день до' },
            ] as const
          ).map((opt) => (
            <button
              key={opt.id}
              onClick={() => runAction('set-shopping-timing', { shopping_timing: opt.id })}
              style={{
                flex: 1,
                padding: '8px 4px',
                borderRadius: 10,
                border: settings.shopping_timing === opt.id ? `1px solid ${COLORS.primary}` : `1px solid ${COLORS.line}`,
                background: settings.shopping_timing === opt.id ? COLORS.primary : COLORS.panel,
                color: settings.shopping_timing === opt.id ? '#fff' : COLORS.ink,
                fontWeight: 600,
                fontSize: 12,
                cursor: 'pointer',
              }}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <FieldError error={saveError} action="set-shopping-timing" />
      </section>

      <NutritionTargetSection settings={settings} scaling={portionScaling} saveError={saveError} runAction={runAction} />

      <CycleSection slots={slots} order={cycleOrder} recipes={recipes} saveError={saveError} runAction={runAction} />
      <DishesSection recipes={recipes} onEdit={onEdit} recipeAction={recipeAction} />
      <IngredientsSection catalog={catalog} loadIngredients={loadIngredients} onEdit={onEditIngredient} />
    </div>
  );
}

// ---------- Цель по ккал и белку (8.1) ----------
function TargetNote({ settings }: { settings: Settings }) {
  const { daily_kcal_target: kcal, daily_protein_target: protein } = settings;
  if (kcal === null && protein === null) return null;
  const parts = [kcal !== null && `${kcal} ккал`, protein !== null && `${protein}г белка`].filter(Boolean);
  return <p style={{ fontSize: 12, color: COLORS.inkSoft, margin: '4px 0 0' }}>Цель: {parts.join(' · ')}</p>;
}

type NutritionTargetSectionProps = {
  settings: Settings;
  scaling: AppState['portion_scaling'];
  saveError: SaveError | null;
  runAction: RunAction;
};

function NutritionTargetSection({ settings, scaling, saveError, runAction }: NutritionTargetSectionProps) {
  // Черновик полей; после ответа сервера — снова то, что сохранено.
  const [kcal, setKcal] = useState(text(settings.daily_kcal_target));
  const [protein, setProtein] = useState(text(settings.daily_protein_target));
  useEffect(() => {
    setKcal(text(settings.daily_kcal_target));
    setProtein(text(settings.daily_protein_target));
  }, [settings.daily_kcal_target, settings.daily_protein_target]);

  return (
    <section style={cardStyle}>
      <label style={labelStyle}>Цель на день</label>
      <div style={{ display: 'flex', gap: 8 }}>
        <input
          type="number"
          inputMode="numeric"
          aria-label="Ккал"
          placeholder="ккал — без цели"
          value={kcal}
          onChange={(e) => setKcal(e.target.value)}
          style={inputStyle}
        />
        <input
          type="number"
          inputMode="numeric"
          aria-label="Белок, г"
          placeholder="белок, г — без цели"
          value={protein}
          onChange={(e) => setProtein(e.target.value)}
          style={inputStyle}
        />
      </div>
      <button
        onClick={() =>
          runAction('set-nutrition-target', {
            daily_kcal_target: optionalNumber(kcal),
            daily_protein_target: optionalNumber(protein),
          })
        }
        style={{ ...ghostBtnStyle, marginTop: 10 }}
      >
        Применить
      </button>
      <p style={{ fontSize: 12, color: COLORS.inkSoft, margin: '8px 0 0' }}>
        Итог дня покажет отклонение от цели. Пустое поле — без цели.
      </p>
      <FieldError error={saveError} action="set-nutrition-target" />
      <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, marginTop: 10 }}>
        <input
          type="checkbox"
          checked={scaling.enabled}
          onChange={(e) => runAction('set-portion-scaling', { enabled: e.target.checked })}
        />
        Подгонять порции обедов и ужинов под цель
      </label>
      <p style={{ fontSize: 12, color: COLORS.inkSoft, margin: '4px 0 0' }}>
        {scaling.enabled ? `Порции — под ${scaling.kcal_target} ккал.` : 'Порции — как в рецептах.'}
        {scaling.changes_from && <> Изменение — с {weekdayShort(scaling.changes_from)} {fmtDate(scaling.changes_from)}, купленное не меняется.</>}
      </p>
      <FieldError error={saveError} action="set-portion-scaling" />
    </section>
  );
}

// ---------- Блюда: список рецептов, архив (5.5) ----------
type DishesSectionProps = {
  recipes: Recipes;
  onEdit: (editing: Editing) => void;
  recipeAction: RecipeAction;
};

function DishesSection({ recipes, onEdit, recipeAction }: DishesSectionProps) {
  // Ошибка архива — у своего рецепта (например, «стоит в цикле, позиция 3»).
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [archiveOpen, setArchiveOpen] = useState(false);
  const archived = KINDS.flatMap(({ kind }) => recipesOfKind(recipes, kind, 'archived'));

  const run = async (name: 'archive-recipe' | 'restore-recipe', id: string) => {
    const result = await recipeAction(name, { id });
    setErrors((all) => ({ ...all, [id]: result ? Object.values(result).join('; ') : '' }));
  };

  const row = (r: Recipe, actions: React.ReactNode) => (
    <li key={r.id} style={{ marginBottom: 6 }}>
      {r.name} <span style={{ fontSize: 12, color: COLORS.inkSoft }}>≈{r.kcal} ккал · {r.protein}г белка</span>
      <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>{actions}</div>
      {errors[r.id] && (
        <p role="alert" style={errorTextStyle}>
          {errors[r.id]}
        </p>
      )}
    </li>
  );

  return (
    <section style={cardStyle}>
      <h2 style={{ ...sectionHeading, marginTop: 0 }}>Блюда</h2>
      {KINDS.map(({ kind, title, add }) => (
        <div key={kind} style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: COLORS.primarySoft, marginBottom: 4 }}>{title}</div>
          <ul style={{ margin: '0 0 6px', paddingLeft: 18, fontSize: 13 }}>
            {recipesOfKind(recipes, kind).map((r) =>
              row(
                r,
                <>
                  <button onClick={() => onEdit({ id: r.id, kind })} style={smallBtnStyle}>
                    Изменить
                  </button>
                  <button onClick={() => run('archive-recipe', r.id)} style={smallBtnStyle}>
                    В архив
                  </button>
                </>,
              ),
            )}
          </ul>
          <button onClick={() => onEdit({ id: null, kind })} style={{ ...smallBtnStyle, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <Plus size={14} /> {add}
          </button>
        </div>
      ))}
      {archived.length > 0 && (
        <div>
          <button
            onClick={() => setArchiveOpen(!archiveOpen)}
            style={{ display: 'flex', alignItems: 'center', gap: 4, background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontSize: 12, fontWeight: 700, color: COLORS.primarySoft }}
          >
            {archiveOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />} Архив ({archived.length})
          </button>
          {archiveOpen && (
            <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 13, color: COLORS.inkSoft }}>
              {archived.map((r) =>
                row(
                  r,
                  <button onClick={() => run('restore-recipe', r.id)} style={smallBtnStyle}>
                    Вернуть
                  </button>,
                ),
              )}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

// ---------- Редактор рецепта (5.5) ----------
// Форма хранит то, что ввёл пользователь, строками; в числа — только при
// отправке. Ккал и белок черновика считает сервер (api/recipes/preview).

type DraftLine = { ingredient_id: string; qty: string };
type Draft = {
  name: string;
  kind: Kind;
  is_fish: boolean;
  lines: DraftLine[];
  steps: string[];
  storage: { keep: string; pan: string; micro: string; after: string };
};

const PREVIEW_DELAY_MS = 300;
const STORAGE_FIELDS = [
  { field: 'keep', label: 'Как хранить' },
  { field: 'pan', label: 'Разогрев на сковороде' },
  { field: 'micro', label: 'Разогрев в микроволновке' },
  { field: 'after', label: 'Добавить после разогрева (необязательно)' },
] as const;

function draftOf(recipe: Recipe | undefined, kind: Kind): Draft {
  return {
    name: recipe?.name ?? '',
    kind: recipe?.kind ?? kind,
    is_fish: recipe?.is_fish ?? false,
    lines: recipe
      ? recipe.ingredients.map((i) => ({ ingredient_id: String(i.ingredient_id), qty: i.qty_display }))
      : [{ ingredient_id: '', qty: '' }],
    steps: recipe ? [...recipe.steps] : [''],
    storage: {
      keep: recipe?.storage?.keep ?? '',
      pan: recipe?.storage?.pan ?? '',
      micro: recipe?.storage?.micro ?? '',
      after: recipe?.storage?.after ?? '',
    },
  };
}

/** «12,5» и «12.5» — одно число; непонятное — 0, сервер ответит ошибкой у поля. */
function toNumber(text: string): number {
  const n = Number(text.trim().replace(',', '.'));
  return text.trim() && Number.isFinite(n) ? n : 0;
}

function linesPayload(lines: DraftLine[]) {
  return lines.map((l) => ({ ingredient_id: Number(l.ingredient_id) || 0, qty: toNumber(l.qty) }));
}

function payloadOf(draft: Draft, id: string | null): RecipePayload {
  return {
    ...(id ? { id } : {}),
    name: draft.name,
    kind: draft.kind,
    is_fish: draft.is_fish,
    ingredients: linesPayload(draft.lines),
    steps: draft.steps,
    storage:
      draft.kind === 'breakfast'
        ? null
        : { keep: draft.storage.keep, pan: draft.storage.pan, micro: draft.storage.micro, after: draft.storage.after || null },
  };
}

const PLAN_USE_WARNING: Record<PlanUse, string> = {
  bought: 'Продукты на это блюдо уже куплены: правка изменит план на текущие дни.',
  next_trip: 'Блюдо в ближайшей закупке: список покупок изменится.',
};

type RecipeEditorProps = {
  editing: Editing;
  recipe: Recipe | undefined;
  planUse: PlanUse | undefined;
  catalog: IngredientsCatalog | null;
  loadIngredients: () => Promise<void>;
  recipeAction: RecipeAction;
  onClose: () => void;
};

function RecipeEditor({ editing, recipe, planUse, catalog, loadIngredients, recipeAction, onClose }: RecipeEditorProps) {
  const ingredients = catalog?.ingredients ?? null;
  const [draft, setDraft] = useState<Draft>(() => draftOf(recipe, editing.kind));
  const [errors, setErrors] = useState<FieldErrors>({});
  const [saving, setSaving] = useState(false);
  const nutrition = useNutritionPreview(draft.lines, recipe);

  useEffect(() => {
    if (!catalog) loadIngredients();
  }, [catalog, loadIngredients]);

  const update = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }));
  const setLine = (i: number, patch: Partial<DraftLine>) =>
    update({ lines: draft.lines.map((l, n) => (n === i ? { ...l, ...patch } : l)) });
  const setStep = (i: number, text: string) => update({ steps: draft.steps.map((s, n) => (n === i ? text : s)) });
  // Ошибки строк привязаны к номеру строки — после удаления они бы съехали.
  const removeAt = (field: 'lines' | 'steps', i: number) => {
    update({ [field]: draft[field].filter((_, n) => n !== i) });
    setErrors({});
  };

  const save = async () => {
    setSaving(true);
    const result = await recipeAction('save-recipe', payloadOf(draft, editing.id));
    setSaving(false);
    if (result) setErrors(result);
    else onClose();
  };

  const byId = new Map((ingredients ?? []).map((i) => [String(i.id), i]));
  const groups = catalog ? ingredientGroups(catalog) : [];
  const hasStorage = draft.kind !== 'breakfast';
  const shownKeys = new Set([
    'name',
    'ingredients',
    'steps',
    'storage',
    ...draft.lines.flatMap((_, i) => [`ingredients.${i}.ingredient_id`, `ingredients.${i}.qty`]),
    ...draft.steps.map((_, i) => `steps.${i}`),
    ...(hasStorage ? STORAGE_FIELDS.map(({ field }) => `storage.${field}`) : []),
  ]);
  const otherErrors = Object.entries(errors).filter(([key]) => !shownKeys.has(key));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <button onClick={onClose} style={{ ...ghostBtnStyle, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <ArrowLeft size={14} /> Назад
        </button>
        <h2 style={{ ...sectionHeading, margin: 0, flex: 1 }}>{recipe ? 'Изменить рецепт' : `${KIND_NAME[draft.kind]}: новый рецепт`}</h2>
        <button onClick={save} disabled={saving} style={{ ...primaryBtnStyle, opacity: saving ? 0.6 : 1 }}>
          Сохранить
        </button>
      </div>

      {planUse && (
        <p role="status" style={{ margin: 0, padding: 10, borderRadius: 10, background: COLORS.accentSoft, fontSize: 13 }}>
          {PLAN_USE_WARNING[planUse]}
        </p>
      )}

      <section style={cardStyle}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>
          {nutrition ? `≈${nutrition.kcal} ккал · ${nutrition.protein}г белка` : '— ккал'}
        </div>
        <div style={{ fontSize: 12, color: COLORS.inkSoft }}>считает сервер по ингредиентам</div>
      </section>

      <section style={cardStyle}>
        <label style={labelStyle}>
          Название
          <input value={draft.name} onChange={(e) => update({ name: e.target.value })} style={{ ...inputStyle, marginTop: 4 }} />
        </label>
        <ErrorText errors={errors} at="name" />

        <label style={{ ...labelStyle, marginTop: 10 }}>
          Тип
          {recipe ? (
            <div style={{ fontWeight: 400, fontSize: 13, color: COLORS.ink, marginTop: 4 }}>{KIND_NAME[draft.kind]}</div>
          ) : (
            <select value={draft.kind} onChange={(e) => update({ kind: e.target.value as Kind })} style={{ ...inputStyle, marginTop: 4 }}>
              {KINDS.map(({ kind, one }) => (
                <option key={kind} value={kind}>{one}</option>
              ))}
            </select>
          )}
        </label>

        <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 10, fontSize: 13 }}>
          <input type="checkbox" checked={draft.is_fish} onChange={(e) => update({ is_fish: e.target.checked })} />
          Рыба/морепродукты
        </label>
      </section>

      <section style={cardStyle}>
        <div style={labelStyle}>Ингредиенты (1 порция)</div>
        {!ingredients && <p style={{ fontSize: 13, color: COLORS.inkSoft, margin: 0 }}>Загрузка справочника…</p>}
        {ingredients &&
          draft.lines.map((line, i) => (
            <div key={i} style={{ marginBottom: 8 }}>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <select
                  aria-label={`Ингредиент ${i + 1}`}
                  value={line.ingredient_id}
                  onChange={(e) => setLine(i, { ingredient_id: e.target.value })}
                  style={{ ...inputStyle, flex: 1 }}
                >
                  <option value="">— выберите —</option>
                  {groups.map((g) => (
                    <optgroup key={g.category} label={g.category}>
                      {g.items.map((item) => (
                        <option key={item.id} value={item.id}>{item.name}</option>
                      ))}
                    </optgroup>
                  ))}
                </select>
                <input
                  aria-label={`Количество ${i + 1}`}
                  inputMode="decimal"
                  value={line.qty}
                  onChange={(e) => setLine(i, { qty: e.target.value })}
                  style={{ ...inputStyle, width: 70 }}
                />
                <span style={{ fontSize: 12, width: 44 }}>{byId.get(line.ingredient_id)?.unit ?? ''}</span>
                <button aria-label={`Убрать ингредиент ${i + 1}`} onClick={() => removeAt('lines', i)} style={iconBtnStyle}>
                  <X size={14} />
                </button>
              </div>
              <ErrorText errors={errors} at={`ingredients.${i}.ingredient_id`} />
              <ErrorText errors={errors} at={`ingredients.${i}.qty`} />
            </div>
          ))}
        <ErrorText errors={errors} at="ingredients" />
        <button onClick={() => update({ lines: [...draft.lines, { ingredient_id: '', qty: '' }] })} style={smallBtnStyle}>
          + ингредиент
        </button>
      </section>

      <section style={cardStyle}>
        <div style={labelStyle}>Приготовление</div>
        {draft.steps.map((step, i) => (
          <div key={i} style={{ marginBottom: 8 }}>
            <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
              <span style={{ fontSize: 13, paddingTop: 8 }}>{i + 1}.</span>
              <textarea
                aria-label={`Шаг ${i + 1}`}
                value={step}
                rows={3}
                onChange={(e) => setStep(i, e.target.value)}
                style={{ ...inputStyle, flex: 1, resize: 'vertical' }}
              />
              <button aria-label={`Убрать шаг ${i + 1}`} onClick={() => removeAt('steps', i)} style={iconBtnStyle}>
                <X size={14} />
              </button>
            </div>
            <ErrorText errors={errors} at={`steps.${i}`} />
          </div>
        ))}
        <ErrorText errors={errors} at="steps" />
        <button onClick={() => update({ steps: [...draft.steps, ''] })} style={smallBtnStyle}>
          + шаг
        </button>
      </section>

      {hasStorage && (
        <section style={cardStyle}>
          <div style={labelStyle}>Хранение и разогрев</div>
          {STORAGE_FIELDS.map(({ field, label }) => (
            <label key={field} style={{ ...labelStyle, fontWeight: 500, marginTop: 6 }}>
              {label}
              <textarea
                value={draft.storage[field]}
                rows={2}
                onChange={(e) => update({ storage: { ...draft.storage, [field]: e.target.value } })}
                style={{ ...inputStyle, marginTop: 4, resize: 'vertical' }}
              />
              <ErrorText errors={errors} at={`storage.${field}`} />
            </label>
          ))}
          <ErrorText errors={errors} at="storage" />
        </section>
      )}

      {otherErrors.map(([key, msg]) => (
        <p key={key} role="alert" style={errorTextStyle}>
          Не удалось сохранить: {msg}
        </p>
      ))}
    </div>
  );
}

/**
 * Ккал и белок черновика: пока строки ингредиентов как у сохранённого
 * рецепта — его значения; после правки — ответ сервера с задержкой, устаревший
 * ответ отбрасывается.
 */
function useNutritionPreview(lines: DraftLine[], recipe: Recipe | undefined): Nutrition | null {
  const saved = recipe ? { kcal: recipe.kcal, protein: recipe.protein } : null;
  const [initialKey] = useState(() => JSON.stringify(lines));
  const [preview, setPreview] = useState<Nutrition | null>(saved);
  const latest = useRef(0);
  const key = JSON.stringify(lines);

  useEffect(() => {
    const id = ++latest.current;
    if (key === initialKey) {
      setPreview(saved);
      return;
    }
    const timer = setTimeout(async () => {
      try {
        const counted = linesPayload(JSON.parse(key) as DraftLine[]).filter((l) => l.ingredient_id);
        const result = await previewRecipe(counted);
        if (id === latest.current) setPreview(result);
      } catch {
        // предпросмотр не удался — остаётся прошлое значение, сохранение покажет ошибку
      }
    }, PREVIEW_DELAY_MS);
    return () => clearTimeout(timer);
    // saved — значения того же рецепта, меняются вместе с ним (key у редактора)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, initialKey]);

  return preview;
}

function ErrorText({ errors, at }: { errors: FieldErrors; at: string }) {
  if (!errors[at]) return null;
  return (
    <p role="alert" style={errorTextStyle}>
      {errors[at]}
    </p>
  );
}

// ---------- Цикл: обеды и ужины по позициям (5.6, 8.4b) ----------
type CycleSectionProps = {
  slots: AppState['cycle_slots'];
  order: AppState['cycle_order'];
  recipes: Recipes;
  saveError: SaveError | null;
  runAction: RunAction;
};

type Slot = AppState['cycle_slots'][number];

function CycleSection({ slots, order, recipes, saveError, runAction }: CycleSectionProps) {
  // Состав позиции — списками целиком; с какого цикла он действует, решает сервер.
  const save = (slot: Slot, change: { lunch_ids?: string[]; dinner_ids?: string[] }) =>
    runAction('set-cycle-slot', { slot: slot.slot, lunch_ids: slot.lunch_ids, dinner_ids: slot.dinner_ids, ...change });
  return (
    <section style={cardStyle}>
      <h2 style={{ ...sectionHeading, marginTop: 0 }}>Цикл из 5 блюд</h2>
      <p style={{ fontSize: 12, color: COLORS.inkSoft, margin: '0 0 8px' }}>
        Несколько вариантов в позиции идут по кругу, по одному на цикл. Новый обед или ужин встаёт в позицию сам.
      </p>
      {slots.map((slot) => (
        <div key={slot.slot} style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: COLORS.primarySoft, marginBottom: 4 }}>Блюдо №{slot.slot}</div>
          <div style={{ display: 'flex', gap: 6 }}>
            <VariantList
              label="Обед"
              add="+ обед"
              slot={slot.slot}
              ids={slot.lunch_ids}
              options={recipesOfKind(recipes, 'lunch')}
              onChange={(lunch_ids) => save(slot, { lunch_ids })}
            />
            <VariantList
              label="Ужин"
              add="+ ужин"
              slot={slot.slot}
              ids={slot.dinner_ids}
              options={recipesOfKind(recipes, 'dinner')}
              onChange={(dinner_ids) => save(slot, { dinner_ids })}
            />
          </div>
          {slot.changes_from && (
            <p style={{ fontSize: 12, color: COLORS.inkSoft, margin: '4px 0 0' }}>
              Изменение — с {weekdayShort(slot.changes_from)} {fmtDate(slot.changes_from)}, купленное не меняется.
            </p>
          )}
        </div>
      ))}
      <FieldError error={saveError} action="set-cycle-slot" />
      <CycleOrderControls order={order} saveError={saveError} runAction={runAction} />
    </section>
  );
}

type VariantListProps = {
  label: string;
  add: string;
  slot: number;
  ids: string[];
  options: Recipe[];
  onChange: (ids: string[]) => void;
};

function VariantList({ label, add, slot, ids, options, onChange }: VariantListProps) {
  // «+» добавляет первый рецепт, которого ещё нет в позиции, — дальше его меняют в списке.
  const unused = options.find((r) => !ids.includes(r.id));
  const smallBtn = { ...ghostBtnStyle, padding: '2px 8px', fontSize: 12 };
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
      {ids.map((id, i) => (
        <div key={i} style={{ display: 'flex', gap: 4 }}>
          <select
            aria-label={`${label} №${slot}${ids.length > 1 ? `, вариант ${i + 1}` : ''}`}
            value={id}
            onChange={(e) => onChange(ids.map((x, j) => (j === i ? e.target.value : x)))}
            style={{ ...inputStyle, flex: 1, minWidth: 0 }}
          >
            {options.map((r) => (
              <option key={r.id} value={r.id}>{r.name}</option>
            ))}
          </select>
          {ids.length > 1 && (
            <button
              aria-label={`Убрать вариант ${i + 1} (${label.toLowerCase()} №${slot})`}
              onClick={() => onChange(ids.filter((_, j) => j !== i))}
              style={smallBtn}
            >
              ×
            </button>
          )}
        </div>
      ))}
      {unused && (
        <button onClick={() => onChange([...ids, unused.id])} style={{ ...smallBtn, alignSelf: 'flex-start' }}>
          {add}
        </button>
      )}
    </div>
  );
}

// ---------- Перемешивание цикла (8.4a) ----------
type CycleOrderControlsProps = {
  order: AppState['cycle_order'];
  saveError: SaveError | null;
  runAction: RunAction;
};

function CycleOrderControls({ order, saveError, runAction }: CycleOrderControlsProps) {
  const smallBtn = { ...ghostBtnStyle, padding: '4px 10px', fontSize: 12 };
  return (
    <div style={{ borderTop: `1px solid ${COLORS.line}`, paddingTop: 10, marginTop: 4 }}>
      <p style={{ fontSize: 12, color: COLORS.inkSoft, margin: 0 }}>
        {order.shuffled ? 'Порядок перемешан: каждые 5 дней — свой.' : 'Блюда идут по кругу 1→5.'}
        {order.changes_from && (
          <> Изменение — с {weekdayShort(order.changes_from)} {fmtDate(order.changes_from)}, купленное не меняется.</>
        )}
      </p>
      <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
        <button onClick={() => runAction('reshuffle-cycle', {})} style={smallBtn}>
          {order.shuffled ? 'Перемешать ещё раз' : 'Перемешать порядок'}
        </button>
        {order.shuffled && (
          <button onClick={() => runAction('reset-cycle-order', {})} style={smallBtn}>
            Вернуть порядок 1→5
          </button>
        )}
      </div>
      <FieldError error={saveError} action="reshuffle-cycle" />
      <FieldError error={saveError} action="reset-cycle-order" />
    </div>
  );
}

// ---------- Справочник ингредиентов (5.6) ----------

/** Группы для списков: категории — в порядке сервера, внутри — по алфавиту. */
function ingredientGroups(catalog: IngredientsCatalog) {
  return catalog.categories
    .map((category) => ({
      category,
      items: catalog.ingredients
        .filter((i) => i.category === category)
        .sort((a, b) => a.name.localeCompare(b.name, 'ru')),
    }))
    .filter((g) => g.items.length > 0);
}

type IngredientsSectionProps = {
  catalog: IngredientsCatalog | null;
  loadIngredients: () => Promise<void>;
  onEdit: (id: number | 'new') => void;
};

function IngredientsSection({ catalog, loadIngredients, onEdit }: IngredientsSectionProps) {
  const [open, setOpen] = useState(false);
  // Справочник грузится, только когда его раскрыли.
  useEffect(() => {
    if (open && !catalog) loadIngredients();
  }, [open, catalog, loadIngredients]);

  return (
    <section style={cardStyle}>
      <h2 style={{ ...sectionHeading, marginTop: 0 }}>Ингредиенты</h2>
      <button
        onClick={() => setOpen(!open)}
        style={{ display: 'flex', alignItems: 'center', gap: 4, background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontSize: 13, fontWeight: 600, color: COLORS.primarySoft }}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />} Справочник{catalog ? ` (${catalog.ingredients.length})` : ''}
      </button>
      {open && !catalog && <p style={{ fontSize: 13, color: COLORS.inkSoft }}>Загрузка справочника…</p>}
      {open &&
        catalog &&
        ingredientGroups(catalog).map((g) => (
          <div key={g.category} style={{ marginTop: 8 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: COLORS.primarySoft, marginBottom: 2 }}>{g.category}</div>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
              {g.items.map((i) => (
                <li key={i.id} style={{ marginBottom: 4 }}>
                  {i.name}{' '}
                  <span style={{ fontSize: 12, color: COLORS.inkSoft }}>
                    {i.unit} · {i.kcal_100g} ккал · {i.protein_100g}г белка на 100 г
                  </span>{' '}
                  <button onClick={() => onEdit(i.id)} style={smallBtnStyle}>
                    Изменить
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      <button onClick={() => onEdit('new')} style={{ ...smallBtnStyle, display: 'inline-flex', alignItems: 'center', gap: 4, marginTop: 10 }}>
        <Plus size={14} /> Новый ингредиент
      </button>
    </section>
  );
}

// Форма ингредиента: числа — строками, при отправке пустое → null (сервер
// ответит «укажите …» у поля, если поле обязательное).
type IngredientDraft = {
  name: string;
  unit: string;
  category: string;
  kcal_100g: string;
  protein_100g: string;
  grams_per_unit: string;
  store_piece: string;
  source: string;
};

const INGREDIENT_NUMBERS = [
  { field: 'kcal_100g', label: 'Ккал на 100 г' },
  { field: 'protein_100g', label: 'Белок на 100 г, г' },
  { field: 'grams_per_unit', label: 'Граммов в единице (для шт, ст.л., зубчик)' },
  { field: 'store_piece', label: 'Штука в магазине, в единице ингредиента (необязательно)' },
] as const;

function optionalNumber(text: string): number | null {
  return text.trim() ? toNumber(text) : null;
}

function text(value: number | null | undefined): string {
  return value === null || value === undefined ? '' : String(value);
}

type IngredientEditorProps = {
  id: number | null;
  catalog: IngredientsCatalog | null;
  recipes: Recipes;
  loadIngredients: () => Promise<void>;
  ingredientAction: (payload: IngredientPayload) => Promise<FieldErrors | null>;
  onClose: () => void;
};

function IngredientEditor({ id, catalog, recipes, loadIngredients, ingredientAction, onClose }: IngredientEditorProps) {
  useEffect(() => {
    if (!catalog) loadIngredients();
  }, [catalog, loadIngredients]);
  if (!catalog) return <p style={{ color: COLORS.inkSoft, textAlign: 'center', marginTop: 40 }}>Загрузка справочника…</p>;
  const ingredient = id === null ? undefined : catalog.ingredients.find((i) => i.id === id);
  return (
    <IngredientForm
      id={id}
      ingredient={ingredient}
      catalog={catalog}
      recipes={recipes}
      ingredientAction={ingredientAction}
      onClose={onClose}
    />
  );
}

type IngredientFormProps = Omit<IngredientEditorProps, 'catalog' | 'loadIngredients'> & {
  ingredient: IngredientsCatalog['ingredients'][number] | undefined;
  catalog: IngredientsCatalog;
};

function IngredientForm({ id, ingredient, catalog, recipes, ingredientAction, onClose }: IngredientFormProps) {
  const [draft, setDraft] = useState<IngredientDraft>(() => ({
    name: ingredient?.name ?? '',
    unit: ingredient?.unit ?? catalog.units[0],
    category: ingredient?.category ?? catalog.categories[0],
    kcal_100g: text(ingredient?.kcal_100g),
    protein_100g: text(ingredient?.protein_100g),
    grams_per_unit: text(ingredient?.grams_per_unit),
    store_piece: text(ingredient?.store_piece),
    source: ingredient?.source ?? '',
  }));
  const [errors, setErrors] = useState<FieldErrors>({});
  const [saving, setSaving] = useState(false);
  const update = (patch: Partial<IngredientDraft>) => setDraft((d) => ({ ...d, ...patch }));
  const usedIn = (ingredient?.used_in ?? []).map((rid) => recipes[rid]?.name ?? rid);

  const save = async () => {
    setSaving(true);
    const result = await ingredientAction({
      ...(id === null ? {} : { id }),
      name: draft.name,
      unit: draft.unit,
      category: draft.category,
      kcal_100g: optionalNumber(draft.kcal_100g),
      protein_100g: optionalNumber(draft.protein_100g),
      grams_per_unit: optionalNumber(draft.grams_per_unit),
      store_piece: optionalNumber(draft.store_piece),
      source: draft.source || null,
    });
    setSaving(false);
    if (result) setErrors(result);
    else onClose();
  };

  const shownKeys = new Set(['name', 'unit', 'category', 'source', ...INGREDIENT_NUMBERS.map(({ field }) => field)]);
  const otherErrors = Object.entries(errors).filter(([key]) => !shownKeys.has(key));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <button onClick={onClose} style={{ ...ghostBtnStyle, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <ArrowLeft size={14} /> Назад
        </button>
        <h2 style={{ ...sectionHeading, margin: 0, flex: 1 }}>{ingredient ? 'Изменить ингредиент' : 'Новый ингредиент'}</h2>
        <button onClick={save} disabled={saving} style={{ ...primaryBtnStyle, opacity: saving ? 0.6 : 1 }}>
          Сохранить
        </button>
      </div>

      {usedIn.length > 0 && (
        <p role="status" style={{ margin: 0, padding: 10, borderRadius: 10, background: COLORS.accentSoft, fontSize: 13 }}>
          В рецептах: {usedIn.join(', ')}. Ккал и белок этих рецептов пересчитаются; единицу не поменять.
        </p>
      )}

      <section style={cardStyle}>
        <label style={labelStyle}>
          Название
          <input value={draft.name} onChange={(e) => update({ name: e.target.value })} style={{ ...inputStyle, marginTop: 4 }} />
        </label>
        <ErrorText errors={errors} at="name" />

        <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
          <label style={{ ...labelStyle, flex: 1 }}>
            Единица
            <select
              value={draft.unit}
              disabled={usedIn.length > 0}
              onChange={(e) => update({ unit: e.target.value })}
              style={{ ...inputStyle, marginTop: 4 }}
            >
              {catalog.units.map((u) => (
                <option key={u} value={u}>{u}</option>
              ))}
            </select>
            <ErrorText errors={errors} at="unit" />
          </label>
          <label style={{ ...labelStyle, flex: 2 }}>
            Категория
            <select value={draft.category} onChange={(e) => update({ category: e.target.value })} style={{ ...inputStyle, marginTop: 4 }}>
              {catalog.categories.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
            <ErrorText errors={errors} at="category" />
          </label>
        </div>
      </section>

      <section style={cardStyle}>
        {INGREDIENT_NUMBERS.map(({ field, label }) => (
          <label key={field} style={{ ...labelStyle, marginTop: 6 }}>
            {label}
            <input
              inputMode="decimal"
              value={draft[field]}
              onChange={(e) => update({ [field]: e.target.value })}
              style={{ ...inputStyle, marginTop: 4 }}
            />
            <ErrorText errors={errors} at={field} />
          </label>
        ))}
        <label style={{ ...labelStyle, marginTop: 6 }}>
          Источник (например, fdc 171287 или этикетка)
          <input value={draft.source} onChange={(e) => update({ source: e.target.value })} style={{ ...inputStyle, marginTop: 4 }} />
          <ErrorText errors={errors} at="source" />
        </label>
      </section>

      {otherErrors.map(([key, msg]) => (
        <p key={key} role="alert" style={errorTextStyle}>
          Не удалось сохранить: {msg}
        </p>
      ))}
    </div>
  );
}

/** Ошибка сохранения — в секции того действия, которое её вызвало. */
function FieldError({ error, action }: { error: SaveError | null; action: ActionName }) {
  if (error?.action !== action) return null;
  return (
    <p role="alert" style={{ color: '#8a3b2b', fontSize: 12, margin: '8px 0 0' }}>
      Не удалось сохранить: {error.message}
    </p>
  );
}

const cardStyle: CSSProperties = {
  background: COLORS.panel,
  borderRadius: 14,
  padding: 14,
  border: `1px solid ${COLORS.line}`,
};
const labelStyle: CSSProperties = { display: 'block', fontSize: 12, fontWeight: 600, color: COLORS.primarySoft, marginBottom: 4 };
const inputStyle: CSSProperties = {
  width: '100%',
  padding: '8px 10px',
  borderRadius: 8,
  border: `1px solid ${COLORS.line}`,
  fontSize: 13,
  background: '#fff',
  color: COLORS.ink,
};
const ghostBtnStyle: CSSProperties = {
  padding: '8px 14px',
  borderRadius: 8,
  border: `1px solid ${COLORS.primary}`,
  background: '#fff',
  color: COLORS.primary,
  fontWeight: 600,
  fontSize: 13,
  cursor: 'pointer',
};
const smallBtnStyle: CSSProperties = { ...ghostBtnStyle, padding: '3px 10px', fontSize: 12, fontWeight: 500 };
const primaryBtnStyle: CSSProperties = { ...ghostBtnStyle, background: COLORS.primary, color: '#fff' };
const iconBtnStyle: CSSProperties = { ...ghostBtnStyle, padding: '4px 6px', border: `1px solid ${COLORS.line}`, color: COLORS.inkSoft };
const errorTextStyle: CSSProperties = { color: '#8a3b2b', fontSize: 12, margin: '4px 0 0' };
const sectionHeading: CSSProperties = {
  fontFamily: 'ui-serif, Georgia, serif',
  fontSize: 15,
  fontWeight: 700,
  margin: '20px 0 8px',
  color: COLORS.primary,
};
