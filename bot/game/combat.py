"""Ядро боя: камень/ножницы/бумага на трёх приёмах.

🛡️ Парирование бьёт ⚔️ Удар, ⚔️ Удар бьёт 🗡️ Укол, 🗡️ Укол бьёт 🛡️ Парирование.
Одинаковые приёмы — ничья, урона нет ни у кого.

Ничего не знает ни про Telegram, ни про базу — только числа, чтобы механику
можно было прогонять тестами и балансировать отдельно от всего остального.
"""

import random
from dataclasses import dataclass
from enum import StrEnum


class Move(StrEnum):
    STRIKE = "strike"  # ⚔️ Удар — рубит укол
    PARRY = "parry"    # 🛡️ Парирование — гасит удар
    THRUST = "thrust"  # 🗡️ Укол — проходит мимо парирования


LABELS = {
    Move.STRIKE: "⚔️ Удар",
    Move.PARRY: "🛡️ Парирование",
    Move.THRUST: "🗡️ Укол",
}

# Кольцо: кто кого бьёт. Единственный источник истины — из него собирается
# матрица, поэтому «камень» физически не может обыграть сам себя.
BEATS: dict[Move, Move] = {
    Move.STRIKE: Move.THRUST,
    Move.PARRY: Move.STRIKE,
    Move.THRUST: Move.PARRY,
}


def _multipliers(mine: Move, his: Move) -> tuple[float, float]:
    """Урон наносит только победитель раунда. Одинаковые ходы — ничья."""
    if BEATS[mine] == his:
        return (1.0, 0.0)
    if BEATS[his] == mine:
        return (0.0, 1.0)
    return (0.0, 0.0)


# (мой ход, его ход) -> (мой множитель урона по нему, его множитель по мне)
MULTIPLIERS: dict[tuple[Move, Move], tuple[float, float]] = {
    (mine, his): _multipliers(mine, his) for mine in Move for his in Move
}

# Описание исхода глазами игрока: (мой ход, его ход) -> строка.
OUTCOMES: dict[tuple[Move, Move], str] = {
    (Move.STRIKE, Move.STRIKE): "Клинки встречаются в воздухе. Оба целы.",
    (Move.STRIKE, Move.PARRY): "Удар уходит в парирование — и пропадает зря.",
    (Move.STRIKE, Move.THRUST): "Удар сбивает укол с линии и достаёт первым.",
    (Move.PARRY, Move.STRIKE): "Парирование гасит удар, ты отвечаешь коротко и точно.",
    (Move.PARRY, Move.PARRY): "Оба закрылись. Постояли, посмотрели друг на друга.",
    (Move.PARRY, Move.THRUST): "Укол находит щель в парировании.",
    (Move.THRUST, Move.STRIKE): "Укол не успевает — тебя рубят первым.",
    (Move.THRUST, Move.PARRY): "Укол проходит мимо клинка и достаёт цель.",
    (Move.THRUST, Move.THRUST): "Два укола расходятся впритирку. Оба мимо.",
}


@dataclass
class Fighter:
    name: str
    hp: int
    hp_max: int
    atk: int
    defense: int

    @property
    def alive(self) -> bool:
        return self.hp > 0

    @property
    def hp_percent(self) -> float:
        return self.hp / self.hp_max if self.hp_max else 0.0


@dataclass
class RoundResult:
    move_a: Move
    move_b: Move
    damage_to_a: int
    damage_to_b: int
    description: str


# Масштаб защиты: при таком делителе первое очко защиты снимает ~9% входящего
# урона. С делителем «под сотню» защита на малых статах не значила бы ничего.
DEFENSE_SCALE = 10


def base_damage(attacker: Fighter, defender: Fighter) -> float:
    """Защита урон делит, а не вычитает — иначе слабый бьющий уходит в ноль."""
    return attacker.atk * DEFENSE_SCALE / (DEFENSE_SCALE + defender.defense)


def _roll(attacker: Fighter, defender: Fighter, multiplier: float,
          rng: random.Random) -> int:
    if multiplier == 0:
        return 0
    damage = base_damage(attacker, defender) * multiplier * rng.uniform(0.9, 1.1)
    return max(1, round(damage))


def resolve_round(a: Fighter, b: Fighter, move_a: Move, move_b: Move,
                  rng: random.Random | None = None) -> RoundResult:
    """Считает один обмен ударами и сразу применяет урон к бойцам."""
    rng = rng or random.Random()
    mult_a, mult_b = MULTIPLIERS[(move_a, move_b)]

    damage_to_b = _roll(a, b, mult_a, rng)
    damage_to_a = _roll(b, a, mult_b, rng)

    a.hp = max(0, a.hp - damage_to_a)
    b.hp = max(0, b.hp - damage_to_b)

    return RoundResult(
        move_a=move_a, move_b=move_b,
        damage_to_a=damage_to_a, damage_to_b=damage_to_b,
        description=OUTCOMES[(move_a, move_b)],
    )
