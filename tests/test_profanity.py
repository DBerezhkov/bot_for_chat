import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", "test:token")

from aiogram.types import Message  # noqa: E402

from bot.content.loader import load  # noqa: E402
from bot.handlers import profanity  # noqa: E402

MAT = [
    "хуй", "нахуя", "хуёвый", "охуеть", "пиздец", "распиздяй", "бля", "блять",
    "блядь", "ебать", "заебал", "заебись", "уебок", "ебанутый", "проебал",
    "выебон", "ебло", "наебнулся", "ебёт", "ёб твою", "долбоёб", "пидор",
    "пидр", "мудак", "мудило", "залупа", "гандон", "шлюха", "сука", "сукин сын",
    "БЛЯЯЯЯ", "xyй", "cyka", "Ну ты и мудак, конечно",
]

CLEAN = [
    "оглобля", "корабля", "рубля", "сабля", "страхую", "застрахуйте",
    "хлебать", "дебаты", "ребята", "небо", "себе", "Глеба", "колебаться",
    "погребальный", "спидран", "спидометр", "мудрый", "сукно", "сук", "бляха-муха",
    "штрихуем", "чихуахуа", "Гидра сегодня злая", "команда", "хлеб", "ребёнок",
]


@pytest.mark.parametrize("text", MAT)
def test_detects_mat(text):
    assert profanity.has_mat(text)


@pytest.mark.parametrize("text", CLEAN)
def test_ignores_clean_words(text):
    assert not profanity.has_mat(text)


def test_scold_bank():
    phrases = load("phrases", "mat", "scold.txt")
    assert len(phrases) >= 30
    assert len(set(phrases)) == len(phrases)
    assert not any(profanity.has_mat(p) for p in phrases)
    assert not any("{" in p or "<" in p for p in phrases)


def _msg(text, is_bot=False):
    msg = Message.model_construct(text=text, caption=None,
                                  from_user=SimpleNamespace(is_bot=is_bot))
    object.__setattr__(msg, "reply", AsyncMock())
    return msg


async def test_guard_replies_and_passes_message_on():
    handler = AsyncMock(return_value="ok")
    msg = _msg("да блять опять Гидра")
    assert await profanity.MatGuard()(handler, msg, {}) == "ok"
    msg.reply.assert_awaited_once()
    handler.assert_awaited_once()


async def test_guard_silent_on_clean_and_bots():
    handler = AsyncMock()
    clean, bot = _msg("всем привет"), _msg("сука", is_bot=True)
    await profanity.MatGuard()(handler, clean, {})
    await profanity.MatGuard()(handler, bot, {})
    clean.reply.assert_not_awaited()
    bot.reply.assert_not_awaited()
    assert handler.await_count == 2
