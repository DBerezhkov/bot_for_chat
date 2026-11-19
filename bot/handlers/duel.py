"""Дуэли между соклановцами.

Дуэль идёт в клан-чате: вызов, принятие и итог видят все, а содержимое раунда —
только его участники, через всплывающие окна. Ходы вскрываются одновременно,
поэтому бот копит выборы обоих и считает раунд, когда нажали двое.

Тот, кто нажал первым, видит итог раунда со следующим своим нажатием — иначе
показать его было бы некуда: всплывашка появляется только в ответ на действие.

Состояние живёт в памяти: дуэль занимает пару минут и не тратит энергию,
так что потерять её при перезапуске не жалко. Протухшие вызовы и брошенные
бои вычищаются, а один игрок может быть только в одной дуэли сразу.
"""

import html
import random
import time
from dataclasses import dataclass, field

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import db
from bot.game.classes import CLASSES, stats_for
from bot.game.combat import LABELS, SKILL_SCOUT, Fighter, Move, RoundResult, Side, resolve_round

router = Router(name="duel")

ROUNDS = 5
DAILY_EXP_LIMIT = 5
ALERT_LIMIT = 200
# Непринятый вызов живёт 10 минут, начатый бой — полчаса с последнего хода.
CHALLENGE_TTL = 10 * 60
FIGHT_TTL = 30 * 60


@dataclass
class Duel:
    challenger: int
    rival: int
    fighters: dict[int, Fighter]
    round: int = 1
    moves: dict[int, Move | None] = field(default_factory=dict)
    last: RoundResult | None = None
    classes: dict[int, str | None] = field(default_factory=dict)
    charges: dict[int, int] = field(default_factory=dict)  # по заряду навыка на дуэль
    skills: dict[int, str] = field(default_factory=dict)    # навык, нажатый в этом раунде
    created: float = field(default_factory=time.monotonic)
    accepted: bool = False

    @property
    def expired(self) -> bool:
        ttl = FIGHT_TTL if self.accepted else CHALLENGE_TTL
        return time.monotonic() - self.created > ttl

    def rival_of(self, uid: int) -> int:
        return self.rival if uid == self.challenger else self.challenger


DUELS: dict[int, Duel] = {}


def _purge() -> None:
    for duel_id in [i for i, d in DUELS.items() if d.expired]:
        DUELS.pop(duel_id, None)


def busy(uid: int) -> bool:
    """Занят тот, кто дерётся или сам ждёт ответа на свой вызов. Чужой
    непринятый вызов не занимает: иначе любой мог бы «запереть» человека."""
    _purge()
    return any(uid in d.fighters and (d.accepted or uid == d.challenger)
               for d in DUELS.values())


def _fighter(player: db.Player) -> Fighter:
    hp, atk, defense = stats_for(player.cls, player.hp_max, player.atk, player.defense,
                                 duel=True)
    # Имя идёт в сообщения с HTML-разметкой: «<» в нике ломал бы их.
    return Fighter(html.escape(player.name), hp, hp, atk, defense)


def _moves_keyboard(duel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=LABELS[m], callback_data=f"duel:mv:{m.value}:{duel_id}")
         for m in Move],
        [InlineKeyboardButton(text="🎯 Навык класса (1 раз)",
                              callback_data=f"duel:sk:{duel_id}")],
    ])


def health_line(duel: Duel, viewer: int) -> str:
    mine, theirs = duel.fighters[viewer], duel.fighters[duel.rival_of(viewer)]
    return f"❤️ Ты {mine.hp}/{mine.hp_max} · {theirs.name} {theirs.hp}/{theirs.hp_max}"


def round_recap(duel: Duel, viewer: int) -> str:
    """Итог прошлого раунда глазами конкретного участника."""
    if duel.last is None:
        return ""
    last = duel.last
    if viewer == duel.challenger:
        mine, theirs = last.label_a, last.label_b
        to_me, to_them, healed = last.damage_to_a, last.damage_to_b, last.healed_a
    else:
        mine, theirs = last.label_b, last.label_a
        to_me, to_them, healed = last.damage_to_b, last.damage_to_a, last.healed_b
    heal = f" (+{healed} лечение)" if healed else ""
    return (f"{mine} · {theirs}\n"
            f"Тебе −{to_me}{heal}, ему −{to_them}\n")


def waiting_alert(duel: Duel, viewer: int, chosen: str) -> str:
    recap = round_recap(duel, viewer)
    return (f"Раунд {duel.round}/{ROUNDS}\n{recap}{health_line(duel, viewer)}\n\n"
            f"Ты выбрал {chosen}. Ждём соперника.")[:ALERT_LIMIT]


def result_alert(duel: Duel, viewer: int) -> str:
    return (f"{round_recap(duel, viewer)}\n{health_line(duel, viewer)}")[:ALERT_LIMIT]


