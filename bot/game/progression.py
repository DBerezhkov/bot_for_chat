"""Характеристики персонажа на заданном уровне, без находок.

Начисляет их `db.add_exp` — по одному уровню за раз, прямо в базе. Эта функция
даёт то же самое, но целиком и без базы: по ней считается баланс локаций
и проверяется, что начисление в базе от неё не разъехалось.
"""

import math
from math import comb

from bot import config
from bot.game.combat import DEFENSE_SCALE


def stats_at(level: int) -> tuple[int, int, int]:
    """(здоровье, атака, защита) на уровне `level`."""
    ups = level - 1
    defense_ups = level // config.LEVEL_DEF_EVERY
    return (
        config.START_HP + ups * config.LEVEL_HP,
        config.START_ATK + ups * config.LEVEL_ATK,
        config.START_DEF + defense_ups,
    )


def exp_to_next(level: int) -> int:
    """Сколько опыта нужно, чтобы уйти с этого уровня на следующий.

    Кривая подобрана под реальный доход из подземелий (он растёт вместе с
    уровнем, потому что растёт цена тварей): первый уровень берётся за пару
    побед в первый же день, второй — за пару дней, а последний стоит месяца.
    """
    return round(level ** 2.4) + level


def _hits(hp: int, atk: int, defense: int) -> int:
    """Сколько выигранных раундов нужно, чтобы снять цель с таким запасом."""
    return math.ceil(hp / (atk * DEFENSE_SCALE / (DEFENSE_SCALE + defense)))


def win_chance(level: int, monster) -> float:
    """Шанс игрока такого уровня одолеть тварь, если жать наугад.

    Исход раунда от характеристик не зависит вовсе — это монетка. Зависит,
    сколько раундов надо выиграть каждой стороне; отсюда и вся кривая
    сложности локаций.
    """
    hp, atk, defense = stats_at(level)
    mine = _hits(monster.hp, atk, monster.defense)
    theirs = _hits(hp, monster.atk, defense)
    return sum(comb(mine - 1 + k, k) * 0.5 ** (mine + k) for k in range(theirs))
