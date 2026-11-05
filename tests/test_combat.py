import math
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("CLAN_CHAT_ID", "-1001234567890")

from bot.game import run as run_mod  # noqa: E402
from bot.game.combat import (  # noqa: E402
    BEATS, DEFENSE_SCALE, LABELS, MULTIPLIERS, OUTCOMES, Fighter, Move, base_damage,
    resolve_round,
)
from bot.game.progression import stats_at, win_chance  # noqa: E402
from bot.game.world import (  # noqa: E402
    LOCATIONS, LOCATIONS_BY_KEY, drop_chance,
)

MOVES = list(Move)


def player(hp=100, atk=10, defense=5) -> Fighter:
    return Fighter(name="Игрок", hp=hp, hp_max=hp, atk=atk, defense=defense)


def dummy(hp=100, atk=10, defense=5) -> Fighter:
    return Fighter(name="Чучело", hp=hp, hp_max=hp, atk=atk, defense=defense)


# --- матрица ходов ---

def test_matrix_covers_all_pairs():
    assert set(MULTIPLIERS) == {(a, b) for a in MOVES for b in MOVES}
    assert set(OUTCOMES) == set(MULTIPLIERS)
    assert set(LABELS) == set(MOVES)


def test_matrix_is_symmetric():
    """Множители для (a, b) и (b, a) обязаны быть зеркальны, иначе один
    из бойцов получает преимущество просто за то, что он «первый»."""
    for a in MOVES:
        for b in MOVES:
            mine, his = MULTIPLIERS[(a, b)]
            his_mirror, mine_mirror = MULTIPLIERS[(b, a)]
            assert (mine, his) == (mine_mirror, his_mirror)


@pytest.mark.parametrize("winner, loser", [
    (Move.PARRY, Move.STRIKE),
    (Move.STRIKE, Move.THRUST),
    (Move.THRUST, Move.PARRY),
])
def test_rock_paper_scissors_cycle(winner, loser):
    """Парирование бьёт удар, удар бьёт укол, укол бьёт парирование."""
    mine, his = MULTIPLIERS[(winner, loser)]
    assert (mine, his) == (1.0, 0.0), "урон наносит только победитель раунда"


def test_cycle_has_no_dominant_move():
    """Кольцо замкнуто: каждый приём ровно один бьёт и ровно одному проигрывает."""
    assert set(BEATS) == set(MOVES)
    assert set(BEATS.values()) == set(MOVES)
    for move in MOVES:
        assert BEATS[move] != move


@pytest.mark.parametrize("move", list(Move))
def test_same_moves_deal_no_damage(move):
    """Ничья — это ничья: не «оба получили своё», а ноль урона обоим."""
    assert MULTIPLIERS[(move, move)] == (0.0, 0.0)

    a, b = player(), dummy()
    result = resolve_round(a, b, move, move, random.Random(1))
    assert (result.damage_to_a, result.damage_to_b) == (0, 0)
    assert (a.hp, b.hp) == (a.hp_max, b.hp_max)


# --- урон ---

def test_defense_divides_damage():
    assert base_damage(player(atk=10), dummy(defense=0)) == 10
    assert base_damage(player(atk=10), dummy(defense=DEFENSE_SCALE)) == pytest.approx(5)


def test_first_point_of_defense_is_worth_it():
    """На малых статах защита обязана чувствоваться с первого очка, иначе
    стат мёртвый: с делителем «под сотню» очко снимало бы 2% урона."""
    bare = base_damage(player(atk=10), dummy(defense=0))
    armoured = base_damage(player(atk=10), dummy(defense=1))
    assert bare / armoured - 1 > 0.08


def test_defense_never_zeroes_damage():
    """Даже против огромной защиты удар проходит хотя бы на единицу."""
    a, b = player(atk=1), dummy(defense=10_000)
    resolve_round(a, b, Move.STRIKE, Move.THRUST, random.Random(1))
    assert b.hp == b.hp_max - 1


