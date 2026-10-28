"""Порядок слотов в цикле (8.4a): перемешивание с зерном.

Цикл n — дни ленты 5n..5n+4 от якоря; каждый — своя перестановка слотов.
На стыке циклов любые 4 дня подряд — разные блюда, поэтому партия до 4 дней
без повторов при любых днях готовки. Следующий цикл выбирается только из
перестановок, совместимых с хвостом предыдущего, — порядок идёт цепочкой от
первой строки CycleShuffle. От партий и дней готовки он не зависит.
"""

from functools import lru_cache
from hashlib import sha256
from itertools import permutations

from meal_planner.core.models import CycleShuffle

CYCLE_LEN = 5
NATURAL = tuple(range(1, CYCLE_LEN + 1))
DISTINCT_DAYS = 4  # любые столько дней подряд — разные блюда
ALL_ORDERS = tuple(permutations(NATURAL))
# Повороты круга 1→5: с какого слота ни начни, дальше по порядку.
ROTATIONS = tuple(NATURAL[r:] + NATURAL[:r] for r in range(CYCLE_LEN))


def fits_after(prev: tuple[int, ...], order: tuple[int, ...]) -> bool:
    """На стыке prev | order любые DISTINCT_DAYS дней подряд разные."""
    tape = prev + order
    return all(
        len(set(tape[i : i + DISTINCT_DAYS])) == DISTINCT_DAYS
        for i in range(CYCLE_LEN - DISTINCT_DAYS + 1, CYCLE_LEN)
    )


@lru_cache(maxsize=4096)
def shuffled_after(prev: tuple[int, ...], seed: int, n: int) -> tuple[int, ...]:
    # sha256, а не random.shuffle: алгоритмы random меняются между версиями
    # Python, а план должен остаться тем же после обновления образа.
    candidates = [o for o in ALL_ORDERS if o != prev and fits_after(prev, o)]
    return min(
        candidates,
        key=lambda o: sha256(f"{seed}:{n}:{''.join(map(str, o))}".encode()).digest(),
    )


@lru_cache(maxsize=128)
def natural_after(prev: tuple[int, ...]) -> tuple[int, ...]:
    """Обратно к кругу 1→5: поворот, если он допустим сразу, иначе один
    переходный цикл, после которого поворот допустим."""
    fitting = [o for o in ALL_ORDERS if fits_after(prev, o)]
    rotations = [o for o in fitting if o in ROTATIONS]
    if rotations:
        return min(rotations)
    return min(o for o in fitting if any(fits_after(o, r) for r in ROTATIONS))


@lru_cache(maxsize=4096)
def cycle_order(shuffles: tuple[CycleShuffle, ...], n: int) -> tuple[int, ...]:
    """Слоты цикла n по дням: order[i % 5] — слот дня ленты i."""
    if not shuffles or n < shuffles[0].from_cycle:
        return NATURAL
    # Цепочка от первой строки — ~73 цикла в год; шаги закэшированы.
    order = NATURAL
    rows = list(shuffles)
    current = None
    for c in range(shuffles[0].from_cycle, n + 1):
        while rows and rows[0].from_cycle <= c:
            current = rows.pop(0)
        assert current is not None
        if current.seed is None:
            order = order if order in ROTATIONS else natural_after(order)
        else:
            order = shuffled_after(order, current.seed, c)
    return order
