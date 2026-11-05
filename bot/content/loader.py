import random
from functools import lru_cache
from pathlib import Path

CONTENT_DIR = Path(__file__).parent


@lru_cache
def load(*parts: str) -> tuple[str, ...]:
    """Читает набор фраз: одна строка — одна фраза.

    Пустые строки и строки с # в начале пропускаются, чтобы наборы можно было
    делить на смысловые блоки прямо в файле.
    """
    path = CONTENT_DIR.joinpath(*parts)
    lines = path.read_text(encoding="utf-8").splitlines()
    phrases = tuple(
        line.strip() for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    )
    if not phrases:
        raise RuntimeError(f"Набор фраз пуст: {path}")
    return phrases


def pick(*parts: str) -> str:
    """Случайная фраза из набора."""
    return random.choice(load(*parts))
