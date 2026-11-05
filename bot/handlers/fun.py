"""Ежедневные развлечения чата: герой и клоун дня, «кто сегодня…»,
предсказание и общий рейтинг.

Выбор «X дня» делается один раз в сутки на чат и хранится в базе: повторный
вызов показывает уже выбранного, а не перекидывает жребий. Кандидаты — все,
кого бот знает (см. middlewares.RegisterPlayer и /init).
"""

import asyncio
import html
import random

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot import db
from bot.content.loader import load, pick
from bot.services.scheduler import _mention as mention

router = Router(name="fun")

# Пауза между строками подводки — ради интриги. Тесты выставляют её в ноль.
REVEAL_DELAY = 1.3

TITLES = {
    "hero": ("🏅", "Герой дня"),
    "clown": ("🤡", "Клоун дня"),
}

_locks: dict[str, asyncio.Lock] = {}


async def _players_by_id() -> dict[int, db.Player]:
    return {p.tg_id: p for p in await db.all_players()}


async def _reveal(message: Message, lines: tuple[str, ...]) -> None:
    for line in lines:
        await message.answer(line)
        await asyncio.sleep(REVEAL_DELAY)


async def _title_of_day(message: Message, kind: str) -> None:
    icon, title = TITLES[kind]
    chat_id = message.chat.id
    lock = _locks.setdefault(f"{chat_id}:{kind}", asyncio.Lock())

    # Под замком: два одновременных вызова иначе устроили бы двойную подводку.
    async with lock:
        players = await _players_by_id()
        picked = await db.get_pick(chat_id, kind)
        if picked:
            winner = players.get(picked[0])
            name = html.escape(winner.name) if winner else "кто-то, кого я уже не помню"
            await message.answer(f"{icon} {title} уже выбран: <b>{name}</b>.\n"
                                 "Новый жребий — завтра.")
            return

        if not players:
            await message.answer("В реестре пусто. Пусть народ нажмёт кнопку в /init.")
            return

        first, _ = await db.save_pick(chat_id, kind, random.choice(list(players)))
        intro = pick("phrases", "fun", f"{kind}_intros.txt")
        await _reveal(message, tuple(part.strip() for part in intro.split("|")))
        final = pick("phrases", "fun", f"{kind}_finals.txt")
        await message.answer(final.format(mention=mention(players[first])))


@router.message(Command("hero"))
async def hero(message: Message) -> None:
    await _title_of_day(message, "hero")


@router.message(Command("clown"))
async def clown(message: Message) -> None:
    await _title_of_day(message, "clown")


@router.message(Command("who"))
async def who(message: Message, command: CommandObject) -> None:
    question = (command.args or "").strip().rstrip("?")
    if not question:
        await message.answer("Спроси как надо: <code>/who сегодня сливает ключи</code>")
        return

    players = list((await _players_by_id()).values())
    if not players:
        await message.answer("Выбирать не из кого — реестр пуст. /init")
        return

    chosen = random.choice(players)
    verdict = random.choice(load("phrases", "fun", "who.txt"))
    await message.answer(f"🤔 Кто {html.escape(question)}?\n"
                         + verdict.format(name=f"<b>{html.escape(chosen.name)}</b>"))


def prediction_for(tg_id: int, day: str) -> str:
    """Одно предсказание на человека на весь день: перезапрос не перекидывает."""
    return random.Random(f"{day}:{tg_id}").choice(load("phrases", "fun", "predictions.txt"))


# «Гадание», «погадай», «гадание на день» — обычным словом, без слэша.
FORTUNE_WORDS = r"(?i)^\s*(по)?гада(й|ние|ть)(\s+(мне|на\s+день|на\s+сегодня))*\s*[!?.)]*\s*$"


@router.message(Command("predict", "fortune"))
@router.message(F.text.regexp(FORTUNE_WORDS))
async def predict(message: Message) -> None:
    text = prediction_for(message.from_user.id, db._today())
    await message.answer(f"🔮 {html.escape(message.from_user.first_name)}, {text}")


# Что выводится в /top и в каком порядке. Ключ — либо вид «X дня»,
# либо счётчик из fun_stats.
BOARDS = [
    ("pick", "hero", "🏅 Герои дня"),
    ("pick", "clown", "🤡 Клоуны дня"),
    ("stat", "quiz", "🧠 Викторина, верных ответов"),
    ("stat", "croc_guess", "🐊 Угадано в крокодиле"),
    ("stat", "croc_lead", "🎤 Объяснено в крокодиле"),
    ("stat", "wordle_win", "🟩 Отгадано вордлов"),
    ("stat", "dice_win", "🎲 Побед в костях"),
    ("stat", "jackpot", "🎰 Джекпотов"),
    ("stat", "roulette_alive", "🔫 Пережито рулеток"),
    ("stat", "roulette_dead", "💀 Пуль в рулетке"),
]


@router.message(Command("top"))
async def top(message: Message) -> None:
    players = await _players_by_id()
    lines = ["🏆 <b>Доска почёта отряда</b>"]
    for source, key, title in BOARDS:
        if source == "pick":
            rows = (await db.pick_counts(message.chat.id, key))[:5]
        else:
            rows = await db.top_stat(message.chat.id, key, 5)
        if not rows:
            continue
        lines += ["", f"<b>{title}</b>"]
        for place, (uid, count) in enumerate(rows, 1):
            name = html.escape(players[uid].name) if uid in players else "???"
            lines.append(f"{place}. {name} — {count}")

    if len(lines) == 1:
        lines.append("\nПока пусто. Начните с /hero, /quiz или /croc.")
    await message.answer("\n".join(lines))
