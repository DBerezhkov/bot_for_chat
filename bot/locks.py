"""Блокировка на игрока для кнопок, которые читают и пишут его состояние.

aiogram обрабатывает нажатия параллельно, и двойной тап по «Уйти с добычей»
или по сундуку начислял опыт и находку дважды: оба нажатия читали одно и то же
состояние похода. Под блокировкой второе нажатие видит уже обновлённое.
"""

import asyncio
from collections.abc import Awaitable, Callable

from aiogram.types import CallbackQuery

_LOCKS: dict[int, asyncio.Lock] = {}

Handler = Callable[[CallbackQuery], Awaitable[None]]


def per_player(handler: Handler) -> Handler:
    # Сигнатура обёртки — ровно (callback): aiogram по ней решает, какие
    # аргументы передать, и в *args/**kwargs насыпал бы свои служебные.
    async def wrapper(callback: CallbackQuery) -> None:
        lock = _LOCKS.setdefault(callback.from_user.id, asyncio.Lock())
        async with lock:
            await handler(callback)

    wrapper.__name__ = handler.__name__
    wrapper.__doc__ = handler.__doc__
    return wrapper
