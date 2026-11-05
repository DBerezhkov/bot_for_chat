from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import aiosqlite

from bot import config
from bot.game.progression import exp_to_next

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    tg_id       INTEGER PRIMARY KEY,
    username    TEXT,
    name        TEXT NOT NULL,
    hp_max      INTEGER NOT NULL,
    atk         INTEGER NOT NULL,
    defense     INTEGER NOT NULL,
    level       INTEGER NOT NULL DEFAULT 1,
    exp         INTEGER NOT NULL DEFAULT 0,
    energy      INTEGER NOT NULL,
    energy_date TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS joke_log (
    day   TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS findings (
    tg_id    INTEGER NOT NULL,
    item_key TEXT NOT NULL,
    count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tg_id, item_key)
);

CREATE TABLE IF NOT EXISTS runs (
    tg_id INTEGER PRIMARY KEY,
    state TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_picks (
    chat_id INTEGER NOT NULL,
    day     TEXT NOT NULL,
    kind    TEXT NOT NULL,
    first   INTEGER NOT NULL,
    second  INTEGER,
    PRIMARY KEY (chat_id, day, kind)
);

CREATE TABLE IF NOT EXISTS fun_stats (
    chat_id INTEGER NOT NULL,
    tg_id   INTEGER NOT NULL,
    stat    TEXT NOT NULL,
    count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, tg_id, stat)
);

CREATE TABLE IF NOT EXISTS wallets (
    tg_id     INTEGER PRIMARY KEY,
    coins     INTEGER NOT NULL,
    bonus_day TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS duel_log (
    day   TEXT NOT NULL,
    tg_id INTEGER NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, tg_id)
);
"""


@dataclass
class Player:
    tg_id: int
    username: str | None
    name: str
    hp_max: int
    atk: int
    defense: int
    level: int
    exp: int
    energy: int
    energy_date: str


async def init() -> None:
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


def _today() -> str:
    return datetime.now(config.TZ).date().isoformat()


async def upsert_player(tg_id: int, name: str, username: str | None) -> None:
    """Регистрирует новичка либо освежает его имя. Характеристики не трогает."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO players (tg_id, username, name, hp_max, atk, defense,
                                 energy, energy_date, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tg_id) DO UPDATE SET username = excluded.username,
                                             name = excluded.name
            """,
            (
                tg_id, username, name,
                config.START_HP, config.START_ATK, config.START_DEF,
                config.MAX_ENERGY, _today(), datetime.now(config.TZ).isoformat(),
            ),
        )
        await db.commit()


async def get_player(tg_id: int) -> Player | None:
    """Отдаёт игрока, попутно восстанавливая энергию, если наступил новый день."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM players WHERE tg_id = ?", (tg_id,))).fetchone()
        if row is None:
            return None

        today = _today()
        if row["energy_date"] != today:
            await db.execute(
                "UPDATE players SET energy = ?, energy_date = ? WHERE tg_id = ?",
                (config.MAX_ENERGY, today, tg_id),
            )
            await db.commit()
            row = dict(row) | {"energy": config.MAX_ENERGY, "energy_date": today}

        return Player(
            tg_id=row["tg_id"], username=row["username"], name=row["name"],
            hp_max=row["hp_max"], atk=row["atk"], defense=row["defense"],
            level=row["level"], exp=row["exp"],
            energy=row["energy"], energy_date=row["energy_date"],
        )


async def spend_energy(tg_id: int) -> bool:
    """Списывает один поход. False — если энергия кончилась."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE players SET energy = energy - 1 WHERE tg_id = ? AND energy > 0",
            (tg_id,),
        )
        await db.commit()
        return cursor.rowcount > 0


async def all_players() -> list[Player]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM players")).fetchall()
        return [
            Player(
                tg_id=r["tg_id"], username=r["username"], name=r["name"],
                hp_max=r["hp_max"], atk=r["atk"], defense=r["defense"],
                level=r["level"], exp=r["exp"],
                energy=r["energy"], energy_date=r["energy_date"],
            )
            for r in rows
        ]


async def get_findings(tg_id: int) -> dict[str, int]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        rows = await (await db.execute(
            "SELECT item_key, count FROM findings WHERE tg_id = ?", (tg_id,)
        )).fetchall()
        return {key: count for key, count in rows}


async def add_finding(tg_id: int, item_key: str, stat: str,
                      bonus: int, hp_bonus: int) -> None:
    """Записывает находку и сразу вшивает её бонус в характеристики.

    Инвентаря нет: предмет существует только как счётчик и прибавка к статам.
    """
    if stat not in ("atk", "defense"):
        raise ValueError(f"Неизвестный стат: {stat}")

    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO findings (tg_id, item_key, count) VALUES (?, ?, 1)
            ON CONFLICT(tg_id, item_key) DO UPDATE SET count = count + 1
            """,
            (tg_id, item_key),
        )
        await db.execute(
            f"UPDATE players SET {stat} = {stat} + ?, hp_max = hp_max + ? WHERE tg_id = ?",
            (bonus, hp_bonus, tg_id),
        )
        await db.commit()


