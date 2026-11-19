"""Классы: статы, навыки, пассивки, выбор класса и баланс между классами."""

import itertools
import os
import random
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("CLAN_CHAT_ID", "-1001234567890")

from bot import config, db  # noqa: E402
from bot.game import run as run_mod  # noqa: E402
from bot.game.classes import CLASSES, stats_for  # noqa: E402
from bot.game.combat import (  # noqa: E402
    BEATS, FIREBALL_POWER, HEAL_SHARE, MAGE_DRAW, ROGUE_THRUST, SKILL_FIREBALL,
    SKILL_HEAL, SKILL_SCOUT, SKILL_SHIELD, WARRIOR_GUARD, Fighter, Move, Side,
    resolve_round,
)
from bot.game.progression import stats_at  # noqa: E402
from bot.game.world import LOCATIONS, LOCATIONS_BY_KEY, Finding  # noqa: E402

COUNTER = {his: mine for mine, his in BEATS.items()}  # ход, который бьёт данный


class NoSpread(random.Random):
    """Без разброса урона ±10%, чтобы сравнивать точные числа."""

    def uniform(self, a, b):
        return 1.0


def fighter(hp=100, atk=10, defense=0) -> Fighter:
    return Fighter("x", hp, hp, atk, defense)


# --- статы ---

def test_four_classes_each_with_its_own_skill():
    assert set(CLASSES) == {"warrior", "rogue", "mage", "healer"}
    assert {c.skill for c in CLASSES.values()} == {
        SKILL_SHIELD, SKILL_SCOUT, SKILL_FIREBALL, SKILL_HEAL}


def test_no_class_means_bare_stats():
    assert stats_for(None, 10, 5, 0) == (10, 5, 0)


def test_class_rounds_half_up():
    # 5 × 0.9 = 4.5: банковское округление дало бы 4.
    warrior = CLASSES["warrior"]
    assert warrior.apply(10, 5, 0)[1] == round(5 * warrior.atk + 0.001)


def test_stats_never_drop_to_zero():
    for c in CLASSES.values():
        hp, atk, _ = c.apply(1, 1, 0)
        assert hp >= 1 and atk >= 1


# --- навыки и пассивки в раунде ---

@pytest.mark.parametrize("their", list(Move))
def test_shield_never_loses(their):
    me, foe = fighter(), fighter()
    result = resolve_round(me, foe, None, their, NoSpread(),
                           side_a=Side("warrior", SKILL_SHIELD))
    assert result.damage_to_a == 0


def test_fireball_ignores_defense():
    me, foe = fighter(atk=10), fighter(defense=50)
    result = resolve_round(me, foe, None, Move.PARRY, NoSpread(),
                           side_a=Side("mage", SKILL_FIREBALL))
    assert result.damage_to_b == round(10 * FIREBALL_POWER)
    assert result.damage_to_a == 0, "парирование мага без приёма не ранит"


@pytest.mark.parametrize("their", [Move.STRIKE, Move.THRUST])
def test_fireball_leaves_mage_open(their):
    result = resolve_round(fighter(), fighter(), None, their, NoSpread(),
                           side_a=Side("mage", SKILL_FIREBALL))
    assert result.damage_to_a > 0


def test_shield_blocks_fireball():
    result = resolve_round(fighter(), fighter(), None, None, NoSpread(),
                           side_a=Side("mage", SKILL_FIREBALL),
                           side_b=Side("warrior", SKILL_SHIELD))
    assert result.damage_to_b == 0


def test_heal_restores_and_halves_the_blow():
    me = Fighter("x", hp=20, hp_max=100, atk=10, defense=0)
    result = resolve_round(me, fighter(atk=10), None, Move.STRIKE, NoSpread(),
                           side_a=Side("healer", SKILL_HEAL))
    assert result.healed_a == round(100 * HEAL_SHARE)
    assert result.damage_to_a == 5
    assert result.damage_to_b == 0


def test_heal_does_not_overflow():
    me = Fighter("x", hp=95, hp_max=100, atk=10, defense=0)
    resolve_round(me, fighter(), None, Move.PARRY, NoSpread(),
                  side_a=Side("healer", SKILL_HEAL))
    assert me.hp == 100


def test_rogue_thrust_hits_harder():
    plain = resolve_round(fighter(), fighter(), Move.THRUST, Move.PARRY, NoSpread())
    rogue = resolve_round(fighter(), fighter(), Move.THRUST, Move.PARRY, NoSpread(),
                          side_a=Side("rogue"))
    assert rogue.damage_to_b == round(plain.damage_to_b * ROGUE_THRUST)


