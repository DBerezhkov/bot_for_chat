"""Викторина на нативных quiz-опросах Telegram.

Опрос не анонимный, поэтому Telegram присылает боту каждый ответ (poll_answer),
и бот засчитывает очко за верный. Работает и с включённым privacy mode.
"""

import random
import time
from dataclasses import dataclass

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, Poll, PollAnswer

from bot import db
from bot.content.loader import load

router = Router(name="quiz")

OPEN_SECONDS = 30
REWARD = 10  # монет за верный ответ
# Не чаще одного вопроса в этот промежуток на чат — иначе чат превратится
# в ленту опросов.
COOLDOWN = OPEN_SECONDS


@dataclass(frozen=True)
class Question:
    text: str
    correct: str
    wrong: tuple[str, ...]


def questions() -> list[Question]:
    parsed = []
    for line in load("quiz", "questions.txt"):
        text, correct, *wrong = (part.strip() for part in line.split("|"))
        parsed.append(Question(text, correct, tuple(wrong)))
    return parsed


ACTIVE: dict[str, tuple[int, int]] = {}  # poll_id → (chat_id, номер верного варианта)
_last_asked: dict[int, float] = {}       # chat_id → когда задан последний вопрос


@router.message(Command("quiz"))
async def quiz(message: Message) -> None:
    chat_id = message.chat.id
    wait = _last_asked.get(chat_id, 0) + COOLDOWN - time.monotonic()
    if wait > 0:
        await message.answer(f"Дай ответить на прошлый вопрос. Следующий — через {int(wait) + 1} с.")
        return
    _last_asked[chat_id] = time.monotonic()

    question = random.choice(questions())
    options = [question.correct, *question.wrong]
    random.shuffle(options)
    correct = options.index(question.correct)

    sent = await message.answer_poll(
        question=f"🧠 {question.text}", options=options,
        type="quiz", correct_option_id=correct,
        is_anonymous=False, open_period=OPEN_SECONDS,
    )
    ACTIVE[sent.poll.id] = (chat_id, correct)


@router.poll_answer()
async def quiz_answer(answer: PollAnswer) -> None:
    active = ACTIVE.get(answer.poll_id)
    if active is None or answer.user is None:
        return
    chat_id, correct = active
    user = answer.user
    await db.upsert_player(user.id, user.full_name, user.username)
    if answer.option_ids == [correct]:
        await db.add_stat(chat_id, user.id, "quiz")
        await db.add_coins(user.id, REWARD)


@router.poll()
async def quiz_closed(poll: Poll) -> None:
    """Опрос закрылся по таймеру — ответов больше не будет, забываем его."""
    if poll.is_closed:
        ACTIVE.pop(poll.id, None)
