import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from bot import db

log = logging.getLogger(__name__)
_seen_chats: set[int] = set()


class RegisterPlayer(BaseMiddleware):
    """Заносит в книгу всякого, кто подал голос. Нужно, чтобы бот знал,
    кого тегать в шутках."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.chat.id not in _seen_chats:
            # Чтобы узнать ID группы прямо из лога, без /chatid.
            _seen_chats.add(event.chat.id)
            log.info("Чат %s (%s): %s", event.chat.id, event.chat.type,
                     event.chat.title or "личка")
        user = data.get("event_from_user")
        if isinstance(event, Message) and user and not user.is_bot:
            await db.upsert_player(user.id, user.full_name, user.username)
        return await handler(event, data)
