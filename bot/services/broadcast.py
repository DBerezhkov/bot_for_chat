"""Отправка сообщений в клан-чат."""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from bot import config

log = logging.getLogger(__name__)


async def to_clan(bot: Bot, text: str) -> None:
    """Публикует в клан-чат. Молча пропускает, если чат не настроен."""
    if config.CLAN_CHAT_ID is None:
        return
    try:
        await bot.send_message(config.CLAN_CHAT_ID, text)
    except TelegramAPIError as error:
        log.warning("Не удалось написать в клан-чат: %s", error)
