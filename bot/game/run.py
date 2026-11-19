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

from bot.game.classes import CLASSES
from bot.game.combat import SKILL_SCOUT, Fighter, Move, RoundResult, Side, resolve_round
from bot.game.world import Finding, Location, drop_chance

# Дна у похода нет: ниже третьего этажа ждёт та же тварь, только матёрая.
# Предохранитель на случай, если игрок окажется неубиваемым, — чтобы поход
# не мог крутиться вечно.
MAX_STEPS = 30

# Пассивка хиллера: после каждой убитой твари залечивает такую долю максимума.
HEALER_REGEN = 0.15

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
    cls: str = ""
    charges: int = 0
    # Ход твари в текущем раунде, если его уже подсмотрела Разведка: тогда
    # раунд обязан пройти именно с ним, иначе подсказка была бы враньём.
    monster_move: str = ""

    def dumps(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def loads(cls, raw: str) -> "RunState":
        return cls(**json.loads(raw))

    @property
    def steps_left(self) -> int:
        return MAX_STEPS - self.step + 1


def start(location: Location, hp: int, rng: random.Random,
          cls: str | None = None) -> RunState:
    monster = location.monster_at(1)
    return RunState(location=location.key, hp=hp,
                    monster_name=monster.name, monster_hp=monster.hp,
                    cls=cls or "", charges=CLASSES[cls].charges if cls else 0)


def can_use_skill(state: RunState) -> bool:
    return bool(state.cls) and state.charges > 0 and state.phase == PHASE_FIGHT


def scout(state: RunState, location: Location, rng: random.Random) -> Move:
    """Разведка: тварь выбирает ход заранее, игрок его видит. Тратит заряд,
    но не раунд — следующим нажатием игрок отвечает уже зная ход."""
    state.charges -= 1
    if not state.monster_move:
        state.monster_move = location.monster_spec(state.floor).choose(rng).value
    return Move(state.monster_move)


def monster_fighter(state: RunState, location: Location) -> Fighter:
    """Ищем тварь по этажу, а не по имени: ниже дна имена повторяются."""
    monster = location.monster_spec(state.floor)
    return Fighter(name=monster.name, hp=state.monster_hp, hp_max=monster.hp,
                   atk=monster.atk, defense=monster.defense)


def fight_round(state: RunState, player: Fighter, location: Location,
                move: Move | None, rng: random.Random,
                use_skill: bool = False) -> RoundResult:
    """Один обмен ударами. Тратит шаг и двигает поход дальше.

    use_skill — нажат навык класса (кроме Разведки, у неё своя `scout`)."""
    monster_def = location.monster_spec(state.floor)
    monster = monster_fighter(state, location)

    their = Move(state.monster_move) if state.monster_move else monster_def.choose(rng)
    state.monster_move = ""

    skill = None
    if use_skill:
        skill = CLASSES[state.cls].skill
        assert skill != SKILL_SCOUT, "Разведка — не действие в раунде"
        state.charges -= 1

    result = resolve_round(player, monster, move, their, rng,
                           side_a=Side(cls=state.cls or None, skill=skill))

    state.hp = player.hp
    state.monster_hp = monster.hp
    if not monster.alive:
        state.defeated.append(monster.name)
        state.exp += location.exp_for(state.floor)
        state.step += 1  # встреча закрыта, счётчик двигает только она
        if state.cls == "healer" and state.hp > 0:
            state.hp = min(player.hp_max, state.hp + round(player.hp_max * HEALER_REGEN))

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
    # Бросок на каждую, из выпавших — случайная. Раньше брали первую выпавшую,
    # и первая в списке находка падала заметно чаще остальных.
    dropped = [f for f in location.findings
               if rng.random() < drop_chance(already_found.get(f.key, 0), f)]
    prize: Finding | None = rng.choice(dropped) if dropped else None
    if prize:
        state.found.append(prize.key)

    state.step += 1
    _advance(state, location, monster_alive=False, rng=rng)
    return prize
