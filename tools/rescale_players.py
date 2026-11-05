"""Разовый пересчёт характеристик под новую шкалу статов.

Старая шкала (100 HP / 10 атаки / 5 защиты) с новыми монстрами делает игрока
неуязвимым. Скрипт пересобирает статы каждого игрока с нуля: базовые за уровень
плюс бонусы уже найденных предметов. Уровень, опыт и сами находки не трогает.

    .venv/bin/python -m tools.rescale_players
"""

import sqlite3
import sys

from bot import config
from bot.game.progression import stats_at
from bot.game.world import LOCATIONS

FINDINGS = {f.key: f for loc in LOCATIONS for f in loc.findings}


def main() -> None:
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row

    players = db.execute("SELECT tg_id, name, hp_max, atk, defense, level FROM players").fetchall()
    if not players:
        print("Игроков нет, пересчитывать нечего.")
        return

    for player in players:
        hp, atk, defense = stats_at(player["level"])

        for key, count in db.execute(
            "SELECT item_key, count FROM findings WHERE tg_id = ?", (player["tg_id"],)
        ):
            finding = FINDINGS.get(key)
            if finding is None:
                continue  # предмет выпилен из мира — бонус за него не начисляем
            taken = min(count, finding.limit)
            hp += finding.hp_bonus * taken
            if finding.stat == "atk":
                atk += finding.bonus * taken
            else:
                defense += finding.bonus * taken

        db.execute(
            "UPDATE players SET hp_max = ?, atk = ?, defense = ? WHERE tg_id = ?",
            (hp, atk, defense, player["tg_id"]),
        )
        print(f"{player['name']}: {player['hp_max']}/{player['atk']}/{player['defense']}"
              f" -> {hp}/{atk}/{defense} (уровень {player['level']})")

    db.commit()
    print(f"Готово, пересчитано игроков: {len(players)}")


if __name__ == "__main__":
    sys.exit(main())
