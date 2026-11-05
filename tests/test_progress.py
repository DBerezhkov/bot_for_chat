import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("CLAN_CHAT_ID", "-1001234567890")

from bot import config, db  # noqa: E402
from bot.game.progression import exp_to_next, stats_at  # noqa: E402
from bot.game.world import LOCATIONS_BY_KEY  # noqa: E402


@pytest.fixture
async def hero(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    await db.init()
    await db.upsert_player(1, "Дмитрий", "dmitry")
    return 1


# --- опыт и уровни ---

async def test_exp_below_threshold_keeps_level(hero):
    assert await db.add_exp(hero, exp_to_next(1) - 1) == 0
    player = await db.get_player(hero)
    assert (player.level, player.exp) == (1, exp_to_next(1) - 1)
    assert (player.hp_max, player.atk, player.defense) == (
        config.START_HP, config.START_ATK, config.START_DEF)


async def test_level_up_grants_stats(hero):
    assert await db.add_exp(hero, exp_to_next(1)) == 1
    player = await db.get_player(hero)
    assert (player.level, player.exp) == (2, 0)
    assert (player.hp_max, player.atk, player.defense) == (13, 6, 1)


async def test_defense_grows_every_other_level(hero):
    """Защита делит входящий урон и множится на запас HP — расти как остальное
    она не должна, иначе перс к десятому уровню становится неубиваемым."""
    seen = []
    for _ in range(6):
        player = await db.get_player(hero)
        seen.append((player.level, player.defense))
        await db.add_exp(hero, exp_to_next(player.level))

    assert seen == [(1, 0), (2, 1), (3, 1), (4, 2), (5, 2), (6, 3)]


async def test_multiple_levels_in_one_go(hero):
    """Опыт за два порога разом обязан дать оба уровня, а не один."""
    assert await db.add_exp(hero, exp_to_next(1) + exp_to_next(2)) == 2
    player = await db.get_player(hero)
    assert (player.level, player.exp) == (3, 0)
    assert (player.hp_max, player.atk, player.defense) == (16, 7, 1)


async def test_db_progression_matches_the_formula(hero):
    """Баланс локаций считается по stats_at, а статы игроку начисляет база.
    Разъедутся — числа в мире перестанут соответствовать живому персонажу."""
    for level in range(1, 9):
        player = await db.get_player(hero)
        assert player.level == level
        assert (player.hp_max, player.atk, player.defense) == stats_at(level)
        await db.add_exp(hero, exp_to_next(level))


async def test_thresholds_grow_with_level(hero):
    """Каждый следующий уровень дороже предыдущего — иначе кривая прокачки
    выпрямляется и двадцатый уровень берётся так же быстро, как второй."""
    assert [exp_to_next(lv) for lv in range(1, 6)] == sorted(
        exp_to_next(lv) for lv in range(1, 6))

    await db.add_exp(hero, exp_to_next(1))
    await db.add_exp(hero, exp_to_next(2) - 1)
    assert (await db.get_player(hero)).level == 2, "порога не хватило на единицу"
    await db.add_exp(hero, 1)
    assert (await db.get_player(hero)).level == 3


async def test_exp_remainder_carries_over(hero):
    await db.add_exp(hero, exp_to_next(1) + 2)
    player = await db.get_player(hero)
    assert (player.level, player.exp) == (2, 2)


# --- находки ---

async def test_sword_raises_attack(hero):
    sword = LOCATIONS_BY_KEY["rat_field"].findings[0]
    await db.add_finding(hero, sword.key, sword.stat, sword.bonus, sword.hp_bonus)

    player = await db.get_player(hero)
    assert player.atk == config.START_ATK + 1
    assert player.hp_max == config.START_HP
    assert (await db.get_findings(hero))[sword.key] == 1


async def test_armour_raises_defense_and_hp(hero):
    vest = LOCATIONS_BY_KEY["rat_field"].findings[1]
    await db.add_finding(hero, vest.key, vest.stat, vest.bonus, vest.hp_bonus)

    player = await db.get_player(hero)
    assert (player.defense, player.hp_max) == (1, 13)


async def test_repeat_findings_stack(hero):
    sword = LOCATIONS_BY_KEY["rat_field"].findings[0]
    for _ in range(3):
        await db.add_finding(hero, sword.key, sword.stat, sword.bonus, sword.hp_bonus)

    assert (await db.get_player(hero)).atk == config.START_ATK + 3
    assert (await db.get_findings(hero))[sword.key] == 3


async def test_unknown_stat_is_rejected(hero):
    """Имя стата уходит прямо в SQL — чужое значение туда попасть не должно."""
    with pytest.raises(ValueError):
        await db.add_finding(hero, "hack", "level", 100, 0)


# --- дуэли ---

async def test_duel_counter(hero):
    assert await db.duels_today(hero) == 0
    for expected in (1, 2, 3):
        await db.mark_duel(hero)
        assert await db.duels_today(hero) == expected


# --- состояние похода ---

async def test_run_is_saved_and_dropped(hero):
    assert await db.load_run(hero) is None
    await db.save_run(hero, '{"location": "rat_field"}')
    assert await db.load_run(hero) == '{"location": "rat_field"}'

    await db.save_run(hero, '{"location": "wolf_forest"}')
    assert await db.load_run(hero) == '{"location": "wolf_forest"}'

    await db.drop_run(hero)
    assert await db.load_run(hero) is None


# --- сквозной поход ---

async def test_full_run_applies_everything(hero):
    """Проходим поход целиком тем же путём, что и обработчик кнопок:
    энергия списывается, находка сразу меняет статы, в конце капает опыт."""
    import random

    from bot.game import run as run_mod
    from bot.game.combat import Fighter, Move

    location = LOCATIONS_BY_KEY["rat_field"]
    player = await db.get_player(hero)

    assert await db.spend_energy(hero) is True
    assert (await db.get_player(hero)).energy == config.MAX_ENERGY - 1

    class AlwaysLucky(random.Random):
        def random(self):
            return 0.0

    rng = AlwaysLucky(1)
    state = run_mod.start(location, hp=player.hp_max, rng=rng)
    await db.save_run(hero, state.dumps())

    guard = 0
    while state.phase != run_mod.PHASE_DONE:
        guard += 1
        assert guard < 50

        state = run_mod.RunState.loads(await db.load_run(hero))
        player = await db.get_player(hero)

        if state.phase == run_mod.PHASE_FIGHT:
            fighter = Fighter(player.name, state.hp, player.hp_max,
                              player.atk, player.defense)
            # Удача зафиксирована: тварь всегда идёт в удар, парирование его бьёт.
            run_mod.fight_round(state, fighter, location, Move.PARRY, rng)
        else:
            if run_mod.take_path(state, location, state.chest_side, rng):
                prize = run_mod.open_chest(state, location,
                                           await db.get_findings(hero), rng)
                if prize:
                    await db.add_finding(hero, prize.key, prize.stat,
                                         prize.bonus, prize.hp_bonus)
        await db.save_run(hero, state.dumps())

    await db.drop_run(hero)
    await db.add_exp(hero, 1)

    assert await db.load_run(hero) is None
    assert state.found, "с гарантированной удачей сундук обязан что-то отдать"

    final = await db.get_player(hero)
    assert final.exp == 1
    assert (final.atk > config.START_ATK or final.defense > config.START_DEF), \
        "находка должна была поднять стат"


# --- опыт за поход ---

def test_exp_grows_with_depth_and_location():
    """Цена твари — её глубина. Иначе выгодно вечно фармить первый этаж норы:
    побед там втрое больше, а сложности никакой."""
    from bot.game.world import LOCATIONS

    for location in LOCATIONS:
        prices = [location.exp_for(d) for d in range(1, len(location.monsters) + 1)]
        assert prices == sorted(prices), f"{location.title}: {prices}"
        assert prices[0] == location.min_level

    first, last = LOCATIONS[0], LOCATIONS[-1]
    assert last.exp_for(1) > first.exp_for(len(first.monsters)), \
        "первый этаж старшей локации обязан стоить дороже дна младшей"


def test_full_easy_run_is_worth_less_than_one_hard_floor():
    """Прошёл нору целиком на 20 уровне — получил меньше, чем за один этаж
    логова. Ради этого опыт и считается по глубине, а не по числу побед."""
    from bot.game.world import LOCATIONS_BY_KEY

    den = LOCATIONS_BY_KEY["rat_field"]
    lair = LOCATIONS_BY_KEY["lair"]

    whole_den = sum(den.exp_for(d) for d in range(1, len(den.monsters) + 1))
    assert whole_den < lair.exp_for(1)


async def test_duel_exp_follows_the_rival_level(hero, monkeypatch):
    """Опыт за дуэль — уровень соперника: иначе двое новичков накручивают друг
    другу уровни быстрее, чем кто-либо ходит в подземелья."""
    from bot.game.combat import Fighter
    from bot.handlers import duel as duel_mod

    await db.upsert_player(2, "Андрей", None)
    await db.add_exp(2, sum(exp_to_next(lv) for lv in range(1, 6)))
    assert (await db.get_player(2)).level == 6

    duel = duel_mod.Duel(
        challenger=hero, rival=2,
        fighters={hero: Fighter("Дмитрий", 10, 10, 5, 0),
                  2: Fighter("Андрей", 25, 25, 10, 3)},
    )
    await duel_mod._award(duel, winner=hero)

    winner = await db.get_player(hero)
    assert (winner.level, winner.exp) == (2, 6 - exp_to_next(1)), \
        "победа над шестым уровнем стоит 6 опыта"
    assert (await db.get_player(2)).exp == 0, "поражение от первого уровня — ноль"
