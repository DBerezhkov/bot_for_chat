"""Выгрузка рейтингов героев Raid с HellHades в bot/content/heroes.json.

Оценки 0–5 по видам контента, 0 — «не оценивался». Русские имена из
локализации игры берутся с raid.guide: в русском и английском списках героев
у одного героя одна и та же картинка, по ней и сводим. Запускать изредка, когда
выходят новые герои или HellHades пересматривает оценки, и коммитить результат:

    .venv/bin/python -m tools.fetch_heroes
"""

import html
import json
import re
import urllib.request

from bot.content.heroes import DATA_FILE

URL = "https://hellhades.com/wp-json/hh-api/v3/champions?mode=all"
GUIDE = "https://raid.guide/{lang}/shadow-legends/"

# Поля оценок, которые берём как есть. Остальное (картинки, id) боту не нужно.
RATINGS = (
    "overall_user", "clan_boss", "hydra", "chimera", "amius",
    "spider", "dragon", "fire_knight", "ice_golem",
    "spider_hard", "dragon_hard", "fire_knight_hard", "ice_golem_hard",
    "iron_twins", "sand_devil", "phantom_grove", "doom_tower", "wave_control",
    "kuldath", "agreth", "borgoth", "sorath", "iragoth", "grythion", "bommal",
    "astranyx", "leshun", "draugnell", "isheth", "maximoz", "tauraze", "mimic",
    "phrygius", "arena_rating", "dungeon_overall", "grim_forest_overall",
    "hardmode_overall",
)


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _guide(lang: str) -> dict[str, str]:
    """Картинка героя → его имя на этом языке."""
    page = _get(GUIDE.format(lang=lang)).decode("utf-8")
    names = {}
    for body in re.findall(rf'href="(?:https://raid\.guide)?/{lang}/shadow-legends/[^/"]+/"[^>]*>(.*?)</a>',
                           page, re.S):
        image = re.search(r"hero/([^\"]+?)\.jpg", body)
        name = re.search(r"<span>(.*?)</span>", body, re.S)
        if image and name:
            names[image.group(1)] = html.unescape(name.group(1).strip())
    return names


def _key(name: str) -> str:
    return re.sub(r"[^a-z]", "", html.unescape(name).lower())


def russian_names() -> dict[str, str]:
    """Английское имя (в виде _key) → русское."""
    ru, en = _guide("ru"), _guide("en")
    return {_key(en[image]): ru[image] for image in ru if image in en}


def compact(raw: dict, ru: dict[str, str]) -> dict:
    short, _, form = raw["shortname"].partition(" - ")
    return {
        "name": raw["champion"],
        "ru": ru.get(_key(raw["champion"]), ""),
        "short": short,
        "form": form,  # у мификов две формы: Base и Alt
        "rarity": raw["rarity"],
        "role": raw["role"],
        "affinity": raw["affinity_index"],
        "faction": raw["faction_index"],
        "ratings": {k: raw[k] for k in RATINGS if raw.get(k)},
        "arena_roles": raw.get("arena_roles") or [],
        "fw": [raw[k] for k in ("fw_primary", "fw_secondary", "fw_tertiary")
               if raw.get(k) and raw[k] != "None"],
    }


def main() -> None:
    champions = json.loads(_get(URL))["champions"]
    ru = russian_names()
    heroes = [compact(c, ru) for c in champions]
    DATA_FILE.write_text(
        json.dumps(heroes, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8")
    without_ru = sorted({h["name"] for h in heroes if not h["ru"]})
    print(f"Сохранено героев: {len(heroes)} → {DATA_FILE}")
    print(f"Без русского имени: {len(without_ru)} {without_ru}")


if __name__ == "__main__":
    main()
