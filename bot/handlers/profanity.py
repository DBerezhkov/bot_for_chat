"""Цензор: на мат в чате Интендант жёстко одёргивает автора.

Работает как outer-middleware: отвечает на сообщение с матом и пропускает
его дальше, поэтому крокодил, вордл и собеседник продолжают работать.

Мат ищется по корням в каждом слове. Перед проверкой текст нормализуется:
ё→е, латинские двойники букв (x, y, e, o…) → кириллица, повторы букв
схлопываются («бляяя» → «бля»). Корни подобраны так, чтобы не цеплять
обычные слова: «оглобля», «корабля», «страхую», «хлебать», «дебаты».
"""

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from bot.content.loader import pick

log = logging.getLogger(__name__)

_LOOKALIKES = str.maketrans({
    "ё": "е", "a": "а", "c": "с", "e": "е", "k": "к", "m": "м",
    "o": "о", "p": "р", "t": "т", "x": "х", "y": "у",
    "0": "о", "3": "з", "6": "б",
})

_MAT = re.compile(
    r"ху[йяеию]"                         # хуй, хуёвый, нахуя, хуе…
    r"|пизд"
    r"|^бля"                              # в начале: блядь, блять, бля
    r"|(?:^|[аоуыиеяъ])еб(?:а[лнтшк]|ну|н[иу]|уч|л[аоия]|ет|ен|ис|ыв|ок|ук|ищ|он)"
    r"|^еб$|долбо[её]?б"
    r"|пид[оа]р|^пидр"
    r"|муд[аои][кл]|мудач"
    r"|залуп|гандон|шлюх"
    r"|^сук[аиеу]$|^сукин"
)
# Слова, где корень встречается законно.
_SAFE = re.compile(r"страху|штриху|^блях")


def _normalize(text: str) -> list[str]:
    text = text.lower().translate(_LOOKALIKES)
    words = re.findall(r"[а-я]+", text)
    # Схлопываем повторы: «хууууй» → «хуй».
    return [re.sub(r"(.)\1+", r"\1", w) for w in words]


def has_mat(text: str) -> bool:
    return any(_MAT.search(w) and not _SAFE.search(w) for w in _normalize(text))


class MatGuard(BaseMiddleware):
    """Отвечает на сообщения с матом жёстким замечанием."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user and not event.from_user.is_bot:
            text = event.text or event.caption
            if text and has_mat(text):
                try:
                    await event.reply(pick("phrases", "mat", "scold.txt"))
                except Exception:  # нет прав писать — не мешаем остальным
                    log.exception("Не удалось одёрнуть матерщинника")
        return await handler(event, data)
