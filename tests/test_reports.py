import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("CLAN_CHAT_ID", "-1001234567890")

from bot import db  # noqa: E402
from bot.game import run as run_mod  # noqa: E402
from bot.game.combat import LABELS, Fighter, Move, resolve_round  # noqa: E402
from bot.game.world import LOCATIONS, LOCATIONS_BY_KEY  # noqa: E402
from bot.handlers import duel as duel_mod  # noqa: E402
from bot.handlers import raid as raid_mod  # noqa: E402

LOCATION = LOCATIONS_BY_KEY["wolf_forest"]
SECRETS = ("Удар", "Парирование", "Укол", "❤️", "HP")


def make_state(**kwargs) -> run_mod.RunState:
    state = run_mod.start(LOCATION, hp=50, rng=random.Random(1))
    for key, value in kwargs.items():
        setattr(state, key, value)
    return state


def make_player(**kwargs) -> db.Player:
    fields = dict(tg_id=1, username="d", name="Дмитрий", hp_max=100, atk=10,
                  defense=5, level=1, exp=0, energy=2, energy_date="2026-01-01")
    return db.Player(**(fields | kwargs))


# --- сводка о походе ---

def test_report_lists_kills_and_loot():
    state = make_state(defeated=["Молодой волк"], found=["alpha_fang"],
                       exp=LOCATION.exp_for(1))
    exp = raid_mod.run_exp(state, LOCATION)
    text = raid_mod.public_report(state, "Дмитрий", LOCATION, level=1,
                                  levels_gained=0, exp=exp)

    assert "Дмитрий вернулся" in text
    assert "Молодой волк" in text and "Клык вожака" in text
    assert f"Опыт +{exp}" in text and exp == LOCATION.min_level


def test_report_marks_death():
    state = make_state(dead=True, defeated=["Молодой волк"])
    assert "не вернулся" in raid_mod.public_report(state, "Д", LOCATION, 1, 0)


def test_report_survives_empty_run():
    assert "Ни добычи, ни подвигов" in raid_mod.public_report(make_state(), "Д", LOCATION, 1, 0)


def test_report_announces_level_up():
    assert "уровень 4" in raid_mod.public_report(make_state(), "Д", LOCATION, 4, 1)


def test_report_hides_private_details():
    """В клан-чат не должно утекать то, что видит только игрок."""
    state = make_state(hp=7, defeated=["Вожак стаи"], found=["wolf_pelt"])
    text = raid_mod.public_report(state, "Дмитрий", LOCATION, level=2, levels_gained=0)

    assert "7" not in text
    assert not any(word in text for word in SECRETS)


# --- статус похода в чате ---

def test_status_message_is_fully_anonymous():
    """Сообщение с кнопками висит в общем чате весь поход — спрятать сам факт
    его существования Telegram не даёт. Но оно не должно называть ни игрока,
    ни локацию, ни шаг: по нему нельзя понять, кто и куда пошёл."""
    text = raid_mod.ANONYMOUS_STATUS

    assert "Дмитрий" not in text
    for location in LOCATIONS:
        assert location.title not in text
    assert not any(ch.isdigit() for ch in text)
    assert not any(word in text for word in SECRETS)


# --- всплывашки похода ---

def test_alerts_fit_telegram_limit():
    """Всплывающее окно Telegram обрезается на 200 символах — весь бой
    обязан помещаться, иначе игрок не увидит концовку фразы."""
    player = make_player(name="Максимилиан Александрович")
    rng = random.Random(0)

    for seed in range(30):
        state = run_mod.start(LOCATION, hp=player.hp_max, rng=random.Random(seed))
        hero = Fighter(player.name, state.hp, player.hp_max, player.atk, player.defense)
        monster = run_mod.monster_fighter(state, LOCATION)

        for my in Move:
            for theirs in Move:
                result = resolve_round(hero, monster, my, theirs, rng)
                alert = raid_mod.round_alert(result, state, player, LOCATION)
                assert len(alert) <= raid_mod.ALERT_LIMIT


def test_health_line_shows_both_sides_in_fight():
    player = make_player()
    state = make_state(hp=42)
    line = raid_mod.health_line(state, player, LOCATION)

    assert "42/100" in line
    assert state.monster_name in line


