"""Русская рулетка: собрали лобби, крутим барабан, жмём курок по очереди.

Барабан на шесть гнёзд, патрон один, поэтому партия кончается не позже
шестого выстрела. Состояние живёт в памяти: партия идёт минуту-другую,
при перезапуске её не жалко.
"""

import html
import random
from dataclasses import dataclass, field

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import db
from bot.content.loader import pick

router = Router(name="roulette")

CHAMBERS = 6
MAX_PLAYERS = 6


@dataclass
class Game:
    owner: int
    names: dict[int, str] = field(default_factory=dict)  # порядок вступления
    order: list[int] = field(default_factory=list)
    bullet: int = 0
    shot: int = 0
    log: list[str] = field(default_factory=list)

    @property
    def started(self) -> bool:
        return bool(self.order)

    @property
    def current(self) -> int:
        return self.order[self.shot % len(self.order)]

    def start(self, rng: random.Random = random) -> None:
        self.order = list(self.names)
        rng.shuffle(self.order)
        self.bullet = rng.randrange(CHAMBERS)

    def survivors_who_pulled(self) -> set[int]:
        """Кто хотя бы раз нажал курок и остался жив. Последний выстрел — смертельный."""
        return {self.order[i % len(self.order)] for i in range(self.shot - 1)} - {
            self.order[(self.shot - 1) % len(self.order)]}

    def pull(self) -> bool:
        """Жмёт курок текущий игрок. True — выстрел."""
        fired = self.shot == self.bullet
        self.shot += 1
        return fired


GAMES: dict[tuple[int, int], Game] = {}  # (chat_id, message_id) → партия


def _lobby_text(game: Game) -> str:
    listed = "\n".join(f"• {html.escape(n)}" for n in game.names.values())
    return ("🔫 <b>Русская рулетка</b>\n\nБарабан на шесть, патрон один.\n\n"
            f"<b>Участники ({len(game.names)}/{MAX_PLAYERS}):</b>\n{listed}\n\n"
            "<i>Создатель крутит барабан, когда наберётся хотя бы двое.</i>")


def _lobby_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🙋 Я в деле", callback_data="rr:join"),
        InlineKeyboardButton(text="🌀 Крутить", callback_data="rr:go"),
    ]])


def _turn_text(game: Game) -> str:
    lines = ["🔫 <b>Русская рулетка</b>", ""] + game.log
    lines += ["", f"Курок у <b>{html.escape(game.names[game.current])}</b>."]
    return "\n".join(lines)


def _turn_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔫 Нажать курок", callback_data="rr:pull"),
    ]])


@router.message(Command("roulette"))
async def roulette(message: Message) -> None:
    game = Game(owner=message.from_user.id,
                names={message.from_user.id: message.from_user.first_name})
    sent = await message.answer(_lobby_text(game), reply_markup=_lobby_keyboard())
    GAMES[(sent.chat.id, sent.message_id)] = game


@router.callback_query(F.data.startswith("rr:"))
async def roulette_button(callback: CallbackQuery) -> None:
    key = (callback.message.chat.id, callback.message.message_id)
    game = GAMES.get(key)
    if game is None:
        await callback.answer("Эта партия уже закончилась.", show_alert=True)
        return

    me = callback.from_user
    action = callback.data.split(":")[1]

    if action == "join":
        if game.started:
            await callback.answer("Барабан уже крутится.")
        elif me.id in game.names:
            await callback.answer("Ты уже в деле.")
        elif len(game.names) >= MAX_PLAYERS:
            await callback.answer("Мест нет: гнёзд всего шесть.", show_alert=True)
        else:
            game.names[me.id] = me.first_name
            await db.upsert_player(me.id, me.full_name, me.username)
            await callback.answer("Ну, рискуем.")
            await _edit(callback, _lobby_text(game), _lobby_keyboard())
        return

    if action == "go":
        if me.id != game.owner:
            await callback.answer("Крутит тот, кто затеял.", show_alert=True)
        elif game.started:
            await callback.answer()
        elif len(game.names) < 2:
            await callback.answer("Одному тут делать нечего. Ждём второго.", show_alert=True)
        else:
            game.start()
            game.log.append("🌀 Барабан раскручен. Порядок определён жребием.")
            await callback.answer()
            await _edit(callback, _turn_text(game), _turn_keyboard())
        return

    # action == "pull"
    if not game.started:
        await callback.answer()
        return
    if me.id != game.current:
        await callback.answer("Не твоя очередь. Не торопись умирать.", show_alert=True)
        return

    name = html.escape(game.names[me.id])
    if not game.pull():
        game.log.append(pick("phrases", "roulette", "click.txt").format(name=name))
        await callback.answer("Щёлк. Живой!")
        await _edit(callback, _turn_text(game), _turn_keyboard())
        return

    del GAMES[key]
    await callback.answer("💥")
    chat = callback.message.chat.id
    await db.add_stat(chat, me.id, "roulette_dead")
    for uid in game.survivors_who_pulled():
        await db.add_stat(chat, uid, "roulette_alive")

    survivors = ", ".join(html.escape(n) for uid, n in game.names.items() if uid != me.id)
    game.log.append(pick("phrases", "roulette", "bang.txt").format(name=name))
    text = "\n".join(["🔫 <b>Русская рулетка</b>", ""] + game.log
                     + ["", f"☠️ {name} выбывает. Выжили: {survivors}.",
                        "Ещё партию — /roulette"])
    await _edit(callback, text, None)


async def _edit(callback: CallbackQuery, text: str,
                markup: InlineKeyboardMarkup | None) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramAPIError:
        pass  # текст не изменился — Telegram ругается, это не беда
