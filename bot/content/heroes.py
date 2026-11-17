"""Справка по героям Raid для собеседника: рейтинги HellHades (heroes.json).

Ищет упомянутых в тексте героев — по английскому имени, короткому имени
(Siphi вместо Siphi the Lost Bride) или русскому прозвищу из hero_aliases.txt —
и отдаёт их оценки строками для промпта. Данные обновляет tools/fetch_heroes.py.
"""

import json
import re
from collections.abc import Iterable
from functools import lru_cache

from bot.content.loader import CONTENT_DIR, load

DATA_FILE = CONTENT_DIR / "heroes.json"
MAX_HEROES = 5  # больше в промпт не тащим: дорого и модель путается

LABELS = {
    "overall_user": "общая", "clan_boss": "Клан-босс", "hydra": "Гидра",
    "chimera": "Химера", "amius": "Amius", "spider": "Паук", "dragon": "Дракон",
    "fire_knight": "Огненный рыцарь", "ice_golem": "Ледяной голем",
    "spider_hard": "Паук хард", "dragon_hard": "Дракон хард",
    "fire_knight_hard": "Огненный рыцарь хард", "ice_golem_hard": "Ледяной голем хард",
    "iron_twins": "Железные близнецы", "sand_devil": "Песчаный дьявол",
    "phantom_grove": "Призрачная роща", "doom_tower": "Роковая башня",
    "wave_control": "волны Роковой башни", "arena_rating": "Арена",
    "dungeon_overall": "подземелья в целом", "grim_forest_overall": "Мрачный лес",
    "hardmode_overall": "хард-режимы в целом",
}


@lru_cache
def _heroes() -> dict[str, list[dict]]:
    """Имя героя → его записи (у мификов две формы)."""
    by_name: dict[str, list[dict]] = {}
    for hero in json.loads(DATA_FILE.read_text(encoding="utf-8")):
        by_name.setdefault(hero["name"], []).append(hero)
    return by_name


def _stem(key: str) -> str:
    """Основа русского слова для склонений: «лидия» → «лиди» (лидию, лидией)."""
    if re.search("[а-яё]", key) and len(key) > 4 and key[-1] in "аяоеиыуюйь":
        return key[:-1]
    return key


def _pattern(keys) -> re.Pattern:
    # Длинные ключи первыми, чтобы «Siphi the Lost Bride» не съелось как «Siphi».
    # Русским словам разрешены окончания (Арбитра, Лидию), английским — нет.
    parts = [re.escape(_stem(k)) + (r"\w{0,3}" if re.search("[а-яё]", k) else "")
             for k in sorted(keys, key=len, reverse=True)]
    return re.compile(r"(?<!\w)(" + "|".join(parts) + r")(?!\w)", re.IGNORECASE)


@lru_cache
def _matcher() -> tuple[re.Pattern, dict[str, str]]:
    keys: dict[str, str] = {}  # ключ поиска в нижнем регистре → имя героя
    for name, forms in _heroes().items():
        for key in (name, name.removeprefix("The "), forms[0]["short"]):
            if len(key) >= 3:
                keys.setdefault(key.lower(), name)
    for line in load("hero_aliases.txt"):
        alias, _, name = (part.strip() for part in line.partition("="))
        if name not in _heroes():
            raise RuntimeError(f"hero_aliases.txt: нет героя {name!r}")
        keys[alias.lower()] = name
    return _pattern(keys), keys


def _resolve(word: str, keys: dict[str, str]) -> str:
    """Ключ, на который пришлось совпадение; у русского слова — по основе."""
    word = word.lower()
    return keys.get(word) or keys[max((k for k in keys if word.startswith(_stem(k))), key=len)]


def known(name: str) -> bool:
    return name in _heroes()


def find(text: str, extra: Iterable[str] = ()) -> list[str]:
    """Герои, упомянутые в тексте, в порядке появления, без повторов.

    extra — дополнительные «герои» без записи в heroes.json: слова, о которых
    клан оставил заметки (например, герой, которого HellHades ещё не оценил).
    """
    pattern, keys = _matcher()
    hits = [(m.start(), _resolve(m.group(1), keys)) for m in pattern.finditer(text)]
    extra_keys = {k.lower(): k for k in extra if k not in _heroes()}
    if extra_keys:
        hits += [(m.start(), _resolve(m.group(1), extra_keys))
                 for m in _pattern(extra_keys).finditer(text)]
    found: list[str] = []
    for _, name in sorted(hits):
        if name not in found:
            found.append(name)
    return found[:MAX_HEROES]


def split_name(text: str) -> tuple[str, str]:
    """«Лидия топовый разломщик» → ("Lydia the Deathsiren", "топовый разломщик").

    Если героя нет в базе, именем считается первое слово.
    """
    pattern, keys = _matcher()
    match = pattern.match(text.strip())
    if match:
        return _resolve(match.group(1), keys), text.strip()[match.end():]
    word, _, rest = text.strip().partition(" ")
    return word.strip(",.:;!—-").lower(), rest


def _num(value: float) -> str:
    return f"{value:g}"


def describe(name: str) -> str:
    """Одна-две строки справки по герою для промпта."""
    lines = []
    for hero in _heroes()[name]:
        title = name + (f" (форма {hero['form']})" if hero["form"] else "")
        meta = ", ".join((hero["rarity"], hero["role"], hero["affinity"], hero["faction"]))
        ratings = "; ".join(f"{LABELS.get(k, k.capitalize())} {_num(v)}"
                            for k, v in hero["ratings"].items())
        extra = []
        if hero["arena_roles"]:
            extra.append("роли на Арене: " + ", ".join(hero["arena_roles"]))
        if hero["fw"]:
            extra.append("Войны фракций: " + ", ".join(hero["fw"]))
        lines.append(f"{title} [{meta}]: {ratings}" + "".join(f"; {e}" for e in extra))
    return "\n".join(lines)
