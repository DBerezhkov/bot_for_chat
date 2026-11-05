"""Походы в подземелья.

Поход идёт прямо в клан-чате: без личных сообщений спрятать сам факт, что
где-то висит интерактивное сообщение, Telegram не даёт — кнопки физически
существуют в общем чате. Поэтому прячем содержимое, а не существование:

- пока поход идёт, сообщение полностью анонимно — ни имени, ни локации,
  по нему нельзя понять, кто именно сейчас в подземелье;
- весь бой — во всплывающих окнах, их видит только нажавший;
- по возвращении анонимное сообщение заменяется именной сводкой.

Всплывашка Telegram ограничена 200 символами — отсюда сжатый формат.
"""

import random

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import config, db
from bot.game import run as run_mod
from bot.game.combat import LABELS, Fighter, Move, RoundResult
from bot.game.world import LOCATIONS, LOCATIONS_BY_KEY, Location

router = Router(name="raid")

ALERT_LIMIT = 200

ANONYMOUS_STATUS = "🌫 Кто-то из отряда шевелится в темноте."


def _locations_keyboard(uid: int, level: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=loc.button(level),
                              callback_data=f"raid:go:{loc.key}:{uid}")]
        for loc in LOCATIONS
    ])


def _moves_keyboard(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=LABELS[move], callback_data=f"raid:mv:{move.value}:{uid}")
        for move in Move
    ]])


