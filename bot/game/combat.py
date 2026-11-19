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
    move_a: Move | None   # с каким приёмом вышел боец; None — навык без приёма
    move_b: Move | None
    damage_to_a: int
    damage_to_b: int
    description: str
    label_a: str = ""     # что показать игроку: приём или навык
    label_b: str = ""
    healed_a: int = 0
    healed_b: int = 0


# Навыки классов. Разведка — не действие в раунде, а подсказка до него:
# её обрабатывает поход или дуэль, сюда она не доходит.
SKILL_SHIELD = "shield"
SKILL_SCOUT = "scout"
SKILL_FIREBALL = "fireball"
SKILL_HEAL = "heal"

SKILL_TEXTS = {
    SKILL_SHIELD: "Щит принимает всё, проиграть этот раунд нельзя.",
    SKILL_FIREBALL: "Огненный шар летит мимо любой защиты.",
    SKILL_HEAL: "Свет затягивает раны.",
}

WARRIOR_GUARD = 0.75   # пассивка воина: проигранный раунд режет урон на четверть
ROGUE_THRUST = 1.5     # пассивка разбойника: выигранный укол
MAGE_DRAW = 0.2        # пассивка мага: ничья тоже жжёт
FIREBALL_POWER = 1.0   # огненный шар: доля атаки, без учёта защиты
HEAL_SHARE = 0.45      # лечение: доля максимума здоровья
HEAL_GUARD = 0.5       # во время лечения удар врага вдвое слабее

ATTACKS = (Move.STRIKE, Move.THRUST)


@dataclass(frozen=True)
class Side:
    """Кто сражается: класс бойца и жмёт ли он навык в этом раунде."""
    cls: str | None = None
    skill: str | None = None   # ключ навыка, если он нажат


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


def resolve_round(a: Fighter, b: Fighter, move_a: Move | None, move_b: Move | None,
                  rng: random.Random | None = None,
                  side_a: Side = Side(), side_b: Side = Side()) -> RoundResult:
    """Считает один обмен ударами и сразу применяет урон к бойцам.

    Без классов это чистое КНБ. Навыки встраиваются в него, а не обходят:
    Щит — это Парирование, которое не проигрывает; Огненный шар и Лечение
    выходят без приёма, и тогда врага бьёт любой его атакующий ход.
    """
    rng = rng or random.Random()
    eff_a = _effective(move_a, side_a)
    eff_b = _effective(move_b, side_b)

    mult_a, mult_b = _multipliers_with_skills(eff_a, eff_b)
    mult_a = _passive_out(side_a, eff_a, eff_b, mult_a)
    mult_b = _passive_out(side_b, eff_b, eff_a, mult_b)
    mult_b *= _guard(side_a)
    mult_a *= _guard(side_b)

    healed_a = _heal(a, side_a)
    healed_b = _heal(b, side_b)

    damage_to_b = _outgoing(a, b, side_a, mult_a, _guard(side_b), rng)
    damage_to_a = _outgoing(b, a, side_b, mult_b, _guard(side_a), rng)

    a.hp = max(0, a.hp - damage_to_a)
    b.hp = max(0, b.hp - damage_to_b)

    return RoundResult(
        move_a=eff_a, move_b=eff_b,
        damage_to_a=damage_to_a, damage_to_b=damage_to_b,
        description=_describe(eff_a, eff_b, side_a, side_b),
        label_a=_label(eff_a, side_a), label_b=_label(eff_b, side_b),
        healed_a=healed_a, healed_b=healed_b,
    )


def _effective(move: Move | None, side: Side) -> Move | None:
    if side.skill == SKILL_SHIELD:
        return Move.PARRY
    if side.skill in (SKILL_FIREBALL, SKILL_HEAL):
        return None
    return move


def _multipliers_with_skills(eff_a: Move | None, eff_b: Move | None) -> tuple[float, float]:
    if eff_a is not None and eff_b is not None:
        return MULTIPLIERS[(eff_a, eff_b)]
    # Кто вышел без приёма, тот не защищается: его достаёт любой атакующий ход.
    return (1.0 if eff_a in ATTACKS else 0.0, 1.0 if eff_b in ATTACKS else 0.0)


def _passive_out(side: Side, mine: Move | None, his: Move | None, mult: float) -> float:
    if side.cls == "rogue" and mine == Move.THRUST and mult > 0:
        return mult * ROGUE_THRUST
    if side.cls == "mage" and mine is not None and mine == his:
        return MAGE_DRAW
    return mult


def _guard(side: Side) -> float:
    """Во сколько раз режется урон, который этот боец получает."""
    if side.skill == SKILL_SHIELD:
        return 0.0
    guard = HEAL_GUARD if side.skill == SKILL_HEAL else 1.0
    if side.cls == "warrior":
        guard *= WARRIOR_GUARD
    return guard


def _heal(fighter: Fighter, side: Side) -> int:
    if side.skill != SKILL_HEAL:
        return 0
    before = fighter.hp
    fighter.hp = min(fighter.hp_max, fighter.hp + _round_half(fighter.hp_max * HEAL_SHARE))
    return fighter.hp - before


def _outgoing(attacker: Fighter, defender: Fighter, side: Side, mult: float,
              defender_guard: float, rng: random.Random) -> int:
    if side.skill == SKILL_FIREBALL:
        if defender_guard == 0:
            return 0
        damage = attacker.atk * FIREBALL_POWER * defender_guard * rng.uniform(0.9, 1.1)
        return max(1, round(damage))
    return _roll(attacker, defender, mult, rng)


def _round_half(x: float) -> int:
    return int(x + 0.5)


def _label(move: Move | None, side: Side) -> str:
    if side.skill in SKILL_LABELS:
        return SKILL_LABELS[side.skill]
    return LABELS[move] if move else ""


SKILL_LABELS = {
    SKILL_SHIELD: "🛡 Щит",
    SKILL_SCOUT: "👁 Разведка",
    SKILL_FIREBALL: "🔥 Шар",
    SKILL_HEAL: "✨ Лечение",
}


def _describe(eff_a: Move | None, eff_b: Move | None, side_a: Side, side_b: Side) -> str:
    for side in (side_a, side_b):
        if side.skill in SKILL_TEXTS:
            return SKILL_TEXTS[side.skill]
    return OUTCOMES[(eff_a, eff_b)]
