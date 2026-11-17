"""Выгрузка рейтингов героев Raid с HellHades в bot/content/heroes.json.

Оценки 0–5 по видам контента, 0 — «не оценивался». Запускать изредка, когда
выходят новые герои или HellHades пересматривает оценки, и коммитить результат:

    .venv/bin/python -m tools.fetch_heroes
"""

import json
import urllib.request

from bot.content.heroes import DATA_FILE

URL = "https://hellhades.com/wp-json/hh-api/v3/champions?mode=all"

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


def compact(raw: dict) -> dict:
    short, _, form = raw["shortname"].partition(" - ")
    return {
        "name": raw["champion"],
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
    request = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        champions = json.load(response)["champions"]
    heroes = [compact(c) for c in champions]
    DATA_FILE.write_text(
        json.dumps(heroes, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8")
    print(f"Сохранено героев: {len(heroes)} → {DATA_FILE}")


if __name__ == "__main__":
    main()
