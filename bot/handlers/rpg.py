from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from bot import config, db
from bot.game.progression import exp_to_next, win_chance
from bot.game.world import LOCATIONS

router = Router(name="rpg")


def _floor_odds(level: int) -> list[str]:
    """Куда игроку соваться и докуда он там дойдёт.

    Считается вслепую, по порогам боя: это честный прогноз для того, кто просто
    жмёт кнопки, а не угадал повадки твари."""
    lines = ["", "🧭 <b>Куда идти</b>"]
    for location in LOCATIONS:
        odds = " · ".join(
            f"{i}: {win_chance(level, monster):.0%}"
            for i, monster in enumerate(location.monsters, 1)
        )
        lines.append(f"{location.label} ({location.levels}) — {location.verdict(level)}")
        lines.append(f"  этажи {odds}")
    lines.append("<i>Проценты — шанс взять этаж в полном здоровье.</i>")
    return lines


def _collection(found: dict[str, int]) -> list[str]:
    """Ненайденное прячем за вопросами — иначе исследовать нечего."""
    lines = ["", "🗺 <b>Находки</b>"]
    for location in LOCATIONS:
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

    lines = [
        f"👤 <b>{player.name}</b>",
        "",
        f"❤️ Здоровье: {player.hp_max}",
        f"⚔️ Атака: {player.atk}",
        f"🛡 Защита: {player.defense}",
        f"⭐ Уровень: {player.level} ({player.exp}/{exp_to_next(player.level)} опыта)",
        "",
        f"⚡ Походы: {player.energy}/{config.MAX_ENERGY}",
    ]
    if player.energy < config.MAX_ENERGY:
        lines.append(f"Восстановление через {db.next_reset()}")

    lines += _floor_odds(player.level)
    lines += _collection(await db.get_findings(player.tg_id))
    await message.answer("\n".join(lines))
