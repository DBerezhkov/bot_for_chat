"""Локации, их обитатели и постоянные находки."""

import math
import random
from dataclasses import dataclass, replace

from bot.game.combat import Fighter, Move


@dataclass(frozen=True)
class Monster:
    name: str
    hp: int
    atk: int
    defense: int
    # Характер: с какими весами тварь выбирает удар, парирование и укол.
    # Повадки постоянные — на этом и строится вся стратегия игрока.
    weights: tuple[int, int, int]

    def spawn(self) -> Fighter:
        return Fighter(name=self.name, hp=self.hp, hp_max=self.hp,
                       atk=self.atk, defense=self.defense)

    def choose(self, rng: random.Random) -> Move:
        return rng.choices(
            [Move.STRIKE, Move.PARRY, Move.THRUST], weights=self.weights
        )[0]


@dataclass(frozen=True)
class Finding:
    key: str
    name: str
    stat: str  # "atk" или "defense"
    bonus: int
    hp_bonus: int = 0
    limit: int = 3


@dataclass(frozen=True)
class Location:
    key: str
    icon: str
    title: str
    min_level: int
    monsters: tuple[Monster, ...]
    findings: tuple[Finding, ...]

    @property
    def label(self) -> str:
        return f"{self.icon} {self.title}"

    @property
    def levels(self) -> str:
        return f"ур. {self.min_level}–{self.min_level + 3}"

    def verdict(self, level: int) -> str:
        """Стоит ли туда соваться на таком уровне."""
        if level < self.min_level:
            return "смертельно"
        if level > self.min_level + 3:
            return "по силам"
        return "по уровню"

    def button(self, level: int) -> str:
        """Уровень в кнопке: сунуться можно куда угодно, но вслепую — не надо."""
        mark = {"смертельно": "☠️", "по уровню": "🎯", "по силам": "✅"}
        return f"{mark[self.verdict(level)]} {self.label} · {self.levels}"

    def monster_spec(self, depth: int) -> Monster:
        """Кто ждёт на такой-то глубине.

        Первые этажи — свои твари по нарастанию: иначе новичку первым же
        броском выпадал бы вожак, и «локация на четыре уровня» превращалась бы
        в рулетку. Ниже дна подземелье не кончается — там та же последняя
        тварь, но матёрая: каждый этаж вглубь множит ей всё на DEEPER_SCALE.
        """
        depth = max(depth, 1)
        base = self.monsters[min(depth, len(self.monsters)) - 1]
        if depth <= len(self.monsters):
            return base

        factor = DEEPER_SCALE ** (depth - len(self.monsters))
        return replace(
            base,
            name=f"{base.name} ×{depth - len(self.monsters) + 1}",
            hp=round(base.hp * factor),
            atk=round(base.atk * factor),
            defense=round(base.defense * factor),
        )

    def exp_for(self, depth: int) -> int:
        """Опыт за тварь с такого этажа.

        Цена — глубина: первый этаж локации стоит столько, сколько её входной
        уровень. Иначе выгодно было бы вечно фармить первый этаж норы: побед
        там втрое больше, а сложности никакой. Ниже дна цена растёт так же,
        как и сама тварь.
        """
        depth = max(depth, 1)
        floor = min(depth, len(self.monsters))
        price = self.min_level + floor - 1
        if depth > len(self.monsters):
            # Округляем вверх: иначе первые этажи ниже дна стоили бы столько же,
            # сколько само дно, и нырок за него был бы бесплатной щедростью.
            return math.ceil(price * DEEPER_EXP_SCALE ** (depth - len(self.monsters)))
        return price

    def monster_at(self, depth: int) -> Fighter:
        return self.monster_spec(depth).spawn()

    def monster_by_name(self, name: str) -> Monster:
        return next(m for m in self.monsters if m.name == name)