def test_mage_burns_on_a_draw():
    result = resolve_round(fighter(atk=10), fighter(), Move.STRIKE, Move.STRIKE,
                           NoSpread(), side_a=Side("mage"))
    assert result.damage_to_b == round(10 * MAGE_DRAW)
    assert result.damage_to_a == 0


def test_warrior_takes_less_on_a_lost_round():
    plain = resolve_round(fighter(), fighter(), Move.STRIKE, Move.PARRY, NoSpread())
    warrior = resolve_round(fighter(), fighter(), Move.STRIKE, Move.PARRY, NoSpread(),
                            side_a=Side("warrior"))
    assert warrior.damage_to_a == round(plain.damage_to_a * WARRIOR_GUARD)


def test_plain_round_is_unchanged_without_classes():
    """Без классов бой остаётся чистым КНБ — старые тесты на это и опираются."""
    result = resolve_round(fighter(), fighter(), Move.PARRY, Move.STRIKE, NoSpread())
    assert result.damage_to_a == 0 and result.damage_to_b > 0
    assert result.label_a and result.label_b


# --- навыки в походе ---

def _run(cls: str, location_key: str = "rat_field") -> tuple[run_mod.RunState, object]:
    location = LOCATIONS_BY_KEY[location_key]
    return run_mod.start(location, hp=100, rng=random.Random(1), cls=cls), location


def test_run_gets_class_charges():
    for key, c in CLASSES.items():
        state, _ = _run(key)
        assert state.charges == c.charges


def test_scout_shows_the_move_that_really_comes():
    rng = random.Random(5)
    for _ in range(50):
        state, location = _run("rogue")
        seen = run_mod.scout(state, location, rng)
        assert state.charges == CLASSES["rogue"].charges - 1
        me = fighter(hp=100, atk=1)
        result = run_mod.fight_round(state, me, location, COUNTER[seen], rng)
        assert result.move_b == seen
        assert result.damage_to_a == 0, "ответ на подсмотренный ход не проигрывает"


def test_skill_spends_a_charge_and_stops_at_zero():
    state, location = _run("warrior")
    rng = random.Random(2)
    for _ in range(CLASSES["warrior"].charges):
        assert run_mod.can_use_skill(state)
        run_mod.fight_round(state, fighter(hp=100, atk=1), location, None, rng, use_skill=True)
    assert state.charges == 0
    assert not run_mod.can_use_skill(state)


def test_no_class_no_skill():
    location = LOCATIONS_BY_KEY["rat_field"]
    state = run_mod.start(location, hp=10, rng=random.Random(1))
    assert not run_mod.can_use_skill(state)


def test_healer_patches_up_after_a_kill():
    state, location = _run("healer")
    state.hp = 10
    state.monster_move = Move.STRIKE.value  # ход известен — отвечаем наверняка
    run_mod.fight_round(state, Fighter("x", state.hp, 100, 1000, 0), location,
                        COUNTER[Move.STRIKE], random.Random(3))
    assert state.phase == run_mod.PHASE_FORK
    assert state.hp == 10 + round(100 * run_mod.HEALER_REGEN)


def test_others_do_not_heal_between_floors():
    state, location = _run("warrior")
    state.hp = 10
    rng = random.Random(3)
    while state.phase == run_mod.PHASE_FIGHT:
        run_mod.fight_round(state, Fighter("x", state.hp, 100, 1000, 1000), location,
                            Move.STRIKE, rng)
    assert state.hp <= 10


# --- находки ---

def test_every_finding_can_drop_not_just_the_first(monkeypatch):
    """Раньше сундук брал первую выпавшую находку, и первая падала чаще."""
    location = LOCATIONS[0]
    monkeypatch.setattr(run_mod, "drop_chance", lambda found, finding: 1.0)

    got = set()
    rng = random.Random(1)
    for _ in range(200):
        state, _ = _run("warrior")
        state.phase = run_mod.PHASE_FORK
        prize = run_mod.open_chest(state, location, {}, rng)
        got.add(prize.key)
    assert got == {f.key for f in location.findings}


# --- база ---

@pytest.fixture
async def clean_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    await db.init()


async def test_class_is_stored(clean_db):
    await db.upsert_player(1, "Дима", "dima")
    assert (await db.get_player(1)).cls is None
    await db.set_class(1, "mage")
    player = await db.get_player(1)
    assert player.cls == "mage" and player.cls_changed


