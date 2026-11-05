"""Крокодил: ведущий объясняет слово, не называя его, чат угадывает.

Ведущий видит слово всплывающим окном по кнопке — его не видит больше никто.
Догадки — обычные сообщения в чате, поэтому боту нужно видеть переписку:
privacy mode должен быть выключен (или бот — администратор группы).

Раунд живёт в памяти и сам заканчивается через ROUND_SECONDS.
"""

import asyncio
import html
import random
import re
from dataclasses import dataclass, field

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import db
from bot.content.loader import load, pick
from bot.handlers.coins import COIN, under_daily_cap

router = Router(name="crocodile")

ROUND_SECONDS = 180
MAX_SKIPS = 3
GUESSER_REWARD = 15
LEADER_REWARD = 10
DAILY_PAID = 10  # сколько раз в сутки крокодил платит одному человеку


def normalize(text: str) -> str:
    return text.lower().replace("ё", "е")


# Падежные окончания существительных (ё уже заменена на е).
ENDINGS = frozenset("""а я у ю е и ы о ом ем ой ей ою ею ам ям ами ями ах ях ов ев ь""".split()) | {""}


def is_guess(text: str, word: str) -> bool:
    """Слово названо, если оно стоит в сообщении отдельным словом — в любом
    падеже: «дома» для «дом», «ёлку» для «ёлка». Засчитываем только основу
    плюс падежное окончание, поэтому «котёл» не сойдёт за «кот»."""
    word = normalize(word)
    stem = word[:-1] if word[-1] in "аеиоуыэюяйь" else word
    for token in re.findall(r"[а-яa-z]+", normalize(text)):
        if token == word or (len(stem) >= 2 and token.startswith(stem)
                             and token[len(stem):] in ENDINGS):
            return True
    return False


def _say(bank: str, word: str, **names: str) -> str:
    """Реплика из набора content/phrases/croc/: слово — жирным."""
    return pick("phrases", "croc", bank).format(word=f"<b>{word}</b>", **names)


def new_word(exclude: str | None = None) -> str:
    words = load("words", "crocodile.txt")
    while True:
        word = random.choice(words)
        if word != exclude:
            return word


@dataclass
class Round:
    leader: int
    leader_name: str
    word: str
    skips: int = 0
    timer: asyncio.Task | None = field(default=None, repr=False)


ROUNDS: dict[int, Round] = {}


def _round_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👀 Посмотреть слово", callback_data="croc:peek")],
        [InlineKeyboardButton(text="🔄 Другое слово", callback_data="croc:skip"),
         InlineKeyboardButton(text="🏳 Сдаться", callback_data="croc:give_up")],
    ])


NEXT_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(text="🎤 Хочу быть ведущим", callback_data="croc:lead"),
]])


def _open(chat_id: int, leader_id: int, leader_name: str) -> Round:
    """Занимает чат раундом синхронно, до первого await, — иначе два быстрых
    нажатия «Хочу быть ведущим» успели бы создать два раунда."""
    current = ROUNDS[chat_id] = Round(leader_id, leader_name, new_word())
    return current


async def _start(bot: Bot, chat_id: int, current: Round) -> None:
    leader_name = current.leader_name
    current.timer = asyncio.create_task(_expire(bot, chat_id, current))
    await bot.send_message(
        chat_id,
        f"🐊 <b>Крокодил</b>. Ведущий — {html.escape(leader_name)}.\n"
        "Ведущий жмёт «Посмотреть слово» и объясняет его, не называя. "
        f"Остальные пишут догадки прямо в чат. На всё {ROUND_SECONDS // 60} минуты.",
        reply_markup=_round_keyboard(),
    )


async def _expire(bot: Bot, chat_id: int, current: Round) -> None:
    await asyncio.sleep(ROUND_SECONDS)
    if ROUNDS.get(chat_id) is current:
        del ROUNDS[chat_id]
        await bot.send_message(chat_id, _say("timeout.txt", word=current.word),
                               reply_markup=NEXT_KEYBOARD)


def _finish(chat_id: int) -> Round | None:
    current = ROUNDS.pop(chat_id, None)
    if current and current.timer:
        current.timer.cancel()
    return current


@router.message(Command("croc"))
async def croc(message: Message, bot: Bot) -> None:
    current = ROUNDS.get(message.chat.id)
    if current:
        await message.answer(f"🐊 Раунд уже идёт, ведущий — {html.escape(current.leader_name)}.")
        return
    current = _open(message.chat.id, message.from_user.id, message.from_user.first_name)
    await _start(bot, message.chat.id, current)


@router.callback_query(F.data.startswith("croc:"))
async def croc_button(callback: CallbackQuery, bot: Bot) -> None:
    chat_id = callback.message.chat.id
    me = callback.from_user
    action = callback.data.split(":")[1]
    current = ROUNDS.get(chat_id)

    if action == "lead":
        if current:
            await callback.answer(f"Уже ведёт {current.leader_name}.", show_alert=True)
            return
        current = _open(chat_id, me.id, me.first_name)
        await callback.answer()
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            pass
        await _start(bot, chat_id, current)
        return

    if current is None:
        await callback.answer("Раунд уже закончился.", show_alert=True)
        return
    if me.id != current.leader:
        await callback.answer("Это кнопка ведущего. Твоё дело — угадывать.", show_alert=True)
        return

    if action == "peek":
        await callback.answer(f"Твоё слово: {current.word.upper()}", show_alert=True)
    elif action == "skip":
        if current.skips >= MAX_SKIPS:
            await callback.answer("Замены кончились. Объясняй что есть.", show_alert=True)
            return
        current.skips += 1
        current.word = new_word(exclude=current.word)
        await callback.answer(f"Новое слово: {current.word.upper()}\n"
                              f"Замен осталось: {MAX_SKIPS - current.skips}", show_alert=True)
    elif action == "give_up":
        _finish(chat_id)
        await callback.answer()
        await bot.send_message(chat_id, _say("give_up.txt", word=current.word),
                               reply_markup=NEXT_KEYBOARD)


def _round_active(message: Message) -> bool:
    return message.chat.id in ROUNDS


@router.message(F.text, ~F.text.startswith("/"), _round_active)
async def croc_guess(message: Message) -> None:
    current = ROUNDS.get(message.chat.id)
    if current is None or not is_guess(message.text, current.word):
        return

    user = message.from_user
    _finish(message.chat.id)
    if user.id == current.leader:
        await message.reply(_say("burned.txt", word=current.word),
                            reply_markup=NEXT_KEYBOARD)
        return

    paid = []
    if under_daily_cap(user.id, "croc", DAILY_PAID):
        await db.add_coins(user.id, GUESSER_REWARD)
        paid.append(f"+{GUESSER_REWARD} угадавшему")
    if under_daily_cap(current.leader, "croc", DAILY_PAID):
        await db.add_coins(current.leader, LEADER_REWARD)
        paid.append(f"+{LEADER_REWARD} ведущему")
    await db.add_stat(message.chat.id, user.id, "croc_guess")
    await db.add_stat(message.chat.id, current.leader, "croc_lead")
    coins = f"{COIN} {', '.join(paid)}." if paid else "Монеты на сегодня уже выбраны — играем на интерес."
    await message.reply(
        _say("guessed.txt", word=current.word, name=html.escape(user.first_name)) + f"\n{coins}",
        reply_markup=NEXT_KEYBOARD,
    )
