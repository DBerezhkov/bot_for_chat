import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", "test:token")

from bot import config, db  # noqa: E402
from bot.content.loader import load  # noqa: E402
from bot.handlers import dice, fun, quiz, roulette  # noqa: E402


@pytest.fixture
async def clean_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    await db.init()


# --- Слоты -----------------------------------------------------------------

@pytest.mark.parametrize("value, reels", [
    (1, ("BAR",) * 3), (22, ("🍇",) * 3), (43, ("🍋",) * 3), (64, ("7️⃣",) * 3),
])
def test_slot_triples_match_telegram_values(value, reels):
    assert dice.slot_reels(value) == reels


def test_exactly_four_slot_jackpots():
    jackpots = [v for v in range(1, 65) if dice.slot_verdict(v)[1]]
    assert jackpots == [1, 22, 43, 64]
    assert dice.slot_outcome(64) == "jackpot"


def test_slot_outcomes_distribution():
    from collections import Counter
    counts = Counter(dice.slot_outcome(v) for v in range(1, 65))
    assert counts == {"jackpot": 1, "triple": 3, "two_sevens": 9,
                      "pair": counts["pair"], "miss": counts["miss"]}
    assert counts["pair"] + counts["miss"] == 51


def test_all_slot_combinations_distinct():
    assert len({dice.slot_reels(v) for v in range(1, 65)}) == 64


# --- Рулетка ---------------------------------------------------------------

@pytest.mark.parametrize("players", [2, 3, 6])
def test_roulette_always_fires_exactly_once_within_six(players):
    for seed in range(200):
        game = roulette.Game(owner=1, names={i: f"p{i}" for i in range(1, players + 1)})
        game.start(random.Random(seed))
        shots = 0
        while True:
            shooter = game.current
            shots += 1
            if game.pull():
                break
            assert shots < roulette.CHAMBERS
        assert shots == game.bullet + 1
        assert shooter == game.order[(shots - 1) % players]


def test_roulette_turns_rotate():
    game = roulette.Game(owner=1, names={1: "a", 2: "b", 3: "c"})
    game.start(random.Random(0))
    game.bullet = 5  # не стреляет до последнего гнезда
    seen = []
    for _ in range(5):
        seen.append(game.current)
        assert game.pull() is False
    assert seen == game.order + game.order[:2]


# --- Викторина и тексты ----------------------------------------------------

def test_quiz_bank_is_valid():
    bank = quiz.questions()
    assert len(bank) >= 50
    assert len({q.text for q in bank}) == len(bank), "повтор вопроса"
    for q in bank:
        options = [q.correct, *q.wrong]
        assert len(q.wrong) == 3, q.text
        assert len(set(options)) == 4, f"повтор варианта: {q.text}"
        assert all(0 < len(o) <= 100 for o in options), q.text
        assert len(q.text) + 2 <= 300, q.text  # лимит Telegram на вопрос опроса


def test_who_phrases_have_placeholder():
    for phrase in load("phrases", "fun", "who.txt"):
        assert phrase.count("{name}") == 1, phrase


def test_predictions_are_unique_and_stable():
    phrases = load("phrases", "fun", "predictions.txt")
    assert len(phrases) >= 50
    assert len(set(phrases)) == len(phrases)
    assert fun.prediction_for(7, "2026-11-05") == fun.prediction_for(7, "2026-11-05")
    days = {fun.prediction_for(7, f"2026-11-{d:02d}") for d in range(1, 29)}
    assert len(days) > 10, "предсказание почти не меняется день ото дня"


# --- «X дня» ---------------------------------------------------------------

async def test_pick_is_fixed_for_the_day(clean_db):
    assert await db.get_pick(-1, "hero") is None
    assert await db.save_pick(-1, "hero", 10) == (10, None)
    # Второй, опоздавший, не перезаписывает первого.
    assert await db.save_pick(-1, "hero", 20) == (10, None)
    # Другие чат и вид — независимы.
    assert await db.save_pick(-2, "hero", 20) == (20, None)
    assert await db.save_pick(-1, "pair", 1, 2) == (1, 2)
    assert await db.pick_counts(-1, "hero") == [(10, 1)]


async def test_stats_accumulate(clean_db):
    await db.add_stat(-1, 5, "quiz")
    await db.add_stat(-1, 5, "quiz")
    await db.add_stat(-1, 6, "quiz")
    await db.add_stat(-2, 6, "quiz", 10)
    assert await db.top_stat(-1, "quiz") == [(5, 2), (6, 1)]


