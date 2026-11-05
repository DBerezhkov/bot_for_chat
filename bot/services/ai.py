"""Запрос к Gemini через REST — без отдельного SDK, на aiohttp из aiogram."""

import asyncio
import logging

import aiohttp

from bot import config

log = logging.getLogger(__name__)

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT = aiohttp.ClientTimeout(total=30)


class AIError(Exception):
    """Gemini не ответил: сеть, лимит, блокировка. Текст — для лога, не для чата."""


def _body(system: str, prompt: str) -> dict:
    return {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 1.0, "maxOutputTokens": 2048},
    }


def _text(data: dict) -> str:
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    if not text:
        raise AIError(f"пустой ответ: {str(data)[:300]}")
    return text


async def generate(system: str, prompt: str) -> str:
    """Один повтор при 503: бесплатную модель Google часто «перегружает» на секунду."""
    try:
        return await _generate_once(system, prompt)
    except AIError as error:
        if not str(error).startswith("HTTP 503"):
            raise
        log.info("Gemini перегружен, повторяю")
        await asyncio.sleep(2)
        return await _generate_once(system, prompt)


async def _generate_once(system: str, prompt: str) -> str:
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        async with session.post(
            URL.format(model=config.GEMINI_MODEL),
            headers={"x-goog-api-key": config.GEMINI_API_KEY},
            json=_body(system, prompt),
        ) as response:
            data = await response.json(content_type=None)
            if response.status != 200:
                raise AIError(f"HTTP {response.status}: {str(data)[:300]}")
    return _text(data)
