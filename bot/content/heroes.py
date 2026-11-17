"""Справка по героям Raid для собеседника: рейтинги HellHades (heroes.json).

Ищет упомянутых в тексте героев — по английскому имени, короткому имени
(Siphi вместо Siphi the Lost Bride), русскому имени из локализации игры
(Людоед, Лидия Восставшая или просто Лидия) или прозвищу из hero_aliases.txt —
и отдаёт их оценки строками для промпта. Данные обновляет tools/fetch_heroes.py.
"""

import json
import re
from collections.abc import Iterable
from difflib import SequenceMatcher
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


# Падежные окончания после основы русского имени — по типу склонения,
# чтобы «Перезвон» не ловил «перезвоню», а «Синдра» — «синдром».
_FEM = r"(?:а|я|ы|и|е|у|ю|ой|ей|ою|ею)"           # Лидия → Лидию, Лидией
_SOFT = r"(?:ь|я|ю|е|ем|и|ей)"                      # Каэль → Каэля, Каэлем
_HARD = r"(?:а|у|е|ом|ы|ов|ам|ами|ах)?"             # Арбитр → Арбитра, Арбитром


def _stem(key: str) -> str:
    """Основа русского слова для склонений: «лидия» → «лиди» (лидию, лидией)."""
    return _inflect(key)[0]


def _inflect(key: str) -> tuple[str, str]:
    """(основа, разрешённые окончания) для ключа поиска."""
    if not _cyrillic(key) or len(key) < 5:
        return key, ""
    if key[-1] in "ая":
        return key[:-1], _FEM
    if key[-1] == "ь":
        return key[:-1], _SOFT
    if key[-1] not in "аяоеиыуюйёэ":
        return key, _HARD
    return key, ""


def _cyrillic(key: str) -> bool:
    return bool(re.search("[а-яё]", key))


def _pattern(keys) -> re.Pattern:
    # Длинные ключи первыми, чтобы «Siphi the Lost Bride» не съелось как «Siphi».
    # Русским словам от 5 букв разрешены падежные окончания, остальным — нет.
    parts = [re.escape(stem) + endings for stem, endings in
             (_inflect(k) for k in sorted(keys, key=len, reverse=True))]
    return re.compile(r"(?<!\w)(" + "|".join(parts) + r")(?!\w)", re.IGNORECASE)


# Однословные русские имена, совпадающие с обычными словами чата.
_COMMON_WORDS = {
    "один", "мать", "сестра", "тень", "дева", "море", "мороз", "зверь", "игла",
    "мина", "рея", "ария", "мари", "энда", "финал", "фильм", "сигнал", "принцип",
    "визит", "беседа", "жалоба", "зависть", "жалость", "оборона", "оборот",
    "турнир", "катер", "каток", "валун", "вальс", "валюта", "морс", "мораль",
    "морда", "моряк", "посол", "принц", "свидетель", "синдром", "телец", "тиран",
    "трибунал", "финансы", "фильтр", "хозяин", "царица", "чучело", "ярлык",
    "убийца", "хищник", "таран", "венера", "большое",
    # Титулы в начале составных имён: «Капитан Темила», «Страж Святилища».
    "капитан", "страж", "кузнец", "королева", "король", "хранитель", "старшина",
    "советник", "смотритель", "сержант", "священник", "епископ", "друид",
    "вожак", "ловец", "мачеха", "ведунья", "колдунья", "герцогиня", "графиня",
    "баронесса", "грешник", "трибун", "адмирал", "маньяк", "крошка", "стратег",
    # Заимствования, которые в чате чаще просто слова.
    "командир", "ветеран", "снайпер", "скелет", "фанатик", "аристократ",
    "гладиатор", "лучница", "мародер", "авангард", "пантера", "магистр",
}

# Ниже эпика однословные русские имена — обычно нарицательные («Палач»,
# «Страж», «Лекарь»): в чате это почти всегда просто слово, а не герой.
# Исключение — имена собственные: их не переводили, а транслитерировали
# (Kael → Каэль), это и проверяем по звучанию.
_LOW_RARITY = {"Common", "Uncommon", "Rare"}

_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l",
                      "m", "n", "o", "p", "r", "s", "t", "u", "f", "h", "ts", "ch", "sh",
                      "sh", "", "i", "", "e", "iu", "ia"]))


def _transliterated(ru: str, en: str) -> bool:
    latin = "".join(_TRANSLIT.get(ch, ch) for ch in ru.lower())
    return SequenceMatcher(None, latin, en.lower()).ratio() >= 0.6


@lru_cache
def _matcher() -> tuple[re.Pattern, dict[str, str]]:
    keys: dict[str, str] = {}  # ключ поиска в нижнем регистре → имя героя
    first_words: dict[str, set[str]] = {}
    for name, forms in _heroes().items():
        ru = forms[0].get("ru", "")
        for key in (name, name.removeprefix("The "), forms[0]["short"]):
            if len(key) >= 3:
                keys.setdefault(key.lower(), name)
        single = ru and " " not in ru
        generic = single and (ru.lower() in _COMMON_WORDS or (
            forms[0]["rarity"] in _LOW_RARITY and not _transliterated(ru, name)))
        if ru and not generic:
            keys.setdefault(ru.lower(), name)
        if " " in ru:  # «Лидия Восставшая» ищем и просто как «Лидия»
            first_words.setdefault(ru.split()[0].lower(), set()).add(name)
    for word, names in first_words.items():
        # «Леди», «Король» — у многих героев, такие слова не берём.
        # Прилагательное в начале («Золотая Жница») — не имя.
        adjective = re.search(r"(ая|яя|ое|ее|ый|ий|ой|ые|ие)$", word)
        if len(names) == 1 and len(word) >= 4 and word not in _COMMON_WORDS and not adjective:
            keys.setdefault(word, next(iter(names)))
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
        title = name + (f" / {hero['ru']}" if hero.get("ru") else "")
        title += f" (форма {hero['form']})" if hero["form"] else ""
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
