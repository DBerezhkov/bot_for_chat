"""Классы персонажей: множители статов, пассивка и навык.

Все числа классов — здесь. Механика навыков и пассивок считается в
`combat.resolve_round`, а этот модуль только описывает, у кого что есть.
Статы в базе хранятся «голыми» (уровни + находки), класс накладывается
при каждом бою — так смена класса не требует пересчёта базы.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class GameClass:
    key: str
    icon: str
    title: str
    hp: float
    atk: float
    defense: float
    skill: str        # ключ навыка, см. combat.SKILL_*
    charges: int      # зарядов навыка на поход
    about: str        # одна строка для /class
    # Множители (HP, атака, защита) для дуэлей. Свои, а не общие: классы
    # нетранзитивны, и коэффициенты, ровные в подземельях, в дуэлях давали
    # перекос до 65/35. Подобраны симуляцией: при равном уровне любая пара
    # классов выигрывает друг у друга примерно поровну.
    duel: tuple[float, float, float] = (1.0, 1.0, 1.0)

    @property
    def label(self) -> str:
        return f"{self.icon} {self.title}"

    @property
    def skill_label(self) -> str:
        from bot.game.combat import SKILL_LABELS
        return SKILL_LABELS[self.skill]

    def apply(self, hp: int, atk: int, defense: int,
              duel: bool = False) -> tuple[int, int, int]:
        """Голые статы → статы с классом. Округление вверх от половины,
        а не банковское: иначе 4.5 атаки превращались бы в 4."""
        m_hp, m_atk, m_def = self.duel if duel else (self.hp, self.atk, self.defense)
        return (max(1, _round(hp * m_hp)), max(1, _round(atk * m_atk)),
                _round(defense * m_def))


def stats_for(cls: str | None, hp: int, atk: int, defense: int,
              duel: bool = False) -> tuple[int, int, int]:
    """Статы в бою: голые, если класс ещё не выбран."""
    return CLASSES[cls].apply(hp, atk, defense, duel) if cls else (hp, atk, defense)


def _round(x: float) -> int:
    return int(x + 0.5)


CLASSES: dict[str, GameClass] = {c.key: c for c in (
    GameClass(
        key="warrior", icon="🛡", title="Воин",
        hp=1.15, atk=1.0, defense=1.4,
        skill="shield", charges=3, duel=(1.1, 0.9, 1.4),
        about="Живучий. Проигранный раунд режет урон на четверть. "
              "Щит: раунд, который нельзя проиграть.",
    ),
    GameClass(
        key="rogue", icon="🗡", title="Разбойник",
        hp=0.9, atk=1.2, defense=1.0,
        skill="scout", charges=3, duel=(1.0, 1.25, 0.95),
        about="Выигранный Укол бьёт в полтора раза сильнее. "
              "Разведка: подсмотреть ход врага до своего.",
    ),
    GameClass(
        key="mage", icon="🔮", title="Маг",
        hp=0.85, atk=1.4, defense=0.5,
        skill="fireball", charges=2, duel=(0.75, 1.4, 0.65),
        about="Хрупкий, бьёт больнее всех, ничья тоже жжёт врага. "
              "Огненный шар: урон мимо защиты, но и сам не прикрыт.",
    ),
    GameClass(
        key="healer", icon="✨", title="Хиллер",
        hp=1.1, atk=1.0, defense=1.0,
        skill="heal", charges=3, duel=(1.05, 1.05, 1.25),
        about="После каждой убитой твари залечивает часть ран — ныряет глубже всех. "
              "Лечение: почти половина здоровья за раз.",
    ),
)}

# Смена класса: не чаще раза в неделю и за монеты, иначе класс ничего не значит.
CHANGE_COST = 500
CHANGE_COOLDOWN_DAYS = 7