def _fork_keyboard(uid: int) -> InlineKeyboardMarkup:
    """Уйти можно только отсюда — посреди боя тварь не отпустит."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🌿 Налево", callback_data=f"raid:path:left:{uid}"),
            InlineKeyboardButton(text="🪨 Направо", callback_data=f"raid:path:right:{uid}"),
        ],
        [InlineKeyboardButton(text="🚪 Уйти с добычей",
                              callback_data=f"raid:path:leave:{uid}")],
    ])


def health_line(state: run_mod.RunState, player: db.Player,
                location: Location) -> str:
    """Строка здоровья для всплывашки — её видит только игрок."""
    line = f"❤️ Ты {state.hp}/{player.hp_max}"
    if state.phase == run_mod.PHASE_FIGHT and state.monster_hp > 0:
        monster = run_mod.monster_fighter(state, location)
        line += f"\n👹 {monster.name} {monster.hp}/{monster.hp_max}"
    return line


def round_alert(result: RoundResult, state: run_mod.RunState, player: db.Player,
                location: Location) -> str:
    return (
        f"{LABELS[result.move_a]}  ·  {LABELS[result.move_b]}\n"
        f"{result.description}\n"
        f"Тебе −{result.damage_to_a}, врагу −{result.damage_to_b}\n\n"
        f"{health_line(state, player, location)}"
    )[:ALERT_LIMIT]


def run_exp(state: run_mod.RunState, location: Location) -> int:
    """Опыт копится по ходу похода — по этажам, а не по числу побед: иначе
    выгоднее всего было бы вечно молотить первый этаж самой лёгкой локации."""
    return state.exp


def found_names(keys: list[str]) -> list[str]:
    return [f.name for loc in LOCATIONS for f in loc.findings if f.key in keys]


def public_report(state: run_mod.RunState, name: str, location: Location,
                  level: int, levels_gained: int, exp: int = 0) -> str:
    """Единственное, что клан узнаёт о походе, — этот итог."""
    if state.dead:
        lines = [f"💀 {name} не вернулся из похода. {location.label}"]
    else:
        lines = [f"🏁 {name} вернулся из похода. {location.label}"]

    if state.defeated:
        lines.append(f"⚔️ Повержено: {', '.join(state.defeated)}")
        lines.append(f"🕳 Глубина: {len(state.defeated)} этаж(а)")
    if state.found:
        lines.append(f"🎁 Добыто: {', '.join(found_names(state.found))}")
    if not state.defeated and not state.found:
        lines.append("Ни добычи, ни подвигов. Бывает.")

    tail = f"⭐ Опыт +{exp}"
    if levels_gained:
        tail += f" · 🎉 уровень {level}"
    lines.append(tail)
    return "\n".join(lines)


@router.message(Command("raid"))
@router.message(F.text.lower() == "поход")
async def raid(message: Message) -> None:
    player = await db.get_player(message.from_user.id)
    if player is None:
        await message.answer("Сначала /start, потом подвиги.")
        return

    if await db.load_run(player.tg_id):
        await message.answer("Ты уже в подземелье. Сначала выберись оттуда.")
        return

    if player.energy <= 0:
        await message.answer(
            f"Походы на сегодня кончились. Восстановление через {db.next_reset()}."
        )
        return

    await message.answer(ANONYMOUS_STATUS,
                         reply_markup=_locations_keyboard(player.tg_id, player.level))


async def _guard(callback: CallbackQuery, owner: str) -> db.Player | None:
    """Кнопки висят в общем чате, так что чужие нажатия надо отбивать."""
    if str(callback.from_user.id) != owner:
        await callback.answer("Это чужой поход. Сходи в свой: /raid", show_alert=True)
        return None
    return await db.get_player(callback.from_user.id)


async def _advance_screen(callback: CallbackQuery, state: run_mod.RunState,
                          player: db.Player, location: Location, alert: str) -> None:
    """Показывает игроку всплывашку. Сообщение в чате не меняем вовсе — оно
    и так анонимное и одинаковое на любом шаге, редактировать нечего."""
    if state.phase == run_mod.PHASE_DONE:
        await db.drop_run(player.tg_id)
        exp = run_exp(state, location)
        levels = await db.add_exp(player.tg_id, exp)
        fresh = await db.get_player(player.tg_id)
        report = public_report(state, player.name, location, fresh.level, levels, exp)

        await callback.answer(
            f"{alert}\n\n⚡ Походов осталось: {fresh.energy}/{config.MAX_ENERGY}"[:ALERT_LIMIT],
            show_alert=True,
        )
        await callback.message.edit_text(report)
        return

    await db.save_run(player.tg_id, state.dumps())
    await callback.answer(alert, show_alert=True)

    markup = (_fork_keyboard(player.tg_id) if state.phase == run_mod.PHASE_FORK
              else _moves_keyboard(player.tg_id))
    try:
        await callback.message.edit_reply_markup(reply_markup=markup)
    except TelegramAPIError:
        pass  # клавиатура та же — Telegram отказывается редактировать, это не беда


@router.callback_query(F.data.startswith("raid:go:"))
async def start_run(callback: CallbackQuery) -> None:
    _, _, loc_key, owner = callback.data.split(":")
    player = await _guard(callback, owner)
    if player is None:
        return

    if not await db.spend_energy(player.tg_id):
        await callback.answer("Энергия кончилась.", show_alert=True)
        return

    location = LOCATIONS_BY_KEY[loc_key]
    state = run_mod.start(location, hp=player.hp_max, rng=random.Random())
    await db.save_run(player.tg_id, state.dumps())

    await callback.answer(
        f"Ты входишь в {location.title.lower()}.\n\n"
        f"{health_line(state, player, location)}"[:ALERT_LIMIT],
        show_alert=True,
    )
    await callback.message.edit_reply_markup(reply_markup=_moves_keyboard(player.tg_id))


@router.callback_query(F.data.startswith("raid:mv:"))
async def make_move(callback: CallbackQuery) -> None:
    _, _, move_value, owner = callback.data.split(":")
    player = await _guard(callback, owner)
    if player is None:
        return

    raw = await db.load_run(player.tg_id)
    if raw is None:
        await callback.answer("Этот поход уже закончился.", show_alert=True)
        return

    state = run_mod.RunState.loads(raw)
    location = LOCATIONS_BY_KEY[state.location]
    hero = Fighter(name=player.name, hp=state.hp, hp_max=player.hp_max,
                   atk=player.atk, defense=player.defense)

    result = run_mod.fight_round(state, hero, location, Move(move_value), random.Random())
    await _advance_screen(callback, state, player, location,
                          round_alert(result, state, player, location))


@router.callback_query(F.data.startswith("raid:path:"))
async def choose_path(callback: CallbackQuery) -> None:
    _, _, side, owner = callback.data.split(":")
    player = await _guard(callback, owner)
    if player is None:
        return

    raw = await db.load_run(player.tg_id)
    if raw is None:
        await callback.answer("Этот поход уже закончился.", show_alert=True)
        return

    state = run_mod.RunState.loads(raw)
    location = LOCATIONS_BY_KEY[state.location]
    rng = random.Random()

    if side == "leave":
        run_mod.leave(state)
        await _advance_screen(callback, state, player, location,
                              f"Ты уходишь, пока цел.\n\n{health_line(state, player, location)}")
        return

    if run_mod.take_path(state, location, side, rng):
        prize = run_mod.open_chest(state, location, await db.get_findings(player.tg_id), rng)
        if prize:
            await db.add_finding(player.tg_id, prize.key, prize.stat,
                                 prize.bonus, prize.hp_bonus)
            player = await db.get_player(player.tg_id)
            stat_name = "атака" if prize.stat == "atk" else "защита"
            bonus = f"{stat_name} +{prize.bonus}"
            if prize.hp_bonus:
                bonus += f", HP +{prize.hp_bonus}"
            head = f"📦 Сундук!\n{prize.name} — {bonus}. Навсегда."
        else:
            head = "📦 Сундук. Внутри пыль и чьи-то надежды."
    else:
        head = f"Из темноты выходит {state.monster_name}."

    alert = f"{head}\n\n{health_line(state, player, location)}"
    await _advance_screen(callback, state, player, location, alert)