def test_health_line_drops_monster_when_it_is_dead():
    player = make_player()
    state = make_state(hp=42, monster_hp=0, phase=run_mod.PHASE_FORK)
    assert state.monster_name not in raid_mod.health_line(state, player, LOCATION)


# --- дуэль ---

def duel_with(hp_a: int = 100, hp_b: int = 100) -> duel_mod.Duel:
    return duel_mod.Duel(
        challenger=1, rival=2,
        fighters={
            1: Fighter("Дмитрий", hp=hp_a, hp_max=100, atk=10, defense=5),
            2: Fighter("Андрей", hp=hp_b, hp_max=100, atk=10, defense=5),
        },
    )


def test_winner_is_decided_by_share_of_health():
    assert duel_mod._winner(duel_with(60, 40)) == 1
    assert duel_mod._winner(duel_with(40, 60)) == 2
    assert duel_mod._winner(duel_with(50, 50)) is None


def test_bigger_health_pool_does_not_win_by_itself():
    duel = duel_with(60, 40)
    duel.fighters[1].hp_max = 200  # 30% против 40%
    assert duel_mod._winner(duel) == 2


def test_public_result_names_the_winner():
    duel = duel_with(60, 40)
    text = duel_mod.public_result(duel, duel_mod._winner(duel))
    assert "Победил Дмитрий" in text and "Дмитрий против Андрей" in text


def test_public_result_reports_a_draw():
    assert "Ничья" in duel_mod.public_result(duel_with(50, 50), None)


def test_duel_public_texts_hide_the_moves():
    duel = duel_with(60, 40)
    duel.last = resolve_round(duel.fighters[1], duel.fighters[2],
                              Move.THRUST, Move.PARRY, random.Random(1))
    for text in (duel_mod.public_result(duel, 1), duel_mod.public_status(duel)):
        assert not any(word in text for word in ("Удар", "Парирование", "Укол"))


def test_duel_status_hides_round_and_health():
    """Пока бой идёт, снаружи видно только что он идёт."""
    duel = duel_with(60, 40)
    duel.round = 4
    text = duel_mod.public_status(duel)

    assert "Дмитрий" in text and "Андрей" in text
    assert "4" not in text and "раунд" not in text.lower()
    assert "60" not in text and "40" not in text


def test_recap_is_mirrored_for_each_side():
    """Каждый видит свой ход первым и свой урон как «тебе»."""
    duel = duel_with()
    duel.last = resolve_round(duel.fighters[1], duel.fighters[2],
                              Move.THRUST, Move.PARRY, random.Random(1))

    mine = duel_mod.round_recap(duel, 1)
    theirs = duel_mod.round_recap(duel, 2)

    assert mine.startswith(LABELS[Move.THRUST]) and theirs.startswith(LABELS[Move.PARRY])
    assert f"Тебе −{duel.last.damage_to_a}" in mine
    assert f"Тебе −{duel.last.damage_to_b}" in theirs


def test_recap_is_empty_before_the_first_round():
    assert duel_mod.round_recap(duel_with(), 1) == ""


@pytest.mark.parametrize("seed", range(20))
def test_duel_alerts_fit_telegram_limit(seed):
    duel = duel_mod.Duel(
        challenger=1, rival=2,
        fighters={
            1: Fighter("Максимилиан Александрович", 100, 100, 10, 5),
            2: Fighter("Константин Иннокентьевич", 100, 100, 10, 5),
        },
    )
    rng = random.Random(seed)
    duel.last = resolve_round(duel.fighters[1], duel.fighters[2],
                              rng.choice(list(Move)), rng.choice(list(Move)), rng)

    for viewer in (1, 2):
        assert len(duel_mod.result_alert(duel, viewer)) <= duel_mod.ALERT_LIMIT
        for move in Move:
            assert len(duel_mod.waiting_alert(duel, viewer, move)) <= duel_mod.ALERT_LIMIT


def test_finding_names_resolve():
    keys = [f.key for loc in LOCATIONS for f in loc.findings]
    assert len(raid_mod.found_names(keys)) == len(keys)