async def add_exp(tg_id: int, amount: int) -> int:
    """Начисляет опыт и поднимает уровни, пока хватает. Возвращает, сколько взято.

    Порог считает progression.exp_to_next; за уровень персонаж получает
    +LEVEL_HP здоровья, +LEVEL_ATK атаки и +1 защиты через уровень.
    """
    async with aiosqlite.connect(config.DB_PATH) as db:
        row = await (await db.execute(
            "SELECT level, exp FROM players WHERE tg_id = ?", (tg_id,)
        )).fetchone()
        if row is None:
            return 0

        level, exp = row[0], row[1] + amount
        taken = []
        while exp >= exp_to_next(level):
            exp -= exp_to_next(level)
            level += 1
            taken.append(level)

        gained = len(taken)
        defense_up = sum(1 for lvl in taken if lvl % config.LEVEL_DEF_EVERY == 0)

        await db.execute(
            """
            UPDATE players SET level = ?, exp = ?,
                   hp_max = hp_max + ?, atk = atk + ?, defense = defense + ?
             WHERE tg_id = ?
            """,
            (level, exp, gained * config.LEVEL_HP, gained * config.LEVEL_ATK,
             defense_up, tg_id),
        )
        await db.commit()
        return gained


async def save_run(tg_id: int, state: str) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "INSERT INTO runs (tg_id, state) VALUES (?, ?) "
            "ON CONFLICT(tg_id) DO UPDATE SET state = excluded.state",
            (tg_id, state),
        )
        await db.commit()


async def load_run(tg_id: int) -> str | None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        row = await (await db.execute(
            "SELECT state FROM runs WHERE tg_id = ?", (tg_id,)
        )).fetchone()
        return row[0] if row else None


async def drop_run(tg_id: int) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("DELETE FROM runs WHERE tg_id = ?", (tg_id,))
        await db.commit()


async def duels_today(tg_id: int) -> int:
    async with aiosqlite.connect(config.DB_PATH) as db:
        row = await (await db.execute(
            "SELECT count FROM duel_log WHERE day = ? AND tg_id = ?", (_today(), tg_id)
        )).fetchone()
        return row[0] if row else 0


async def mark_duel(tg_id: int) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO duel_log (day, tg_id, count) VALUES (?, ?, 1)
            ON CONFLICT(day, tg_id) DO UPDATE SET count = count + 1
            """,
            (_today(), tg_id),
        )
        await db.commit()


async def jokes_sent_today() -> int:
    async with aiosqlite.connect(config.DB_PATH) as db:
        row = await (await db.execute(
            "SELECT count FROM joke_log WHERE day = ?", (_today(),)
        )).fetchone()
        return row[0] if row else 0


async def mark_joke_sent() -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO joke_log (day, count) VALUES (?, 1)
            ON CONFLICT(day) DO UPDATE SET count = count + 1
            """,
            (_today(),),
        )
        await db.commit()


