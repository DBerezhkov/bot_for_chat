import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", "test:token")

from bot import config, db  # noqa: E402
from bot.content.loader import load  # noqa: E402
from bot.handlers import coins, crocodile, dice, wordle  # noqa: E402
from bot.handlers.wordle import HIT, MISS, NEAR  # noqa: E402

BANNED = {"шлюха", "сука", "дерьмо", "секс", "пенис", "аборт", "негр", "еврей", "привет", "пора"}


@pytest.fixture
async def clean_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    await db.init()


# --- Словари ---------------------------------------------------------------

def test_crocodile_words_big_clean_unique():
    words = load("words", "crocodile.txt")
    assert len(words) >= 2500
    assert len(set(words)) == len(words)
    assert all(w.isalpha() and w == w.lower() for w in words)
    assert not BANNED & set(words)


def test_wordle_dictionaries():
    answers = wordle.answers()
    assert len(answers) >= 600
    assert len(set(answers)) == len(answers)
    assert all(len(w) == 5 and w.isalpha() and "ё" not in w for w in answers)
    assert set(answers) <= wordle.allowed()
    assert len(wordle.allowed()) >= 3000
    assert not BANNED & set(answers)


# --- Вордл -----------------------------------------------------------------

@pytest.mark.parametrize("guess, secret, marks", [
    ("банка", "банка", HIT * 5),
    ("стиль", "банка", MISS * 5),
    ("столб", "банка", MISS * 4 + NEAR),
    ("кабан", "банка", NEAR + HIT + NEAR + NEAR + NEAR),
    # Повтор: в «книга» одна «а», вторая «а» догадки не красится.
    ("аллея", "книга", NEAR + MISS * 4),
    # Зелёная буква съедает единственную такую же: жёлтой больше нет.
    ("пап" + "ка", "лапка", MISS + HIT + HIT + HIT + HIT),
])
def test_wordle_score(guess, secret, marks):
    assert wordle.score(guess, secret) == marks


def test_wordle_reward_scale():
    assert wordle.reward(1) == 60 and wordle.reward(6) == 10


class FakeUser:
    def __init__(self, uid=1, name="Тест"):
        self.id, self.first_name, self.full_name, self.username = uid, name, name, None
        self.is_bot = False


class FakeChat:
    id = -100


class FakeMessage:
    def __init__(self, uid=1, text=""):
        self.chat, self.from_user, self.text, self.sent = FakeChat(), FakeUser(uid), text, []

    async def answer(self, text, **_):
        self.sent.append(text)

    reply = answer


class Args:
    def __init__(self, args):
        self.args = args


async def test_wordle_round_win_pays(clean_db):
    wordle.ROUNDS[FakeChat.id] = wordle.Round("банка")
    miss = FakeMessage()
    await wordle.guess(miss, Args("столб"))
    assert "СТОЛБ" in miss.sent[0]

    bad = FakeMessage()
    await wordle.guess(bad, Args("ыыыыы"))
    assert "нет" in bad.sent[0] and len(wordle.ROUNDS[FakeChat.id].rows) == 1

    win = FakeMessage(uid=2)
    await wordle.guess(win, Args("БАНКА"))
    assert FakeChat.id not in wordle.ROUNDS
    assert await db.balance(2) == db.START_COINS + wordle.reward(2)


async def test_wordle_round_lost_after_six(clean_db):
    wordle.ROUNDS[FakeChat.id] = wordle.Round("банка")
    tries = [w for w in wordle.answers() if w != "банка"][:wordle.ATTEMPTS]
    for word in tries:
        msg = FakeMessage()
        await wordle.guess(msg, Args(word))
    assert FakeChat.id not in wordle.ROUNDS
    assert "БАНКА" in msg.sent[-1]


# --- Крокодил --------------------------------------------------------------

@pytest.mark.parametrize("text, word, hit", [
    ("ёлка", "ёлка", True), ("Это ЁЛКА!", "ёлка", True), ("может, елка?", "ёлка", True),
    ("ёлку", "ёлка", True), ("ёлками", "ёлка", True),
    ("ёл ка", "ёлка", False), ("ёлочный", "ёлка", False),
    ("дома", "дом", True), ("домом", "дом", True), ("домик", "дом", False),
    ("котёл", "кот", False), ("кота", "кот", True),
])
def test_crocodile_guess_matching(text, word, hit):
    assert crocodile.is_guess(text, word) is hit


async def test_crocodile_guess_rewards_both(clean_db):
    crocodile.ROUNDS[FakeChat.id] = crocodile.Round(leader=1, leader_name="Вед", word="слон")
    await crocodile.croc_guess(FakeMessage(uid=2, text="кот"))
    assert FakeChat.id in crocodile.ROUNDS

    await crocodile.croc_guess(FakeMessage(uid=2, text="это слон!"))
    assert FakeChat.id not in crocodile.ROUNDS
    assert await db.balance(2) == db.START_COINS + crocodile.GUESSER_REWARD
    assert await db.balance(1) == db.START_COINS + crocodile.LEADER_REWARD


async def test_crocodile_leader_burns_round(clean_db):
    crocodile.ROUNDS[FakeChat.id] = crocodile.Round(leader=1, leader_name="Вед", word="слон")
    msg = FakeMessage(uid=1, text="слон")
    await crocodile.croc_guess(msg)
    assert FakeChat.id not in crocodile.ROUNDS
    assert "<b>слон</b>" in msg.sent[0]  # раунд закрыт, слово раскрыто
    assert await db.balance(1) == db.START_COINS


# --- Монеты ----------------------------------------------------------------

async def test_wallet_never_negative(clean_db):
    assert await db.balance(1) == db.START_COINS
    assert await db.spend_coins(1, db.START_COINS + 1) is False
    assert await db.spend_coins(1, 60) is True
    assert await db.spend_coins(1, 60) is False
    assert await db.balance(1) == 40


async def test_daily_once_per_day(clean_db):
    assert await db.claim_daily(1, 50) is True
    assert await db.claim_daily(1, 50) is False
    assert await db.balance(1) == db.START_COINS + 50


@pytest.mark.parametrize("raw, amount", [
    ("50", 50), (" 7 ", 7), ("0", None), ("-5", None), ("abc", None), (None, None),
    ("99999999", None), ("²", None), ("５", None),
])
def test_parse_amount(raw, amount):
    assert coins.parse_amount(raw) == amount


def test_slots_house_edge():
    ev = sum(dice.slot_payout(v) for v in range(1, 65)) / 64
    assert ev == 0.75
