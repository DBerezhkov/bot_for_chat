"""Азарт на нативных кубиках Telegram (sendDice): слоты и дуэль на костях,
по желанию — на монеты.

Значение кубика выдаёт сервер Telegram, бот его не выбирает и подкрутить
не может — поэтому результат честный для всех.
"""

import asyncio
import html
import random
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import db
from bot.content.loader import load, pick
from bot.handlers.coins import COIN, parse_amount
from bot.services.scheduler import _mention as mention

router = Router(name="dice")

# Анимация кубика идёт пару секунд: комментировать раньше — спойлер.
ANIMATION_DELAY = 3.5

SLOT_SYMBOLS = ("BAR", "🍇", "🍋", "7️⃣")


def slot_reels(value: int) -> tuple[str, str, str]:
    """Значение 🎰 (1–64) → три барабана слева направо.

    Telegram кодирует барабаны в системе счисления по основанию 4:
    64 — три семёрки, 1 — три BAR, 22 — три винограда, 43 — три лимона.
    """
    v = value - 1
    return SLOT_SYMBOLS[v % 4], SLOT_SYMBOLS[v // 4 % 4], SLOT_SYMBOLS[v // 16]


def slot_outcome(value: int) -> str:
    """Исход спина — он же имя файла с фразами в content/phrases/slots/."""
    reels = slot_reels(value)
    if reels == ("7️⃣",) * 3:
        return "jackpot"
    if len(set(reels)) == 1:
        return "triple"
    if reels.count("7️⃣") == 2:
        return "two_sevens"
    if len(set(reels)) == 2:
        return "pair"
    return "miss"


# Фраза говорит о деньгах: без ставки она звучала бы странно.
MONEY_TALK = re.compile(r"×\s*\d|ставк|выплат", re.I)


def slot_phrases(outcome: str, staked: bool) -> list[str]:
    phrases = load("phrases", "slots", f"{outcome}.txt")
    return list(phrases) if staked else [p for p in phrases if not MONEY_TALK.search(p)]


def slot_verdict(value: int, staked: bool = False) -> tuple[str, bool]:
    """Случайная фраза под исход и признак джекпота (три одинаковых)."""
    outcome = slot_outcome(value)
    return random.choice(slot_phrases(outcome, staked)), outcome in ("jackpot", "triple")


def slot_payout(value: int) -> int:
    """Во сколько раз возвращается ставка (вместе с самой ставкой).

    Шансы: три семёрки 1/64, другая тройка 3/64, ровно две семёрки 9/64.
    Матожидание (15 + 3·5 + 9·2) / 64 = 0.75 — казино в плюсе, бонус /daily
    не даёт обнулиться навсегда.
    """
    reels = slot_reels(value)
    if reels == ("7️⃣",) * 3:
        return 15
    if len(set(reels)) == 1:
        return 5
    if reels.count("7️⃣") == 2:
        return 2
    return 0


async def _comment_slot(message: Message, value: int, user_id: int, stake: int = 0) -> None:
    await asyncio.sleep(ANIMATION_DELAY)
    text, jackpot = slot_verdict(value, staked=bool(stake))
    if jackpot:
        await db.add_stat(message.chat.id, user_id, "jackpot")
    if stake:
        won = stake * slot_payout(value)
        if won:
            await db.add_coins(user_id, won)
            text += f"\n{COIN} Выигрыш: {won}."
        else:
            text += f"\n{COIN} Ставка {stake} ушла в казну."
    await message.reply(text)


@router.message(Command("slots"))
async def slots(message: Message, command: CommandObject) -> None:
    """Без аргумента — на интерес, с числом — на монеты."""
    stake = 0
    if command.args:
        stake = parse_amount(command.args)
        if stake is None:
            await message.answer("Ставка — целое число: <code>/slots 20</code>")
            return
        if not await db.spend_coins(message.from_user.id, stake):
            await message.answer(f"Не хватает монет. У тебя {await db.balance(message.from_user.id)}.")
            return
    try:
        spin = await message.answer_dice(emoji="🎰")
    except TelegramAPIError:
        if stake:
            await db.add_coins(message.from_user.id, stake)
        raise
    await _comment_slot(spin, spin.dice.value, message.from_user.id, stake)


@router.message(F.dice.emoji == "🎰", F.forward_origin.is_(None))
async def own_slot(message: Message) -> None:
    """Кто сам кинул 🎰 в чат — тоже получает вердикт. Пересланные не считаем."""
    await _comment_slot(message, message.dice.value, message.from_user.id)


# --- Дуэль на костях -------------------------------------------------------

_settled: set[int] = set()  # id вызовов, по которым кости уже брошены


def _challenge_keyboard(challenger: int, rival: int, stake: int) -> InlineKeyboardMarkup:
    tail = f"{challenger}:{rival}:{stake}"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎲 Принять", callback_data=f"roll:ok:{tail}"),
        InlineKeyboardButton(text="🏳 Отказ", callback_data=f"roll:no:{tail}"),
    ]])