@pytest.mark.parametrize("winner, loser", [
    (Move.PARRY, Move.STRIKE),
    (Move.STRIKE, Move.THRUST),
    (Move.THRUST, Move.PARRY),
])
def test_only_the_winner_deals_damage(winner, loser):
    a, b = player(), dummy()
    result = resolve_round(a, b, winner, loser, random.Random(1))
    assert result.damage_to_a == 0, "проигравший раунд не должен отвечать"
    assert result.damage_to_b > 0


def test_hp_never_goes_negative():
    a, b = player(), dummy(hp=1)
    resolve_round(a, b, Move.THRUST, Move.PARRY, random.Random(1))
    assert b.hp == 0
    assert not b.alive


def test_damage_spread_stays_within_ten_percent():
    rng = random.Random(0)
    a, b = player(), dummy(hp=10 ** 9)
    expected = base_damage(a, b)
    for _ in range(300):
        result = resolve_round(a, b, Move.STRIKE, Move.THRUST, rng)
        # Границы с поправкой на округление до целого.
        assert expected * 0.9 - 0.5 <= result.damage_to_b <= expected * 1.1 + 0.5


# --- находки ---

def test_drop_chances():
    finding = LOCATIONS[0].findings[0]
    assert drop_chance(0, finding) == 0.10
    assert drop_chance(1, finding) == 0.05
    assert drop_chance(finding.limit, finding) == 0.0
    assert drop_chance(finding.limit + 3, finding) == 0.0


def test_every_location_gives_both_stats():
    for location in LOCATIONS:
        stats = {f.stat for f in location.findings}
        assert stats == {"atk", "defense"}


def test_armour_also_gives_hp():
    """Иначе защита проигрывает атаке: урон она делит, а атака его множит."""
    for location in LOCATIONS:
        for finding in location.findings:
            if finding.stat == "defense":
                assert finding.hp_bonus > 0


def _best_answer(monster):
    """Какой ход выгоднее всего против известных повадок твари."""
    weights = [w / sum(monster.weights) for w in monster.weights]
    scores = {}
    for mine in MOVES:
        gain = sum(w * MULTIPLIERS[(mine, m)][0] for w, m in zip(weights, MOVES))
        taken = sum(w * MULTIPLIERS[(mine, m)][1] for w, m in zip(weights, MOVES))
        scores[mine] = gain - taken
    return max(scores, key=scores.get), scores


def test_each_location_demands_all_three_answers():
    """Если на всех тварей отвечает один и тот же ход, боя как выбора нет —
    игрок просто жмёт одну кнопку. Повадки обязаны быть разведены."""
    for location in LOCATIONS:
        answers = {_best_answer(m)[0] for m in location.monsters}
        assert answers == set(MOVES), f"{location.title}: ответы вырождены в {answers}"


def test_best_answer_has_a_noticeable_edge():
    """Перевес должен быть заметным, иначе знание повадок ничего не даёт."""
    for location in LOCATIONS:
        for monster in location.monsters:
            best, scores = _best_answer(monster)
            second = sorted(scores.values())[-2]
            assert scores[best] - second > 0.1, f"{monster.name}: выбор почти безразличен"


# --- баланс локаций ---

def _rounds_to_kill(attacker, defender) -> int:
    """Сколько раундов надо выиграть, чтобы снять цель. Именно это число, а не
    характеристики сами по себе, и решает исход боя."""
    return math.ceil(defender.hp_max / base_damage(attacker, defender))


def _hero_at(level: int) -> Fighter:
    hp, atk, defense = stats_at(level)
    return Fighter("Игрок", hp, hp, atk, defense)


def test_first_floor_is_passable_on_arrival():
    """Придя в локацию на её уровне, игрок обязан уверенно брать первый этаж —
    иначе он не увидит ничего, кроме экрана смерти."""
    for location in LOCATIONS:
        chance = win_chance(location.min_level, location.monsters[0])
        assert chance >= 0.62, f"{location.title}: первый этаж {chance:.0%}"