class FakeUser:
    def __init__(self, uid):
        self.id, self.first_name = uid, "Тест"


class FakeChat:
    id = -100


class FakeMessage:
    def __init__(self, uid=1):
        self.chat, self.from_user, self.sent = FakeChat(), FakeUser(uid), []

    async def answer(self, text, **_):
        self.sent.append(text)


async def test_hero_announces_once(clean_db, monkeypatch):
    monkeypatch.setattr(fun, "REVEAL_DELAY", 0)
    await db.upsert_player(1, "Дмитрий", "dmitry")
    await db.upsert_player(2, "Павел", None)

    first = FakeMessage()
    await fun.hero(first)
    assert len(first.sent) == 4  # три строки подводки и итог
    assert "Герой дня" in first.sent[-1]

    again = FakeMessage()
    await fun.hero(again)
    assert len(again.sent) == 1 and "уже выбран" in again.sent[0]


def test_main_wires_only_fun_routers():
    from bot import main
    commands = {c.command for c in main.COMMANDS}
    assert {"raid", "duel", "profile", "pair"}.isdisjoint(commands), "RPG и пара дня выключены"
    assert {"hero", "quiz", "roulette", "roll", "slots", "top",
            "croc", "wordle", "w", "daily", "balance", "give", "rich"} <= commands


def test_quiz_bank_safe_for_html_parse_mode():
    for q in quiz.questions():
        for text in (q.text, q.correct, *q.wrong):
            assert not set(text) & set("<>&"), text


def test_roulette_survivors_are_only_those_who_pulled():
    game = roulette.Game(owner=1, names={1: "a", 2: "b", 3: "c"})
    game.start(random.Random(0))
    game.bullet = 0  # первый же выстрел
    assert game.pull() is True
    assert game.survivors_who_pulled() == set()

    game.order, game.shot, game.bullet = [1, 2, 3], 0, 4
    while not game.pull():
        pass
    # Выстрелы: 1,2,3,1,2(бах) — выжили 1 и 3.
    assert game.survivors_who_pulled() == {1, 3}


async def test_commands_alias_shows_help():
    from bot.handlers import common
    msg = FakeMessage()
    await common.help_cmd(msg)
    for cmd in ("/hero", "/croc", "/wordle", "/quiz", "/roulette", "/roll", "/slots",
                "/daily", "/give", "/top", "/commands"):
        assert cmd in msg.sent[0], cmd
    from bot import main
    assert "commands" in {c.command for c in main.COMMANDS}


def test_thousand_fortunes_gender_neutral():
    import re
    phrases = load("phrases", "fun", "predictions.txt")
    assert len(phrases) >= 1000
    assert len(set(phrases)) == len(phrases)
    # Прошедшее время в адрес читателя выдаёт род: «ты нашёл», «ты пришла».
    gendered = re.compile(r"\bты\s+\w+(л|ла|лся|лась)\b", re.I)
    assert not [p for p in phrases if gendered.search(p)]
    assert all(len(p) <= 220 for p in phrases)


@pytest.mark.parametrize("text, hit", [
    ("гадание", True), ("Погадай!", True), ("погадай мне", True), ("гадание на день", True),
    ("гадание это фигня", False), ("гадалка", False), ("кто погадай", False),
])
def test_fortune_word_trigger(text, hit):
    import re
    assert bool(re.match(fun.FORTUNE_WORDS, text)) is hit


@pytest.mark.parametrize("outcome", ["jackpot", "triple", "two_sevens", "pair", "miss"])
def test_slot_phrase_banks(outcome):
    phrases = load("phrases", "slots", f"{outcome}.txt")
    assert len(phrases) >= 400
    assert len(set(phrases)) == len(phrases)
    assert all("{" not in p for p in phrases)
    # Без ставки остаётся из чего выбирать, и деньги там не упоминаются.
    free = dice.slot_phrases(outcome, staked=False)
    assert len(free) >= 150
    assert not [p for p in free if dice.MONEY_TALK.search(p)]


def test_free_spin_never_talks_about_stake():
    for value in range(1, 65):
        for _ in range(20):
            assert not dice.MONEY_TALK.search(dice.slot_verdict(value, staked=False)[0])