async def test_old_database_gets_new_columns_and_one_reset(tmp_path, monkeypatch):
    """База со старой RPG: колонки класса появляются, прогресс обнуляется один раз."""
    import aiosqlite

    path = str(tmp_path / "old.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    async with aiosqlite.connect(path) as conn:
        await conn.executescript(db.SCHEMA.replace("CREATE TABLE IF NOT EXISTS meta", "CREATE TABLE IF NOT EXISTS meta_unused"))
        await conn.execute(
            "INSERT INTO players (tg_id, username, name, hp_max, atk, defense, level, exp, "
            "energy, energy_date, created_at) VALUES (1, 'a', 'A', 40, 15, 6, 9, 30, 2, 'x', 'x')")
        await conn.execute("INSERT INTO findings VALUES (1, 'rat_tooth', 2)")
        await conn.commit()

    await db.init()
    player = await db.get_player(1)
    assert (player.level, player.exp, player.hp_max, player.atk, player.defense) == (
        1, 0, config.START_HP, config.START_ATK, config.START_DEF)
    assert await db.get_findings(1) == {}

    # Второй запуск уже ничего не сбрасывает.
    await db.add_exp(1, 100)
    await db.init()
    assert (await db.get_player(1)).level > 1


async def test_change_cooldown(clean_db):
    from bot.handlers.rpg import change_blocked_for

    await db.upsert_player(1, "Дима", "dima")
    await db.set_class(1, "mage")
    player = await db.get_player(1)
    now = datetime.now(config.TZ)
    assert change_blocked_for(player, now) is not None
    assert change_blocked_for(player, now + timedelta(days=8)) is None


# --- дуэли ---

def test_one_duel_per_player_and_stale_duels_vanish(monkeypatch):
    from bot.handlers import duel as duel_mod

    duel_mod.DUELS.clear()
    d = duel_mod.Duel(challenger=1, rival=2, fighters={1: fighter(), 2: fighter()})
    duel_mod.DUELS[10] = d
    # Непринятый вызов занимает только вызвавшего: жертву им не запрёшь.
    assert duel_mod.busy(1) and not duel_mod.busy(2) and not duel_mod.busy(3)

    d.accepted = True
    assert duel_mod.busy(1) and duel_mod.busy(2)

    d.created -= duel_mod.FIGHT_TTL + 1
    assert not duel_mod.busy(1)
    assert 10 not in duel_mod.DUELS


# --- тексты ---

def test_guide_fits_one_message():
    from bot.handlers.rpg import GUIDE, _class_text
    assert len(GUIDE) < 4096 and len(_class_text()) < 4096


def test_skill_alerts_fit_telegram_limit():
    from bot.handlers import raid as raid_mod

    long_name = "Ж" * 64
    player = db.Player(tg_id=1, username=None, name=long_name, hp_max=999, atk=99,
                       defense=99, level=99, exp=0, energy=3, energy_date="x", cls="healer")
    location = LOCATIONS[-1]
    state = run_mod.start(location, hp=999, rng=random.Random(1), cls="healer")
    result = resolve_round(Fighter(long_name, 500, 999, 99, 99), location.monster_spec(1).spawn(),
                           None, Move.STRIKE, random.Random(1), side_a=Side("healer", SKILL_HEAL))
    assert len(raid_mod.round_alert(result, state, player, location)) <= raid_mod.ALERT_LIMIT


# --- баланс ---

def _blind_run(level: int, location, cls: str, rng: random.Random) -> int:
    """Игрок без знания повадок: ход наугад, навык по простому правилу,
    глубже — пока здоровья не меньше половины. Так считается весь баланс."""
    hp, atk, defense = CLASSES[cls].apply(*stats_at(level))
    state = run_mod.start(location, hp, rng, cls)
    first = True
    while state.phase != run_mod.PHASE_DONE:
        if state.phase == run_mod.PHASE_FIGHT:
            me = Fighter("x", state.hp, hp, atk, defense)
            move, skill = rng.choice(list(Move)), False
            if run_mod.can_use_skill(state):
                if cls == "rogue" and first:
                    move = COUNTER[run_mod.scout(state, location, rng)]
                elif (cls == "mage" and first) or (
                        cls in ("warrior", "healer") and state.hp < hp * 0.45):
                    skill = True
            run_mod.fight_round(state, me, location, None if skill else move, rng,
                                use_skill=skill)
            first = False
        else:
            if state.hp < hp * 0.5:
                run_mod.leave(state)
                continue
            if run_mod.take_path(state, location, rng.choice(["left", "right"]), rng):
                run_mod.open_chest(state, location, {}, rng)
            first = True
    return state.exp


def test_classes_level_up_at_about_the_same_pace():
    """Опыт за поход у каждого класса в среднем по всем подземельям — в пределах
    12% от среднего по классам. Отдельная точка гуляет сильнее: на первых
    уровнях статы крошечные, и одно очко атаки сразу меняет число нужных
    попаданий (разбойник на 1-м уровне в норе — 1.3 от среднего)."""
    totals = {k: [] for k in CLASSES}
    for location in LOCATIONS:
        for level in (location.min_level, location.min_level + 3):
            exp = {}
            for k in CLASSES:
                rng = random.Random(level * 31 + len(k))
                exp[k] = sum(_blind_run(level, location, k, rng) for _ in range(400)) / 400
            mean = sum(exp.values()) / len(exp)
            for k, v in exp.items():
                totals[k].append(v / mean)
    for k, ratios in totals.items():
        average = sum(ratios) / len(ratios)
        assert 0.88 <= average <= 1.12, (k, average)
        assert all(0.7 <= r <= 1.35 for r in ratios), (k, ratios)


def _blind_duel(level: int, ca: str, cb: str, rng: random.Random) -> float:
    """Дуэль двух игроков, жмущих наугад; навык — по простому правилу.
    Разведка видит ход, только если соперник нажал первым (половина случаев)."""
    def make(cls):
        hp, atk, defense = CLASSES[cls].apply(*stats_at(level), duel=True)
        return Fighter(cls, hp, hp, atk, defense)

    a, b = make(ca), make(cb)
    charge = {0: 1, 1: 1}
    for round_no in range(5):
        moves, skills, scouts = [], [], []
        for i, (me, cls) in enumerate(((a, ca), (b, cb))):
            skill = CLASSES[cls].skill
            use = charge[i] and (
                (skill in (SKILL_FIREBALL, SKILL_SCOUT) and round_no == 0)
                or (skill in (SKILL_SHIELD, SKILL_HEAL) and me.hp < me.hp_max * 0.5))
            moves.append(rng.choice(list(Move)))
            skills.append(skill if use and skill != SKILL_SCOUT else None)
            scouts.append(bool(use) and skill == SKILL_SCOUT)
            if skills[-1]:
                charge[i] = 0
        for i in (0, 1):
            if scouts[i] and not skills[1 - i] and rng.random() < 0.5:
                moves[i] = COUNTER[moves[1 - i]]
        resolve_round(a, b, None if skills[0] else moves[0], None if skills[1] else moves[1],
                      rng, Side(ca, skills[0]), Side(cb, skills[1]))
        if not a.alive or not b.alive:
            break
    share_a, share_b = a.hp / a.hp_max, b.hp / b.hp_max
    return 1.0 if share_a > share_b else 0.0 if share_a < share_b else 0.5


def test_classes_are_even_in_duels():
    """При равном уровне любая пара классов выигрывает друг у друга от 35 до 65%.
    Классы нетранзитивны (как камень-ножницы-бумага), точнее подбором дуэльных
    множителей свести не удалось; живая игра — угадывание повадок соперника —
    весит больше этого разброса."""
    for level in (1, 10, 20, 30):
        rng = random.Random(level)
        for ca, cb in itertools.combinations(CLASSES, 2):
            share = sum(_blind_duel(level, ca, cb, rng) for _ in range(2000)) / 2000
            assert 0.35 <= share <= 0.65, (level, ca, cb, share)


async def test_double_tap_does_not_pay_twice(clean_db):
    """aiogram обрабатывает нажатия параллельно: без блокировки два тапа по
    «Уйти с добычей» начисляли опыт дважды."""
    import asyncio
    from types import SimpleNamespace

    from bot.handlers import raid as raid_mod

    await db.upsert_player(1, "Дима", "dima")
    await db.set_class(1, "warrior")
    state, _ = _run("warrior")
    state.phase, state.exp = run_mod.PHASE_FORK, 7
    await db.save_run(1, state.dumps())

    class Msg:
        async def edit_text(self, *a, **k):
            await asyncio.sleep(0)

        async def edit_reply_markup(self, *a, **k):
            pass

    async def answer(*a, **k):
        await asyncio.sleep(0)

    def tap():
        return SimpleNamespace(from_user=SimpleNamespace(id=1), data="raid:path:leave:1",
                               message=Msg(), answer=answer)

    await asyncio.gather(raid_mod.choose_path(tap()), raid_mod.choose_path(tap()))
    from bot.game.progression import exp_to_next

    player = await db.get_player(1)
    total = player.exp + sum(exp_to_next(lvl) for lvl in range(1, player.level))
    assert total == 7