def test_floors_get_harder_going_down():
    """Этажи идут по нарастанию — на любом уровне диапазона."""
    for location in LOCATIONS:
        for level in range(location.min_level, location.min_level + 4):
            chances = [win_chance(level, m) for m in location.monsters]
            assert chances == sorted(chances, reverse=True), \
                f"{location.title}, ур.{level}: {[f'{c:.0%}' for c in chances]}"


def test_deepest_floor_opens_up_only_with_levels():
    """Последний этаж на входе почти закрыт, а к выходному уровню становится
    честной монеткой. Это и есть причина качаться внутри локации."""
    for location in LOCATIONS:
        lo = location.min_level
        last = location.monsters[-1]

        assert win_chance(lo, last) <= 0.32, f"{location.title}: низ слишком лёгкий"
        assert 0.44 <= win_chance(lo + 3, last) <= 0.56, \
            f"{location.title}: низ не выравнивается к выходу"
        # На старших локациях разрыв меньше: статы растут линейно, и четыре
        # уровня прибавляют там 15% силы против 90% в самом начале.
        assert win_chance(lo + 3, last) - win_chance(lo, last) >= 0.18


def test_next_dungeon_is_not_a_free_ride():
    """Пройдя локацию до конца, игрок не должен заходить в следующую как к себе
    домой — иначе уровни локаций декоративны, а вердикт в кнопке врёт."""
    for previous, following in zip(LOCATIONS, LOCATIONS[1:]):
        exit_level = previous.min_level + 3
        first_floor = win_chance(exit_level, following.monsters[0])
        assert first_floor <= 0.50, (
            f"{following.title}: на выходе из «{previous.title}» первый этаж "
            f"даёт {first_floor:.0%}"
        )


def test_rat_den_is_beatable_by_level_four():
    """Нора заканчивается там, где её последняя тварь становится ровней."""
    boss = LOCATIONS_BY_KEY["rat_field"].monsters[-1]
    assert win_chance(1, boss) <= 0.15
    assert 0.44 <= win_chance(4, boss) <= 0.56


def test_wolves_one_shot_a_fresh_player():
    """Волк — первое, что новичок видит за пределами норы: любой сносит его
    с одного попадания."""
    hero = _hero_at(1)
    for wolf in LOCATIONS_BY_KEY["wolf_forest"].monsters:
        assert base_damage(wolf.spawn(), hero) >= hero.hp_max, wolf.name


def test_locations_cover_levels_without_gaps():
    starts = [loc.min_level for loc in LOCATIONS]
    assert starts == list(range(1, 4 * len(LOCATIONS), 4)), \
        "каждая локация закрывает свои четыре уровня, без дыр и нахлёстов"


def test_locations_declare_their_level():
    levels = [loc.min_level for loc in LOCATIONS]
    assert levels[0] == 1, "первая локация обязана быть доступна новичку"
    assert levels == sorted(levels), "локации идут по возрастанию сложности"
    for loc in LOCATIONS:
        assert str(loc.min_level) in loc.button(1)


@pytest.mark.parametrize("location", LOCATIONS, ids=lambda loc: loc.key)
def test_button_warns_before_a_deadly_walk_in(location):
    """Кнопка обязана отличать «по уровню» от «тебя там съедят» — иначе
    новичок уходит в логово одним нажатием и не понимает, что случилось."""
    lo = location.min_level
    assert location.verdict(lo) == "по уровню"
    assert location.verdict(lo + 3) == "по уровню"
    assert location.verdict(lo + 4) == "по силам"
    if lo > 1:
        assert location.verdict(lo - 1) == "смертельно"
        assert "☠️" in location.button(lo - 1)