def next_reset(now: datetime | None = None) -> str:
    """Сколько осталось до восстановления энергии — строкой вида «8 ч. 12 мин.»."""
    now = now or datetime.now(config.TZ)
    tomorrow = datetime.combine(date.fromordinal(now.date().toordinal() + 1),
                                datetime.min.time(), tzinfo=config.TZ)
    left = tomorrow - now
    hours, minutes = divmod(int(left.total_seconds()) // 60, 60)
    return f"{hours} ч. {minutes} мин."


# --- Мини-игры чата -------------------------------------------------------


async def get_pick(chat_id: int, kind: str) -> tuple[int, int | None] | None:
    """Сегодняшний выбор дня («герой», «пара»…) в этом чате, если уже сделан."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        row = await (await db.execute(
            "SELECT first, second FROM daily_picks WHERE chat_id = ? AND day = ? AND kind = ?",
            (chat_id, _today(), kind),
        )).fetchone()
        return (row[0], row[1]) if row else None


async def save_pick(chat_id: int, kind: str, first: int,
                    second: int | None = None) -> tuple[int, int | None]:
    """Фиксирует выбор дня. Если кто-то успел раньше — остаётся тот, первый."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO daily_picks (chat_id, day, kind, first, second) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, _today(), kind, first, second),
        )
        await db.commit()
    return await get_pick(chat_id, kind)


async def pick_counts(chat_id: int, kind: str) -> list[tuple[int, int]]:
    """Сколько раз каждый становился «тем самым» за всё время, по убыванию."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        rows = await (await db.execute(
            "SELECT first, COUNT(*) AS n FROM daily_picks WHERE chat_id = ? AND kind = ? "
            "GROUP BY first ORDER BY n DESC, first",
            (chat_id, kind),
        )).fetchall()
        return [(r[0], r[1]) for r in rows]


async def add_stat(chat_id: int, tg_id: int, stat: str, amount: int = 1) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO fun_stats (chat_id, tg_id, stat, count) VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id, tg_id, stat) DO UPDATE SET count = count + excluded.count
            """,
            (chat_id, tg_id, stat, amount),
        )
        await db.commit()


async def top_stat(chat_id: int, stat: str, limit: int = 10) -> list[tuple[int, int]]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        rows = await (await db.execute(
            "SELECT tg_id, count FROM fun_stats WHERE chat_id = ? AND stat = ? AND count > 0 "
            "ORDER BY count DESC, tg_id LIMIT ?",
            (chat_id, stat, limit),
        )).fetchall()
        return [(r[0], r[1]) for r in rows]


# --- Монеты ---------------------------------------------------------------
# Кошелёк один на человека, не на чат: бот живёт в одном клан-чате.

START_COINS = 100


async def _ensure_wallet(db, tg_id: int) -> None:
    await db.execute("INSERT OR IGNORE INTO wallets (tg_id, coins) VALUES (?, ?)",
                     (tg_id, START_COINS))


async def balance(tg_id: int) -> int:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await _ensure_wallet(db, tg_id)
        await db.commit()
        row = await (await db.execute("SELECT coins FROM wallets WHERE tg_id = ?",
                                      (tg_id,))).fetchone()
        return row[0]


async def add_coins(tg_id: int, amount: int) -> int:
    """Начисляет (или, с минусом, безусловно списывает). Возвращает новый баланс."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        await _ensure_wallet(db, tg_id)
        await db.execute("UPDATE wallets SET coins = coins + ? WHERE tg_id = ?",
                         (amount, tg_id))
        await db.commit()
        row = await (await db.execute("SELECT coins FROM wallets WHERE tg_id = ?",
                                      (tg_id,))).fetchone()
        return row[0]


async def spend_coins(tg_id: int, amount: int) -> bool:
    """Списывает, только если хватает. Одним запросом — двойное нажатие
    не уведёт баланс в минус."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        await _ensure_wallet(db, tg_id)
        cursor = await db.execute(
            "UPDATE wallets SET coins = coins - ? WHERE tg_id = ? AND coins >= ?",
            (amount, tg_id, amount),
        )
        await db.commit()
        return cursor.rowcount > 0


async def claim_daily(tg_id: int, amount: int) -> bool:
    """Ежедневный бонус. False — сегодня уже забирал."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        await _ensure_wallet(db, tg_id)
        cursor = await db.execute(
            "UPDATE wallets SET coins = coins + ?, bonus_day = ? "
            "WHERE tg_id = ? AND bonus_day != ?",
            (amount, _today(), tg_id, _today()),
        )
        await db.commit()
        return cursor.rowcount > 0


async def richest(limit: int = 10) -> list[tuple[int, int]]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        rows = await (await db.execute(
            "SELECT tg_id, coins FROM wallets ORDER BY coins DESC, tg_id LIMIT ?", (limit,)
        )).fetchall()
        return [(r[0], r[1]) for r in rows]
