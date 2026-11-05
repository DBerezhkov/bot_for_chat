"""Состояние похода: встречи, развилки и сундуки.

Шаг — это встреча целиком: убитая тварь или открытый сундук. Бой идёт столько
раундов, сколько нужно, и обрываться на середине из-за счётчика не может —
иначе поход заканчивался бы ничем ровно там, где становится интересно.

Здоровье между встречами не восстанавливается, поэтому после каждой победы
игрок решает сам: уйти с тем, что есть, или пойти дальше на оставшихся HP.
"""

import json
import random
from dataclasses import asdict, dataclass, field

from bot.game.combat import Fighter, Move, RoundResult, resolve_round
from bot.game.world import Finding, Location, drop_chance

# Дна у похода нет: ниже третьего этажа ждёт та же тварь, только матёрая.
# Предохранитель на случай, если игрок окажется неубиваемым, — чтобы поход
# не мог крутиться вечно.
MAX_STEPS = 30

PHASE_FIGHT = "fight"
PHASE_FORK = "fork"
PHASE_DONE = "done"


@dataclass
class RunState:
    location: str
    hp: int
    step: int = 1
    phase: str = PHASE_FIGHT
    # Номер этажа: он же решает, кто встретится и сколько за него дадут.
    floor: int = 1
    monster_name: str = ""
    monster_hp: int = 0
    # За какой из двух троп спрятан сундук. Вторая всегда ведёт в бой —
    # так выбор остаётся честной монеткой и не вырождается в «оба сундука».
    chest_side: str = "left"
    exp: int = 0
    found: list[str] = field(default_factory=list)
    defeated: list[str] = field(default_factory=list)
    dead: bool = False

    def dumps(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def loads(cls, raw: str) -> "RunState":
        return cls(**json.loads(raw))

    @property
    def steps_left(self) -> int:
        return MAX_STEPS - self.step + 1


def start(location: Location, hp: int, rng: random.Random) -> RunState:
    monster = location.monster_at(1)
    return RunState(location=location.key, hp=hp,
                    monster_name=monster.name, monster_hp=monster.hp)


def monster_fighter(state: RunState, location: Location) -> Fighter:
    """Ищем тварь по этажу, а не по имени: ниже дна имена повторяются."""
    monster = location.monster_spec(state.floor)
    return Fighter(name=monster.name, hp=state.monster_hp, hp_max=monster.hp,
                   atk=monster.atk, defense=monster.defense)


def fight_round(state: RunState, player: Fighter, location: Location,
                move: Move, rng: random.Random) -> RoundResult:
    """Один обмен ударами. Тратит шаг и двигает поход дальше."""
    monster_def = location.monster_spec(state.floor)
    monster = monster_fighter(state, location)

    result = resolve_round(player, monster, move, monster_def.choose(rng), rng)

    state.hp = player.hp
    state.monster_hp = monster.hp
    if not monster.alive:
        state.defeated.append(monster.name)
        state.exp += location.exp_for(state.floor)
        state.step += 1  # встреча закрыта, счётчик двигает только она

    _advance(state, location, monster_alive=monster.alive, rng=rng)
    return result


def _advance(state: RunState, location: Location, monster_alive: bool,
             rng: random.Random) -> None:
    if state.hp <= 0:
        state.dead = True
        state.phase = PHASE_DONE
    elif state.step > MAX_STEPS:
        state.phase = PHASE_DONE
    elif monster_alive:
        state.phase = PHASE_FIGHT
    else:
        state.phase = PHASE_FORK
        state.chest_side = rng.choice(["left", "right"])


def leave(state: RunState) -> None:
    """Уйти с добычей. Единственный способ закончить поход живым и по своей воле."""
    state.phase = PHASE_DONE


def take_path(state: RunState, location: Location, side: str,
              rng: random.Random) -> bool:
    """Выбор тропы. True — впереди сундук, False — новый противник."""
    if side == state.chest_side:
        return True

    state.floor += 1
    monster = location.monster_at(state.floor)
    state.monster_name = monster.name
    state.monster_hp = monster.hp
    state.phase = PHASE_FIGHT
    return False


def open_chest(state: RunState, location: Location, already_found: dict[str, int],
               rng: random.Random) -> Finding | None:
    """Открывает сундук: тратит шаг и, если повезёт, отдаёт находку."""
    prize: Finding | None = None
    for finding in location.findings:
        if rng.random() < drop_chance(already_found.get(finding.key, 0), finding):
            prize = finding
            state.found.append(finding.key)
            break

    state.step += 1
    _advance(state, location, monster_alive=False, rng=rng)
    return prize