def test_fight_does_not_end_on_a_step_counter():
    """Счётчик двигают только законченные встречи: бой, оборванный на середине,
    оставлял бы игрока без добычи ровно там, где становится интересно."""
    location = LOCATIONS_BY_KEY["rat_field"]
    rng = random.Random(1)
    state = run_mod.start(location, hp=1000, rng=rng)
    hero = Fighter("Игрок", hp=1000, hp_max=1000, atk=1, defense=99)  # ковыряет долго

    rounds = 0
    while state.monster_hp > 0:
        rounds += 1
        assert state.step == 1, "живая тварь не должна отнимать встречи"
        run_mod.fight_round(state, hero, location, rng.choice(MOVES), rng)

    assert rounds > 5, "бой обязан идти дольше, чем встреч в походе"
    assert state.step == 2, "убитая тварь закрывает встречу"


def test_leaving_ends_the_run_alive():
    state = run_mod.start(LOCATIONS[0], hp=100, rng=random.Random(1))
    state.phase = run_mod.PHASE_FORK

    run_mod.leave(state)

    assert state.phase == run_mod.PHASE_DONE
    assert state.dead is False


def test_finding_keys_are_unique():
    keys = [f.key for loc in LOCATIONS for f in loc.findings]
    assert len(keys) == len(set(keys))


# --- поход ---

def test_run_state_survives_serialization():
    state = run_mod.start(LOCATIONS[0], hp=100, rng=random.Random(1))
    state.found.append("rusty_sword")
    restored = run_mod.RunState.loads(state.dumps())
    assert restored == state


def test_run_starts_with_a_fight():
    state = run_mod.start(LOCATIONS[0], hp=100, rng=random.Random(1))
    assert state.phase == run_mod.PHASE_FIGHT
    assert state.step == 1
    assert state.monster_hp > 0


def test_living_monster_blocks_the_fork():
    """Пока тварь жива, развилки нет — она забирает следующий шаг себе."""
    location = LOCATIONS_BY_KEY["wolf_forest"]
    state = run_mod.start(location, hp=100, rng=random.Random(3))
    hero = Fighter("Игрок", hp=100, hp_max=100, atk=1, defense=5)  # заведомо не убьёт

    run_mod.fight_round(state, hero, location, Move.PARRY, random.Random(3))

    assert state.monster_hp > 0
    assert state.phase == run_mod.PHASE_FIGHT


def test_defeated_monster_opens_the_fork():
    location = LOCATIONS_BY_KEY["rat_field"]
    # Зерно подобрано так, чтобы тварь пошла в укол и проиграла раунд удару:
    # при ничьей урона нет вовсе и убить её нечем даже с атакой в 999.
    state = run_mod.start(location, hp=100, rng=random.Random(2))
    hero = Fighter("Игрок", hp=100, hp_max=100, atk=999, defense=5)

    run_mod.fight_round(state, hero, location, Move.STRIKE, random.Random(2))

    assert state.monster_hp == 0
    assert state.phase == run_mod.PHASE_FORK
    assert len(state.defeated) == 1


def test_one_path_leads_to_chest_and_the_other_to_a_fight():
    location = LOCATIONS_BY_KEY["rat_field"]
    rng = random.Random(1)
    state = run_mod.start(location, hp=100, rng=rng)
    state.phase = run_mod.PHASE_FORK
    state.chest_side = "left"

    assert run_mod.take_path(state, location, "left", rng) is True

    state.phase = run_mod.PHASE_FORK
    assert run_mod.take_path(state, location, "right", rng) is False
    assert state.phase == run_mod.PHASE_FIGHT


def test_chest_can_yield_a_finding():
    location = LOCATIONS_BY_KEY["rat_field"]
    state = run_mod.start(location, hp=100, rng=random.Random(1))

    class AlwaysLucky(random.Random):
        def random(self):
            return 0.0

    prize = run_mod.open_chest(state, location, {}, AlwaysLucky(1))
    assert prize is not None
    assert state.found == [prize.key]


def test_chest_respects_the_limit():
    location = LOCATIONS_BY_KEY["rat_field"]
    state = run_mod.start(location, hp=100, rng=random.Random(1))
    maxed = {f.key: f.limit for f in location.findings}

    class AlwaysLucky(random.Random):
        def random(self):
            return 0.0

    assert run_mod.open_chest(state, location, maxed, AlwaysLucky(1)) is None


