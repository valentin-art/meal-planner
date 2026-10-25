import { type CSSProperties, useCallback, useState } from 'react';
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
} from 'lucide-react';

import type { ActionName, ActionPayloads, AppState, Recipe } from './api-client';
import { WEEKDAY_FULL, fmtDate, weekdayFull, weekdayShort } from './format';
import { type SaveError, useAppState } from './useAppState';

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
type Recipes = AppState['recipes'];
type MealDay = Day & { cycle_index: number; breakfast_id: string; lunch_id: string; dinner_id: string };
type RunAction = <N extends ActionName>(name: N, payload: ActionPayloads[N]) => Promise<void>;
type Tab = 'schedule' | 'menu' | 'shopping' | 'settings';

export default function MealPlanApp() {
  const { state, range, loadError, saveError, showRange, runAction, retry } = useAppState();
  const [tab, setTab] = useState<Tab>('schedule');
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
            onClick={() => setTab(id)}
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
        {!state || !selected ? (
          loadError ? (
            <div style={{ textAlign: 'center', marginTop: 40 }}>
              <p style={{ color: '#8a3b2b' }}>Не удалось загрузить план: {loadError}</p>
              <button onClick={retry} style={ghostBtnStyle}>Повторить</button>
            </div>
          ) : (
            <p style={{ color: COLORS.inkSoft, textAlign: 'center', marginTop: 40 }}>Загрузка…</p>
          )
        ) : tab === 'schedule' ? (
          <ScheduleTab
            state={state}
            selectedDateStr={selected}
            goToDate={goToDate}
            goToToday={goToToday}
            loadMore={loadMore}
            onOpenDay={(dateStr) => {
              goToDate(dateStr);
              setTab('menu');
            }}
          />
        ) : tab === 'menu' ? (
          <MenuTab
            state={state}
            selectedDateStr={selected}
            goToDate={goToDate}
            openRecipe={openRecipe}
            setOpenRecipe={setOpenRecipe}
            loadMore={loadMore}
          />
        ) : tab === 'shopping' ? (
          <ShoppingTab trip={state.next_shopping_trip} recipes={state.recipes} />
        ) : (
          <SettingsTab
            settings={state.settings}
            position={state.today_position}
            recipes={state.recipes}
            saveError={saveError}
            runAction={runAction}
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

/** До якоря (день 0 и раньше) блюд нет — сервер присылает null. */
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

// ---------- Schedule Tab ----------
type ScheduleTabProps = {
  state: AppState;
  selectedDateStr: string;
  goToDate: (iso: string) => void;
  goToToday: () => void;
  loadMore: () => void;
  onOpenDay: (iso: string) => void;
};

function ScheduleTab({ state, selectedDateStr, goToDate, goToToday, loadMore, onOpenDay }: ScheduleTabProps) {
  const { schedule, recipes, today, settings } = state;
  const isToday = selectedDateStr === today;
  return (
    <div>
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
              ) : !hasMeals(day) ? (
                <CycleStartsNote anchor={settings.meal_anchor_date} style={{ fontSize: 13, margin: '6px 0 0' }} />
              ) : (
                <div style={{ fontSize: 13, marginTop: 6, lineHeight: 1.7 }}>
                  <div><b>Завтрак:</b> {recipes[day.breakfast_id].name}</div>
                  <div><b>Обед:</b> {recipes[day.lunch_id].name}{recipes[day.lunch_id].is_fish && <span style={{ color: COLORS.flame }}> · рыба/морепродукты</span>}</div>
                  <div><b>Ужин:</b> {recipes[day.dinner_id].name}{recipes[day.dinner_id].is_fish && <span style={{ color: COLORS.flame }}> · рыба/морепродукты</span>}</div>
                  <div style={{ fontSize: 12, color: COLORS.inkSoft, marginTop: 4 }}>
                    Итого: ≈{day.kcal_total} ккал · {day.protein_total}г белка
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
  selectedDateStr: string;
  goToDate: (iso: string) => void;
  openRecipe: string | null;
  setOpenRecipe: (id: string | null) => void;
  loadMore: () => void;
};

function MenuTab({ state, selectedDateStr, goToDate, openRecipe, setOpenRecipe, loadMore }: MenuTabProps) {
  const [showStoveInfo, setShowStoveInfo] = useState(false);
  const [showStorageInfo, setShowStorageInfo] = useState(false);
  const { schedule, recipes, today } = state;
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
          const noMenu = d.is_day_zero || d.is_before_start;
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
        ) : !hasMeals(day) ? (
          <CycleStartsNote anchor={state.settings.meal_anchor_date} style={{ fontSize: 14, margin: 0 }} />
        ) : (
          <>
            <p style={{ fontSize: 13, margin: '0 0 10px' }}>
              <span style={{ fontSize: 11, color: COLORS.accent, fontWeight: 700 }}>ЗАВТРАК</span><br />
              {recipes[day.breakfast_id].name}{' '}
              <span style={{ fontSize: 12, color: COLORS.inkSoft }}>(≈{recipes[day.breakfast_id].kcal} ккал · {recipes[day.breakfast_id].protein}г)</span>
            </p>
            <RecipeBlock recipe={recipes[day.lunch_id]} slot="Обед" openRecipe={openRecipe} setOpenRecipe={setOpenRecipe} />
            <div style={{ height: 10 }} />
            <RecipeBlock recipe={recipes[day.dinner_id]} slot="Ужин" openRecipe={openRecipe} setOpenRecipe={setOpenRecipe} />
            <p style={{ fontSize: 13, fontWeight: 600, color: COLORS.primarySoft, marginTop: 12, marginBottom: 0, borderTop: `1px solid ${COLORS.line}`, paddingTop: 8 }}>
              Итого за день: ≈{day.kcal_total} ккал · {day.protein_total}г белка
            </p>
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

function recipesOfKind(recipes: Recipes, kind: Recipe['kind']): Recipe[] {
  return Object.values(recipes).filter((r) => r.kind === kind);
}

type RecipeToggleProps = {
  recipe: Recipe;
  openRecipe: string | null;
  setOpenRecipe: (id: string | null) => void;
};

function RecipeBlock({ recipe: r, slot, openRecipe, setOpenRecipe }: RecipeToggleProps & { slot: string }) {
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
          <span style={{ fontSize: 12, color: COLORS.inkSoft }}>≈{r.kcal} ккал · {r.protein}г белка</span>
        </span>
        {isOpen ? <ChevronDown size={18} color={COLORS.inkSoft} /> : <ChevronRight size={18} color={COLORS.inkSoft} />}
      </button>
      {isOpen && <RecipeDetails recipe={r} />}
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

function RecipeDetails({ recipe }: { recipe: Recipe }) {
  return (
    <div style={{ marginTop: 10, fontSize: 13 }}>
      <b>Ингредиенты (1 порция):</b>
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
  trip: Trip;
  recipes: Recipes;
};

function ShoppingTab({ trip, recipes }: ShoppingTabProps) {
  // Окно, что готовить и что купить — целиком из next_shopping_trip.
  return (
    <div>
      <section style={{ ...cardStyle, borderLeft: `4px solid ${COLORS.accent}` }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <span style={{ fontFamily: 'ui-serif, Georgia, serif', fontWeight: 700, fontSize: 16 }}>Ближайшая закупка</span>
          <span style={{ fontSize: 12, color: COLORS.inkSoft }}>{fmtDate(trip.shopping_date)}</span>
        </div>
        <p style={{ fontSize: 12, color: COLORS.inkSoft, margin: '4px 0 0' }}>
          Готовка — {weekdayShort(trip.cook_date)} {fmtDate(trip.cook_date)}. Едим с {weekdayShort(trip.window_start)} {fmtDate(trip.window_start)} по {weekdayShort(trip.window_end)} {fmtDate(trip.window_end)} включительно ({trip.window_days} {trip.window_days === 1 ? 'день' : 'дня/дней'}).
        </p>

        <h3 style={{ fontSize: 13, color: COLORS.flame, margin: '12px 0 6px' }}>
          <ChefHat size={14} style={{ verticalAlign: '-2px', marginRight: 4 }} />
          Что готовить
        </h3>
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
          {trip.cook_items.map((it, i) => (
            <li key={i} style={{ marginBottom: 2 }}>
              {`${recipes[it.lunch_id].name} + ${recipes[it.dinner_id].name}`} <span style={{ color: COLORS.inkSoft }}>— x{it.portions}</span>
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
  recipes: Recipes;
  saveError: SaveError | null;
  runAction: RunAction;
};

function SettingsTab({ settings, position, recipes, saveError, runAction }: SettingsTabProps) {
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
              Цикл начнётся {weekdayShort(settings.meal_anchor_date)} {fmtDate(settings.meal_anchor_date)}
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

      <section style={cardStyle}>
        {/* Заготовка под фазу 5: пока только список, без редактирования. */}
        <h2 style={{ ...sectionHeading, marginTop: 0 }}>Блюда</h2>
        {(
          [
            { kind: 'lunch', title: 'Обеды' },
            { kind: 'dinner', title: 'Ужины' },
            { kind: 'breakfast', title: 'Завтраки' },
          ] as const
        ).map(({ kind, title }) => (
          <div key={kind} style={{ marginBottom: 8 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: COLORS.primarySoft, marginBottom: 2 }}>{title}</div>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
              {recipesOfKind(recipes, kind).map((r) => (
                <li key={r.id}>
                  {r.name} <span style={{ fontSize: 12, color: COLORS.inkSoft }}>≈{r.kcal} ккал · {r.protein}г белка</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>
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
const sectionHeading: CSSProperties = {
  fontFamily: 'ui-serif, Georgia, serif',
  fontSize: 15,
  fontWeight: 700,
  margin: '20px 0 8px',
  color: COLORS.primary,
};
