"""Все наборы фраз: не меньше 400, без дублей, плейсхолдеры — ровно те,
что подставляет код. Иначе .format() упадёт прямо в чате."""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", "test:token")

from bot.content.loader import load  # noqa: E402

BANKS = {
    ("jokes", "general.txt"): set(),
    ("jokes", "personal.txt"): {"mention"},
    ("reminders", "hydra.txt"): {"when"},
    ("reminders", "chimera.txt"): {"when"},
    ("fun", "who.txt"): {"name"},
    ("fun", "predictions.txt"): set(),
    ("fun", "hero_intros.txt"): set(),
    ("fun", "hero_finals.txt"): {"mention"},
    ("fun", "clown_intros.txt"): set(),
    ("fun", "clown_finals.txt"): {"mention"},
    ("roulette", "click.txt"): {"name"},
    ("roulette", "bang.txt"): {"name"},
    ("dice", "win.txt"): {"winner"},
    ("dice", "draw.txt"): set(),
    ("croc", "guessed.txt"): {"name", "word"},
    ("croc", "timeout.txt"): {"word"},
    ("croc", "give_up.txt"): {"word"},
    ("croc", "burned.txt"): {"word"},
    ("wordle", "win.txt"): set(),
    ("wordle", "lose.txt"): {"word"},
    ("slots", "jackpot.txt"): set(),
    ("slots", "triple.txt"): set(),
    ("slots", "two_sevens.txt"): set(),
    ("slots", "pair.txt"): set(),
    ("slots", "miss.txt"): set(),
}


@pytest.mark.parametrize("bank, placeholders", BANKS.items(), ids=lambda b: "/".join(b)
                         if isinstance(b, tuple) else "")
def test_bank(bank, placeholders):
    phrases = load("phrases", *bank)
    assert len(phrases) >= 400, len(phrases)
    assert len(set(phrases)) == len(phrases), "дубли"
    for phrase in phrases:
        found = re.findall(r"\{(\w*)\}", phrase)
        assert sorted(found) == sorted(placeholders), phrase
        phrase.format(**{p: "X" for p in placeholders})  # не падает


@pytest.mark.parametrize("kind", ["hero", "clown"])
def test_intros_are_three_messages(kind):
    for intro in load("phrases", "fun", f"{kind}_intros.txt"):
        assert len([p for p in intro.split("|") if p.strip()]) == 3, intro


def test_quiz_has_400():
    from bot.handlers import quiz
    assert len(quiz.questions()) >= 400