@router.message(Command("roll"))
async def roll(message: Message, command: CommandObject) -> None:
    """Ответом на сообщение — вызов конкретному человеку, иначе — любому.
    Число после команды — ставка: победитель забирает обе."""
    me = message.from_user
    target = message.reply_to_message.from_user if message.reply_to_message else None

    if target and (target.is_bot or target.id == me.id):
        await message.answer("Сам с собой и с ботами не играют. Найди живого соперника.")
        return

    stake = 0
    if command.args:
        stake = parse_amount(command.args)
        if stake is None:
            await message.answer("Ставка — целое число: <code>/roll 50</code>")
            return
        if await db.balance(me.id) < stake:
            await message.answer(f"Не хватает монет. У тебя {await db.balance(me.id)}.")
            return

    bet = f" Ставка: {stake} {COIN}." if stake else ""
    if target:
        text = (f"🎲 {html.escape(me.first_name)} вызывает {html.escape(target.first_name)} "
                f"на кости. У кого больше — тот и прав.{bet}")
    else:
        text = (f"🎲 {html.escape(me.first_name)} ищет соперника на кости.{bet} "
                "Кто смелый — жми «Принять».")
    await message.answer(text, reply_markup=_challenge_keyboard(
        me.id, target.id if target else 0, stake))


@router.callback_query(F.data.startswith("roll:"))
async def roll_answer(callback: CallbackQuery, bot: Bot) -> None:
    _, action, challenger, rival, stake = callback.data.split(":")
    challenger, rival, stake = int(challenger), int(rival), int(stake)
    me = callback.from_user
    key = callback.message.message_id

    if key in _settled:
        await callback.answer("Тут уже всё решено.")
        return
    if me.id == challenger:
        if action == "no":
            _settled.add(key)
            await callback.answer()
            await callback.message.edit_text("🎲 Вызов отозван.")
        else:
            await callback.answer("Это твой же вызов. Жди соперника.", show_alert=True)
        return
    if rival and me.id != rival:
        await callback.answer("Вызов не тебе.", show_alert=True)
        return
    if action == "no" and not rival:
        await callback.answer("Не хочешь — не жми. Вызов открыт для остальных.")
        return
    _settled.add(key)

    if action == "no":
        await callback.answer()
        await callback.message.edit_text(f"🏳 {html.escape(me.first_name)} отказывается. Вызов снят.")
        return

    if stake:
        # Сначала соперник: если у него не хватает, вызов остаётся открытым.
        if not await db.spend_coins(me.id, stake):
            _settled.discard(key)
            await callback.answer(f"Нужно {stake} монет, у тебя {await db.balance(me.id)}.",
                                  show_alert=True)
            return
        if not await db.spend_coins(challenger, stake):
            await db.add_coins(me.id, stake)
            await callback.answer()
            await callback.message.edit_text("🎲 У вызвавшего кончились монеты. Вызов снят.")
            return

    chat = callback.message.chat.id
    players = {p.tg_id: p for p in await db.all_players()}
    await db.upsert_player(me.id, me.full_name, me.username)
    first_name = html.escape(players[challenger].name) if challenger in players else "Вызвавший"

    try:
        await callback.answer()
        await callback.message.edit_reply_markup(reply_markup=None)
        await bot.send_message(chat, f"🎲 Бросает {first_name}:")
        a = (await bot.send_dice(chat, emoji="🎲")).dice.value
        await bot.send_message(chat, f"🎲 Бросает {html.escape(me.first_name)}:")
        b = (await bot.send_dice(chat, emoji="🎲")).dice.value
    except TelegramAPIError:
        if stake:  # бросок сорвался — деньги назад обоим
            await db.add_coins(challenger, stake)
            await db.add_coins(me.id, stake)
        raise
    await asyncio.sleep(ANIMATION_DELAY)

    if a == b:
        if stake:
            await db.add_coins(challenger, stake)
            await db.add_coins(me.id, stake)
        await bot.send_message(chat, f"🤝 {a} : {b} — " + pick("phrases", "dice", "draw.txt")
                                     + (" Ставки вернул." if stake else ""))
        return

    winner = challenger if a > b else me.id
    await db.add_stat(chat, winner, "dice_win")
    if stake:
        await db.add_coins(winner, 2 * stake)
    winner_player = await db.get_player(winner)
    prize = f" +{2 * stake} {COIN}" if stake else ""
    verdict = pick("phrases", "dice", "win.txt").format(winner=mention(winner_player))
    await bot.send_message(chat, f"🏆 {max(a, b)} : {min(a, b)} — {verdict}{prize}")