def test_death_ends_the_run():
    location = LOCATIONS_BY_KEY["wolf_forest"]
    # Зерно подобрано так, чтобы тварь выиграла раунд: иначе с ничьей урона нет.
    state = run_mod.start(location, hp=1, rng=random.Random(2))
    hero = Fighter("Игрок", hp=1, hp_max=100, atk=1, defense=0)

    run_mod.fight_round(state, hero, location, Move.PARRY, random.Random(2))

    assert state.dead is True
    assert state.phase == run_mod.PHASE_DONE


@pytest.mark.parametrize("seed", range(50))
def test_run_always_ends_within_the_encounter_limit(seed):
    """Прогоняем поход целиком случайной игрой: он обязан закончиться смертью
    или исчерпанием встреч и ни при каких раскладах не зациклиться."""
    rng = random.Random(seed)
    location = rng.choice(LOCATIONS)
    state = run_mod.start(location, hp=100, rng=rng)
    hero_atk, hero_def = 10, 5

    guard = 0
    while state.phase != run_mod.PHASE_DONE:
        guard += 1
        assert guard < 200, "поход зациклился"

        if state.phase == run_mod.PHASE_FIGHT:
            hero = Fighter("Игрок", hp=state.hp, hp_max=100,
                           atk=hero_atk, defense=hero_def)
            run_mod.fight_round(state, hero, location, rng.choice(MOVES), rng)
        elif state.phase == run_mod.PHASE_FORK:
            if run_mod.take_path(state, location, rng.choice(["left", "right"]), rng):
                run_mod.open_chest(state, location, {}, rng)

    assert state.dead or state.step == run_mod.MAX_STEPS + 1


# --- глубина ниже дна ---

def test_dungeon_has_no_bottom():
    """После последнего этажа поход не обрывается: дальше та же тварь, но злее.
    Иначе прокачанному игроку некуда девать силу — он упирается в потолок,
    который сам давно перерос."""
    den = LOCATIONS_BY_KEY["rat_field"]
    bottom = len(den.monsters)

    deeper = den.monster_spec(bottom + 1)
    floor = den.monster_spec(bottom)

    assert deeper.hp > floor.hp and deeper.atk > floor.atk
    assert deeper.weights == floor.weights, "повадки обязаны остаться прежними"
    assert den.exp_for(bottom + 1) > den.exp_for(bottom), "риск должен окупаться"


def test_deeper_floors_keep_growing():
    for location in LOCATIONS:
        bottom = len(location.monsters)
        specs = [location.monster_spec(bottom + n) for n in range(1, 6)]
        assert [m.hp for m in specs] == sorted(m.hp for m in specs)
        prices = [location.exp_for(bottom + n) for n in range(1, 6)]
        assert prices == sorted(prices)


def test_run_tracks_the_floor_it_is_on():
    """Тварь ищется по этажу, а не по имени: ниже дна имена повторяются."""
    location = LOCATIONS_BY_KEY["rat_field"]
    rng = random.Random(5)
    state = run_mod.start(location, hp=1000, rng=rng)
    hero = Fighter("Игрок", hp=1000, hp_max=1000, atk=999, defense=99)

    seen = []
    guard = 0
    while state.phase != run_mod.PHASE_DONE and guard < 40:
        guard += 1
        if state.phase == run_mod.PHASE_FIGHT:
            seen.append(state.floor)
            run_mod.fight_round(state, hero, location, rng.choice(MOVES), rng)
        elif not run_mod.take_path(state, location, rng.choice(["left", "right"]), rng):
            pass
        else:
            run_mod.open_chest(state, location, {}, rng)

    assert max(seen) > len(location.monsters), "поход не ушёл ниже дна"
    assert state.exp > sum(location.exp_for(d) for d in range(1, 4)), \
        "за глубину обязан капать опыт сверх обычного дна"
