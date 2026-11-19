import html
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import config, db
from bot.locks import per_player
from bot.game.classes import CHANGE_COOLDOWN_DAYS, CHANGE_COST, CLASSES, stats_for
from bot.game.progression import exp_to_next, win_chance
from bot.game.world import LOCATIONS

router = Router(name="rpg")


def nearby_locations(level: int) -> list:
    """Подземелье по уровню и следующее за ним. Остальное в профиле — шум:
    дальние локации на низком уровне — сплошные «смертельно» и нули."""
    current = max((i for i, loc in enumerate(LOCATIONS) if loc.min_level <= level), default=0)
    return list(LOCATIONS[current:current + 2])


def _floor_odds(level: int, cls: str | None = None) -> list[str]:
    """Куда игроку соваться и докуда он там дойдёт.

    Считается вслепую, по порогам боя: это честный прогноз для того, кто просто
    жмёт кнопки, а не угадал повадки твари."""
    lines = ["", "🧭 <b>Куда идти</b>"]
    for location in nearby_locations(level):
        odds = " · ".join(
            f"{i}: {win_chance(level, monster, cls):.0%}"
            for i, monster in enumerate(location.monsters, 1)
        )
        lines.append(f"{location.label} ({location.levels}) — {location.verdict(level)}")
        lines.append(f"  этажи {odds}")
    lines.append("<i>Шанс взять этаж в полном здоровье.</i>")
    return lines


def _collection(found: dict[str, int], visited: set[str]) -> list[str]:
    """Только подземелья, где игрок бывал; ненайденное там — за вопросами."""
    # Находка оттуда — тоже след визита: отметки ведутся не с первого дня.
    places = [loc for loc in LOCATIONS
              if loc.key in visited or any(found.get(f.key) for f in loc.findings)]
    if not places:
        return []
    lines = ["", "🗺 <b>Находки</b>"]
    for location in places:
        total = sum(f.limit for f in location.findings)
        mine = sum(min(found.get(f.key, 0), f.limit) for f in location.findings)
        lines.append(f"\n{location.label} — исследовано {round(100 * mine / total)}%")
        for finding in location.findings:
            count = found.get(finding.key, 0)
            if count:
                mark = " (предел)" if count >= finding.limit else ""
                lines.append(f"  ✔ {finding.name} ×{count}{mark}")
            else:
                lines.append("  ❔ ???")
    return lines


@router.message(Command("profile"))
@router.message(F.text.lower() == "профиль")
async def profile(message: Message) -> None:
    player = await db.get_player(message.from_user.id)
    if player is None:
        await message.answer("Тебя нет в реестре отряда. Напиши /start.")
        return

    hp, atk, defense = stats_for(player.cls, player.hp_max, player.atk, player.defense)
    if player.cls:
        game_class = CLASSES[player.cls]
        role = (f"{game_class.label} · навык {game_class.skill_label}, "
                f"{game_class.charges} заряда на поход")
    else:
        role = "Ремесло не выбрано — /class"
    lines = [
        f"👤 <b>{html.escape(player.name)}</b>",
        role,
        "",
        f"❤️ Здоровье: {hp}",
        f"⚔️ Атака: {atk}",
        f"🛡 Защита: {defense}",
        f"⭐ Уровень: {player.level} ({player.exp}/{exp_to_next(player.level)} опыта)",
        "",
        f"⚡ Походы: {player.energy}/{config.MAX_ENERGY}",
    ]
    if player.energy < config.MAX_ENERGY:
        lines.append(f"Восстановление через {db.next_reset()}")

    lines += _floor_odds(player.level, player.cls)
    lines += _collection(await db.get_findings(player.tg_id), await db.visits(player.tg_id))
    await message.answer("\n".join(lines))


def _class_text() -> str:
    lines = ["⚔️ <b>Выбери ремесло</b>", ""]
    for c in CLASSES.values():
        lines.append(f"{c.label} — {c.about}")
        lines.append("")
    lines.append(f"Сменить потом можно не чаще раза в {CHANGE_COOLDOWN_DAYS} дней "
                 f"и за {CHANGE_COST} 🪙. Уровень при смене сохраняется.")
    return "\n".join(lines)


CLASS_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text=c.label, callback_data=f"class:{c.key}")
     for c in list(CLASSES.values())[:2]],
    [InlineKeyboardButton(text=c.label, callback_data=f"class:{c.key}")
     for c in list(CLASSES.values())[2:]],
])


@router.message(Command("class"))
async def class_cmd(message: Message) -> None:
    await message.answer(_class_text(), reply_markup=CLASS_KEYBOARD)


