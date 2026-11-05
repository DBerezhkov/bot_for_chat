"""Вордл на весь чат: бот загадывает существительное из пяти букв, отряд
угадывает вместе, шесть попыток на всех.

Догадка — командой /w слово, поэтому игра работает и с включённым privacy mode
и не ловит случайные пятибуквенные слова из переписки. Раунд живёт в памяти:
при перезапуске его не жалко, /wordle загадает новое.
"""

import html
import random
from collections import Counter
from dataclasses import dataclass, field

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot import db
from bot.content.loader import load, pick
from bot.handlers.coins import COIN, under_daily_cap

router = Router(name="wordle")

LENGTH = 5
ATTEMPTS = 6
DAILY_PAID = 5  # оплачиваемых побед в сутки на человека
HIT, NEAR, MISS = "🟩", "🟨", "⬜"


def normalize(word: str) -> str:
    return word.strip().lower().replace("ё", "е")


def answers() -> tuple[str, ...]:
    return load("words", "wordle_answers.txt")


def allowed() -> frozenset[str]:
    return frozenset(load("words", "wordle_allowed.txt")) | frozenset(answers())


def score(guess: str, secret: str) -> str:
    """Классическая раскраска с учётом повторов: жёлтых для буквы не больше,
    чем её осталось в загаданном слове после зелёных."""
    marks = [MISS] * LENGTH
    left = Counter()
    for i, (g, s) in enumerate(zip(guess, secret)):
        if g == s:
            marks[i] = HIT
        else:
            left[s] += 1
    for i, g in enumerate(guess):
        if marks[i] != HIT and left[g] > 0:
            marks[i] = NEAR
            left[g] -= 1
    return "".join(marks)


def reward(attempts_used: int) -> int:
    """С первой попытки — 60, с последней — 10."""
    return 10 * (ATTEMPTS + 1 - attempts_used)


@dataclass
class Round:
    secret: str
    rows: list[tuple[str, str, str]] = field(default_factory=list)  # раскраска, слово, кто

    def board(self) -> str:
        lines = [f"{marks} {word.upper()} — {html.escape(who)}" for marks, word, who in self.rows]
        lines += ["⬛⬛⬛⬛⬛"] * (ATTEMPTS - len(self.rows))
        missing = sorted({c for _, w, _ in self.rows for c in w} - set(self.secret))
        if missing:
            lines += ["", "Нет в слове: " + " ".join(c.upper() for c in missing)]
        return "\n".join(lines)


ROUNDS: dict[int, Round] = {}


@router.message(Command("wordle"))
async def wordle(message: Message) -> None:
    current = ROUNDS.get(message.chat.id)
    if current is None:
        current = ROUNDS[message.chat.id] = Round(random.choice(answers()))
        head = ("🟩 <b>Вордл</b>: загадал существительное из 5 букв.\n"
                f"Угадывайте всем отрядом: <code>/w слово</code>. Попыток {ATTEMPTS} на всех.\n"
                f"{HIT} буква на месте · {NEAR} есть, но не тут · {MISS} нет")
    else:
        head = f"🟩 <b>Вордл</b> уже идёт, попытка {len(current.rows) + 1} из {ATTEMPTS}."
    await message.answer(head + "\n\n" + current.board())


@router.message(Command("w"))
async def guess(message: Message, command: CommandObject) -> None:
    current = ROUNDS.get(message.chat.id)
    if current is None:
        await message.answer("Сейчас ничего не загадано. Начать — /wordle")
        return

    word = normalize(command.args or "")
    if len(word) != LENGTH or not word.isalpha():
        await message.answer(f"Нужно слово из {LENGTH} букв: <code>/w столб</code>")
        return
    if word not in allowed():
        await message.answer(f"«{html.escape(word)}» в моём словаре нет. "
                             "Принимаю только существительные в начальной форме.")
        return
    if any(word == w for _, w, _ in current.rows):
        await message.answer("Это слово уже пробовали.")
        return

    marks = score(word, current.secret)
    current.rows.append((marks, word, message.from_user.first_name))
    tries = len(current.rows)

    if word == current.secret:
        del ROUNDS[message.chat.id]
        coins = reward(tries)
        if under_daily_cap(message.from_user.id, "wordle", DAILY_PAID):
            await db.add_coins(message.from_user.id, coins)
            prize = f"+{coins} {COIN}"
        else:
            prize = "Монеты за вордл на сегодня уже выбраны."
        await db.add_stat(message.chat.id, message.from_user.id, "wordle_win")
        await message.answer(f"{current.board()}\n\n🎉 <b>{word.upper()}</b>! "
                             f"{html.escape(message.from_user.first_name)}, попытка "
                             f"{tries} из {ATTEMPTS}. {prize}\n"
                             f"{pick('phrases', 'wordle', 'win.txt')}\nЕщё — /wordle")
        return

    if tries >= ATTEMPTS:
        del ROUNDS[message.chat.id]
        lose = pick("phrases", "wordle", "lose.txt").format(word=f"<b>{current.secret.upper()}</b>")
        await message.answer(f"{current.board()}\n\n{lose}\nЕщё — /wordle")
        return

    await message.answer(current.board())
