"""Интендант-собеседник на Gemini.

Отвечает, когда к нему обращаются: тег @бота, ответ на его сообщение или
сообщение, начинающееся с «Интендант», «Развлекатор» или «Бот». Помнит последние сообщения чата —
их собирает ChatLog (outer-middleware, видит всю переписку при выключенном
privacy mode).
"""

import html
import logging
import re
from collections import Counter, deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.types import Message, TelegramObject

from bot import config, db
from bot.content.loader import CONTENT_DIR
from bot.services import ai

log = logging.getLogger(__name__)
router = Router(name="ai_chat")

HISTORY = 30          # сколько последних сообщений чата видит модель
MAX_REPLY = 1500      # обрезка на случай, если модель разговорится

# Характер и правила собеседника — в отдельном файле, правится без кода.
PERSONA = (CONTENT_DIR / "persona.txt").read_text(encoding="utf-8").strip()


# --- Память чата -----------------------------------------------------------

_history: dict[int, deque[str]] = {}


def remember(chat_id: int, who: str, text: str) -> None:
    line = f"{who}: {text}".replace("\n", " ")[:500]
    _history.setdefault(chat_id, deque(maxlen=HISTORY)).append(line)


class ChatLog(BaseMiddleware):
    """Записывает каждое текстовое сообщение чата в память собеседника."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Команды (/slots, /w слово) — не реплики разговора, в память не идут.
        if (isinstance(event, Message) and event.text and event.from_user
                and not event.text.startswith("/")):
            remember(event.chat.id, event.from_user.first_name, event.text)
        return await handler(event, data)


# --- Лимиты ----------------------------------------------------------------

_used: Counter = Counter()  # (день, id чата) → ответов


def take_quota(chat_id: int) -> str | None:
    """Списывает один ответ из дневного лимита чата. None — можно отвечать.

    Лимита на одного человека нет: болтать с Интендантом можно сколько угодно,
    общий лимит держит запросы в бесплатной квоте Google.
    """
    key = (db._today(), chat_id)
    if _used[key] >= config.AI_PER_CHAT_DAY:
        return "Интендант на сегодня охрип — слишком много болтали. До завтра."
    _used[key] += 1
    return None


def refund_quota(chat_id: int) -> None:
    """Ответа не было — попытка не считается."""
    _used[(db._today(), chat_id)] -= 1


# --- Обращение к боту ------------------------------------------------------

CALL = re.compile(r"(?i)^\s*(интендант|развлекатор|бот)\b")


async def addressed_to_me(message: Message, bot: Bot) -> bool:
    if not message.text or message.text.startswith("/"):
        return False
    me = await bot.me()
    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == me.id:
        return True
    if me.username and f"@{me.username.lower()}" in message.text.lower():
        return True
    return bool(CALL.match(message.text))


def build_prompt(chat_id: int, asker: str) -> str:
    lines = "\n".join(_history.get(chat_id, ()))
    return (f"Последние сообщения чата (старые сверху):\n{lines}\n\n"
            f"Ответь на последнее сообщение от {asker}. Не повторяй его имя в начале.")


@router.message(F.text, addressed_to_me)
async def chat(message: Message, bot: Bot) -> None:
    refusal = take_quota(message.chat.id)
    if refusal:
        await message.reply(refusal)
        return

    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        answer = await ai.generate(PERSONA, build_prompt(message.chat.id,
                                                          message.from_user.first_name))
    except Exception as error:  # сеть, лимит Google, блокировка — чат не должен падать
        log.warning("Gemini не ответил: %s", error)
        refund_quota(message.chat.id)
        await message.reply("Интендант задумался и забыл, о чём речь. Попробуй позже.")
        return

    answer = answer[:MAX_REPLY]
    remember(message.chat.id, "Интендант", answer)
    await message.reply(html.escape(answer))