def public_status(duel: Duel) -> str:
    """Пока идёт бой, клан знает только, что он идёт. Ни раунда, ни здоровья."""
    a, b = duel.fighters[duel.challenger], duel.fighters[duel.rival]
    return f"⚔️ {a.name} против {b.name}\nБой идёт, ходы делают втайне."


def public_result(duel: Duel, winner: int | None) -> str:
    a, b = duel.fighters[duel.challenger], duel.fighters[duel.rival]
    verdict = ("🤝 Ничья. Оба хороши, оба помяты." if winner is None
               else f"🏆 Победил {duel.fighters[winner].name}!")
    return (f"⚔️ Дуэль: {a.name} против {b.name}\n\n{verdict}\n"
            f"{a.name}: {a.hp}/{a.hp_max}   ·   {b.name}: {b.hp}/{b.hp_max}")


def _winner(duel: Duel) -> int | None:
    """По доле оставшегося здоровья: иначе живучий побеждает одним запасом HP."""
    a, b = duel.fighters[duel.challenger], duel.fighters[duel.rival]
    if a.hp_percent > b.hp_percent:
        return duel.challenger
    if b.hp_percent > a.hp_percent:
        return duel.rival
    return None


@router.message(Command("duel"))
async def duel_cmd(message: Message) -> None:
    if not message.reply_to_message:
        await message.answer("Вызывать на дуэль надо ответом на сообщение соклановца.")
        return

    rival_user = message.reply_to_message.from_user
    if rival_user.is_bot or rival_user.id == message.from_user.id:
        await message.answer("Выбери себе живого противника.")
        return

    challenger = await db.get_player(message.from_user.id)
    rival = await db.get_player(rival_user.id)
    if challenger is None or rival is None:
        await message.answer("Оба дуэлянта должны быть в реестре отряда. Напишите /start.")
        return
    if challenger.cls is None or rival.cls is None:
        await message.answer("Без ремесла на дуэль не выходят. Оба выберите класс: /class")
        return
    if busy(challenger.tg_id) or busy(rival.tg_id):
        await message.answer("Кто-то из вас уже в дуэли. Сначала закончите её.")
        return
    name_a, name_b = html.escape(challenger.name), html.escape(rival.name)

    sent = await message.answer(
        f"⚔️ {name_a} вызывает {name_b} на дуэль.\n\nПринимаешь?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="Принять вызов",
                callback_data=f"duel:accept:{challenger.tg_id}:{rival.tg_id}",
            )
        ]]),
    )

    # Пока вызов уходил в чат, параллельный /duel мог успеть занять кого-то из двоих.
    if busy(challenger.tg_id) or busy(rival.tg_id):
        await sent.edit_text("Вызов отменён: кто-то из вас уже в дуэли.")
        return

    DUELS[sent.message_id] = Duel(
        challenger=challenger.tg_id,
        rival=rival.tg_id,
        fighters={challenger.tg_id: _fighter(challenger), rival.tg_id: _fighter(rival)},
        classes={challenger.tg_id: challenger.cls, rival.tg_id: rival.cls},
        charges={challenger.tg_id: 1, rival.tg_id: 1},
    )


@router.callback_query(F.data.startswith("duel:accept:"))
async def accept(callback: CallbackQuery) -> None:
    _, _, _, rival_id = callback.data.split(":")
    if str(callback.from_user.id) != rival_id:
        await callback.answer("Вызов не тебе.", show_alert=True)
        return

    duel_id = callback.message.message_id
    duel = DUELS.get(duel_id)
    if duel is None or duel.expired:
        DUELS.pop(duel_id, None)
        await callback.answer("Этот вызов уже протух.", show_alert=True)
        return
    if duel.accepted:
        await callback.answer("Бой уже идёт.", show_alert=True)
        return

    duel.accepted = True
    duel.created = time.monotonic()  # отсчёт брошенного боя — с начала боя
    await callback.answer(f"Бой начался.\n\n{health_line(duel, callback.from_user.id)}",
                          show_alert=True)
    await callback.message.edit_text(public_status(duel),
                                     reply_markup=_moves_keyboard(duel_id))