def change_blocked_for(player: db.Player, now: datetime) -> timedelta | None:
    """Сколько ещё ждать до смены класса. None — менять можно."""
    if not player.cls or not player.cls_changed:
        return None
    left = datetime.fromisoformat(player.cls_changed) + timedelta(days=CHANGE_COOLDOWN_DAYS) - now
    return left if left > timedelta(0) else None


@router.callback_query(F.data.startswith("class:"))
@per_player
async def pick_class(callback: CallbackQuery) -> None:
    """Кнопки одни на всех: каждый, кто нажал, выбирает себе."""
    key = callback.data.split(":")[1]
    user = callback.from_user
    await db.upsert_player(user.id, user.full_name, user.username)
    player = await db.get_player(user.id)

    if player.cls == key:
        await callback.answer(f"Ты и так {CLASSES[key].title.lower()}.", show_alert=True)
        return
    if await db.load_run(player.tg_id):
        await callback.answer("Посреди похода ремесло не меняют. Сначала выберись.",
                              show_alert=True)
        return

    if player.cls:
        wait = change_blocked_for(player, datetime.now(config.TZ))
        if wait:
            days = wait.days + (1 if wait.seconds else 0)
            await callback.answer(f"Сменить ремесло можно через {days} дн.", show_alert=True)
            return
        if not await db.spend_coins(player.tg_id, CHANGE_COST):
            await callback.answer(f"Смена ремесла стоит {CHANGE_COST} 🪙. Не хватает.",
                                  show_alert=True)
            return

    await db.set_class(player.tg_id, key)
    game_class = CLASSES[key]
    await callback.answer(f"Теперь ты {game_class.label}. В поход: /raid", show_alert=True)
    verb = "переписал" if player.cls else "записал"
    await callback.message.answer(
        f"📜 Интендант {verb} в книгу: {html.escape(player.name)} — {game_class.label}. "
        "Посмотрим, сколько протянешь."
    )


GUIDE = (
    "⚔️ <b>RPG отряда — как играть</b>\n\n"
    "<b>1. Выбери класс:</b> /class\n"
    "🛡 <b>Воин</b> — живучий, проигранный раунд бьёт его слабее. "
    "Навык <i>Щит</i>: раунд, который нельзя проиграть.\n"
    "🗡 <b>Разбойник</b> — выигранный Укол бьёт в полтора раза сильнее. "
    "Навык <i>Разведка</i>: подсмотреть ход врага до своего.\n"
    "🔮 <b>Маг</b> — хрупкий, но бьёт больнее всех, даже ничья жжёт врага. "
    "Навык <i>Огненный шар</i>: урон мимо защиты, но сам в этот раунд не прикрыт.\n"
    "✨ <b>Хиллер</b> — после каждой убитой твари подлечивается. "
    "Навык <i>Лечение</i>: почти половина здоровья за раз.\n"
    f"Сменить класс: раз в {CHANGE_COOLDOWN_DAYS} дней за {CHANGE_COST} 🪙, уровень сохраняется.\n\n"
    "<b>2. Бой — камень, ножницы, бумага:</b>\n"
    "🛡 Парирование бьёт ⚔️ Удар · ⚔️ Удар бьёт 🗡 Укол · 🗡 Укол бьёт 🛡 Парирование.\n"
    "Одинаковые приёмы — ничья. Урон наносит только победитель раунда. "
    "У каждой твари свои повадки: присмотрись, что она любит, и бей в ответ. "
    "Навык — отдельная кнопка, заряды выдаются на весь поход.\n\n"
    "<b>3. Поход:</b> /raid или слово «поход»\n"
    f"В сутки {config.MAX_ENERGY} похода. Выбираешь подземелье — 🎯 по уровню, "
    "✅ по силам, ☠️ смертельно. После каждой убитой твари развилка: одна тропа к "
    "сундуку, другая к следующей твари, или 🚪 уйти с добычей. Здоровье между этажами "
    "не восстанавливается, а дна у подземелья нет: глубже — больше опыта, но можно "
    "не вернуться. Опыт за убитых тварей остаётся, даже если погиб.\n"
    "Бой виден только тебе во всплывающих окнах, клан видит лишь итог похода. "
    "Потерял сообщение с кнопками — снова /raid.\n\n"
    "<b>4. Дуэль:</b> ответь на сообщение соклановца командой /duel\n"
    "5 раундов, ходы втайне, победа по доле оставшегося здоровья. "
    "Навык — один раз за дуэль. Опыт за первые 5 дуэлей в сутки.\n\n"
    "<b>5. Персонаж:</b> /profile — уровень, характеристики, куда идти "
    "и шансы на каждом этаже, найденные сокровища.\n\n"
    "Сейчас бета: баланс будем подкручивать по вашим походам. "
    "Странности и жалобы — Диме."
)


@router.message(Command("rpg"))
async def guide(message: Message) -> None:
    await message.answer(GUIDE)
