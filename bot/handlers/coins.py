"""Монеты: ежедневный бонус, баланс, переводы и список богачей.

Зарабатываются в викторине, крокодиле и вордле, ставятся в слотах и костях.
"""

import html
from collections import Counter

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot import db

router = Router(name="coins")

DAILY_BONUS = 50
COIN = "🪙"


_paid_today: Counter = Counter()  # (день, игра, tg_id) → сколько наград выдано


def under_daily_cap(tg_id: int, game: str, cap: int) -> bool:
    """Засчитывает награду в дневной лимит. False — лимит исчерпан, не платить.

    Без лимита крокодил вдвоём («шепнул слово в личку») и вордл подряд
    печатали бы монеты бесконечно. Счётчик в памяти: перезапуск его сбрасывает,
    это приемлемо.
    """
    key = (db._today(), game, tg_id)
    if _paid_today[key] >= cap:
        return False
    _paid_today[key] += 1
    return True


def parse_amount(raw: str | None) -> int | None:
    """Ставка или сумма из аргумента команды. None — не число или не больше нуля."""
    raw = (raw or "").strip()
    if not (raw.isascii() and raw.isdigit()):
        return None
    amount = int(raw)
    return amount if 0 < amount <= 1_000_000 else None


@router.message(Command("balance", "coins"))
async def balance(message: Message) -> None:
    coins = await db.balance(message.from_user.id)
    await message.answer(f"{COIN} {html.escape(message.from_user.first_name)}, "
                         f"у тебя {coins} монет.\nЕжедневный бонус — /daily")


@router.message(Command("daily"))
async def daily(message: Message) -> None:
    uid = message.from_user.id
    if await db.claim_daily(uid, DAILY_BONUS):
        await message.answer(f"{COIN} Держи жалованье: +{DAILY_BONUS}. "
                             f"Теперь у тебя {await db.balance(uid)}.")
    else:
        await message.answer(f"Жалованье сегодня уже выдано. Следующее — через {db.next_reset()}.")


@router.message(Command("give"))
async def give(message: Message, command: CommandObject) -> None:
    target = message.reply_to_message.from_user if message.reply_to_message else None
    amount = parse_amount(command.args)
    if target is None or amount is None:
        await message.answer("Ответь на сообщение того, кому переводишь: <code>/give 50</code>")
        return
    if target.is_bot or target.id == message.from_user.id:
        await message.answer("Самому себе и ботам не переводят.")
        return
    if not await db.spend_coins(message.from_user.id, amount):
        await message.answer(f"Столько нет. У тебя {await db.balance(message.from_user.id)}.")
        return
    await db.upsert_player(target.id, target.full_name, target.username)
    await db.add_coins(target.id, amount)
    await message.answer(f"{COIN} {html.escape(message.from_user.first_name)} → "
                         f"{html.escape(target.first_name)}: {amount} монет.")


@router.message(Command("rich"))
async def rich(message: Message) -> None:
    players = {p.tg_id: p for p in await db.all_players()}
    rows = await db.richest(10)
    if not rows:
        await message.answer("Казна пуста: никто ещё не брал /daily.")
        return
    lines = [f"{COIN} <b>Самые богатые</b>", ""]
    for place, (uid, coins) in enumerate(rows, 1):
        name = html.escape(players[uid].name) if uid in players else "???"
        lines.append(f"{place}. {name} — {coins}")
    await message.answer("\n".join(lines))