async def _award(duel: Duel, winner: int | None) -> str:
    """Начисляет опыт. Дуэли не тратят энергию, поэтому опыт за них ограничен
    пятью в сутки — иначе двое накрутят друг другу уровни за вечер.

    Цена — уровень соперника, как и у тварей в подземельях: победа над сильным
    стоит дорого, избиение новичка не стоит почти ничего.
    """
    # Уровни снимаем ДО начисления: иначе первый победитель успевает вырасти,
    # и второму его победа обходится дороже, чем была на момент боя.
    levels_before = {}
    for tg_id in (duel.challenger, duel.rival):
        player = await db.get_player(tg_id)
        levels_before[tg_id] = player.level if player else 1

    lines = []
    for tg_id in (duel.challenger, duel.rival):
        price = levels_before[duel.rival_of(tg_id)]
        amount = price if tg_id == winner else price // 2
        name = duel.fighters[tg_id].name

        if await db.duels_today(tg_id) >= DAILY_EXP_LIMIT:
            lines.append(f"{name}: опыт не начислен, дневная норма дуэлей выбрана")
            continue

        await db.mark_duel(tg_id)
        levels = await db.add_exp(tg_id, amount)
        line = f"{name}: опыт +{amount}"
        if levels:
            player = await db.get_player(tg_id)
            line += f" · 🎉 уровень {player.level}!"
        lines.append(line)
    return "\n".join(lines)


async def _active(callback: CallbackQuery, raw_id: str) -> tuple[int, Duel] | None:
    """Дуэль, в которой нажавший участвует и ещё не сходил в этом раунде."""
    duel_id = int(raw_id)
    duel = DUELS.get(duel_id)
    if duel is None or not duel.accepted or duel.expired:
        DUELS.pop(duel_id, None)
        await callback.answer("Эта дуэль уже закончилась.", show_alert=True)
        return None

    uid = callback.from_user.id
    if uid not in duel.fighters:
        await callback.answer("Ты в этой дуэли не участвуешь.", show_alert=True)
        return None
    if uid in duel.moves:
        await callback.answer("Ход уже сделан. Ждём соперника.", show_alert=True)
        return None
    return duel_id, duel


@router.callback_query(F.data.startswith("duel:mv:"))
async def make_move(callback: CallbackQuery) -> None:
    _, _, move_value, raw_id = callback.data.split(":")
    found = await _active(callback, raw_id)
    if found is None:
        return
    duel_id, duel = found
    move = Move(move_value)
    await _commit(callback, duel_id, duel, move, LABELS[move])


@router.callback_query(F.data.startswith("duel:sk:"))
async def use_skill(callback: CallbackQuery) -> None:
    _, _, raw_id = callback.data.split(":")
    found = await _active(callback, raw_id)
    if found is None:
        return
    duel_id, duel = found

    uid = callback.from_user.id
    if not duel.charges.get(uid):
        await callback.answer("Навык в этой дуэли уже потрачен.", show_alert=True)
        return

    game_class = CLASSES[duel.classes[uid]]
    if game_class.skill == SKILL_SCOUT:
        # Подсмотреть можно только уже сделанный ход: кто нажал первым,
        # тот и раскрылся. Не нажал — заряд не тратится.
        rival = duel.rival_of(uid)
        if rival not in duel.moves:
            await callback.answer("👁 Соперник ещё думает. Загляни чуть позже.",
                                  show_alert=True)
            return
        duel.charges[uid] = 0
        rival_move = duel.moves[rival]
        seen = (LABELS[rival_move] if rival_move
                else CLASSES[duel.classes[rival]].skill_label)  # он жмёт навык
        await callback.answer(f"👁 Соперник выбрал: {seen}. Отвечай.", show_alert=True)
        return

    duel.charges[uid] = 0
    duel.skills[uid] = game_class.skill
    await _commit(callback, duel_id, duel, None, game_class.skill_label)


async def _commit(callback: CallbackQuery, duel_id: int, duel: Duel,
                  move: Move | None, label: str) -> None:
    uid = callback.from_user.id
    duel.moves[uid] = move
    duel.created = time.monotonic()  # бой брошен, только если долго никто не ходит

    if len(duel.moves) < 2:
        await callback.answer(waiting_alert(duel, uid, label), show_alert=True)
        return

    a, b = duel.fighters[duel.challenger], duel.fighters[duel.rival]
    duel.last = resolve_round(
        a, b, duel.moves[duel.challenger], duel.moves[duel.rival], random.Random(),
        side_a=Side(duel.classes.get(duel.challenger), duel.skills.get(duel.challenger)),
        side_b=Side(duel.classes.get(duel.rival), duel.skills.get(duel.rival)),
    )
    duel.moves.clear()
    duel.skills.clear()
    duel.round += 1

    if duel.round > ROUNDS or not a.alive or not b.alive:
        DUELS.pop(duel_id, None)
        winner = _winner(duel)
        awards = await _award(duel, winner)

        verdict = ("🏆 Ты победил!" if uid == winner
                   else "🤝 Ничья." if winner is None else "💀 Ты проиграл.")
        await callback.answer(f"{round_recap(duel, uid)}\n{verdict}"[:ALERT_LIMIT],
                              show_alert=True)
        await callback.message.edit_text(f"{public_result(duel, winner)}\n\n{awards}")
        return

    # Сообщение в чате не трогаем: раунд сменился, но снаружи это знать незачем.
    await callback.answer(result_alert(duel, uid), show_alert=True)