# Броня даёт ещё и здоровье: чистая защита проигрывает атаке, потому что урон
# она делит, а атака его множит.
#
# Статы твари подобраны под уровень локации через пороги боя. Облезлая крыса
# ровно зеркалит новичка (10 HP, 5 атаки) — против неё бой честная монетка,
# если жать наугад, и уверенная победа, если знать повадки. Крысиный вожак
# выравнивается с игроком только к 4 уровню, на нём нора и заканчивается.
LOCATIONS: tuple[Location, ...] = (
    Location(
        key="rat_field",
        icon="🐀",
        title="Крысиная нора",
        min_level=1,
        monsters=(
            # Зеркало новичка. Трусливая, всё время закрывается — берётся уколом.
            Monster("Облезлая крыса", hp=6, atk=4, defense=0, weights=(15, 60, 25)),
            # Кидается без разбора — ловится парированием. Ровня на 2 уровне.
            Monster("Крыса-падальщик", hp=10, atk=6, defense=3, weights=(70, 25, 5)),
            # Тычет мордой вперёд — сбивается ударом. Ровня игроку 4 уровня.
            Monster("Крысиный вожак", hp=17, atk=8, defense=3, weights=(25, 15, 60)),
        ),
        findings=(
            Finding("rusty_sword", "🗡 Ржавый меч", stat="atk", bonus=1, limit=3),
            Finding("worn_vest", "🛡 Потёртый жилет", stat="defense", bonus=1,
                    hp_bonus=3, limit=3),
        ),
    ),
    Location(
        key="wolf_forest",
        icon="🐺",
        title="Волчий лес",
        min_level=5,
        # Волк бьёт так, что новичка сносит с одного попадания.
        monsters=(
            # Молодой и злой — бросается первым, ловится парированием.
            Monster("Молодой волк", hp=9, atk=12, defense=8, weights=(65, 30, 5)),
            # Битый жизнью, осторожничает и держит защиту — вскрывается уколом.
            Monster("Матёрый волк", hp=16, atk=12, defense=7, weights=(20, 55, 25)),
            # Бьёт короткими выпадами — сбивается ударом.
            Monster("Вожак стаи", hp=12, atk=27, defense=7, weights=(20, 15, 65)),
        ),
        findings=(
            Finding("alpha_fang", "🗡 Клык вожака", stat="atk", bonus=1, limit=3),
            Finding("wolf_pelt", "🛡 Волчья шкура", stat="defense", bonus=1,
                    hp_bonus=3, limit=3),
        ),
    ),
    Location(
        key="spider_catacombs",
        icon="🕷",
        title="Паучьи катакомбы",
        min_level=9,
        monsters=(
            # Бьёт лапами вразнобой — сбивается ударом.
            Monster("Пещерный паук", hp=12, atk=22, defense=11, weights=(25, 15, 60)),
            # Кидается из темноты — ловится парированием.
            Monster("Ткачиха", hp=18, atk=22, defense=12, weights=(60, 30, 10)),
            # Закрывается лапами, как щитом — вскрывается уколом.
            Monster("Матка выводка", hp=26, atk=22, defense=12, weights=(15, 55, 30)),
        ),
        findings=(
            Finding("venom_sting", "🗡 Ядовитое жало", stat="atk", bonus=2, limit=3),
            Finding("chitin_plate", "🛡 Хитиновый панцирь", stat="defense", bonus=2,
                    hp_bonus=5, limit=3),
        ),
    ),
    Location(
        key="barrow",
        icon="💀",
        title="Курган мертвецов",
        min_level=13,
        monsters=(
            # Стоит стеной со щитом — вскрывается уколом.
            Monster("Костяной ратник", hp=16, atk=25, defense=11, weights=(20, 60, 20)),
            # Тычет обломком копья — сбивается ударом.
            Monster("Могильный упырь", hp=16, atk=37, defense=11, weights=(25, 20, 55)),
            # Рубит с плеча — ловится парированием.
            Monster("Курганный владыка", hp=20, atk=37, defense=16, weights=(65, 25, 10)),
        ),
        findings=(
            Finding("dead_blade", "🗡 Клинок мертвеца", stat="atk", bonus=2, limit=3),
            Finding("barrow_mail", "🛡 Курганный доспех", stat="defense", bonus=2,
                    hp_bonus=5, limit=3),
        ),
    ),
    Location(
        key="lair",
        icon="🔥",
        title="Логово",
        min_level=17,
        monsters=(
            # Наскакивает без разбора — ловится парированием.
            Monster("Огненный бес", hp=20, atk=35, defense=11, weights=(55, 35, 10)),
            # Колет трезубцем — сбивается ударом.
            Monster("Надсмотрщик", hp=20, atk=53, defense=11, weights=(20, 25, 55)),
            # Держит глухую защиту и бьёт страшно — только уколом.
            Monster("Владыка логова", hp=21, atk=53, defense=19, weights=(25, 55, 20)),
        ),
        findings=(
            Finding("ember_claw", "🗡 Огненный коготь", stat="atk", bonus=3, limit=3),
            Finding("lord_scale", "🛡 Чешуя владыки", stat="defense", bonus=3,
                    hp_bonus=8, limit=3),
        ),
    ),
)

LOCATIONS_BY_KEY = {loc.key: loc for loc in LOCATIONS}

# Насколько злее становится тварь за каждый этаж ниже дна подземелья
# и насколько дороже она стоит. Цена растёт медленнее силы намеренно: иначе
# перекачанному игроку выгоднее вечно нырять в крысиную нору, чем идти
# в подземелье по своему уровню.
DEEPER_SCALE = 1.25
DEEPER_EXP_SCALE = 1.15

FIRST_DROP_CHANCE = 0.10
REPEAT_DROP_CHANCE = 0.05


def drop_chance(already_found: int, finding: Finding) -> float:
    """Первая находка идёт легче повторной, после лимита не падает вовсе."""
    if already_found >= finding.limit:
        return 0.0
    return FIRST_DROP_CHANCE if already_found == 0 else REPEAT_DROP_CHANCE
