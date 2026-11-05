import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("CLAN_CHAT_ID", "-1001234567890")

from bot import config, db  # noqa: E402
from bot.content.loader import load  # noqa: E402
from bot.content.reminders import REMINDERS  # noqa: E402
from bot.handlers.rpg import exp_to_next  # noqa: E402
from bot.services import scheduler  # noqa: E402


@pytest.fixture
async def clean_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    await db.init()


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


async def test_player_lifecycle(clean_db):
    await db.upsert_player(1, "Дмитрий", "dmitry")

    player = await db.get_player(1)
    assert player.name == "Дмитрий"
    assert (player.hp_max, player.atk, player.defense) == (
        config.START_HP, config.START_ATK, config.START_DEF)
    assert (player.level, player.exp) == (1, 0)
    assert player.energy == config.MAX_ENERGY


async def test_energy_spend_and_daily_reset(clean_db):
    await db.upsert_player(1, "Дмитрий", None)

    for _ in range(config.MAX_ENERGY):
        assert await db.spend_energy(1) is True
    assert await db.spend_energy(1) is False, "энергия ушла в минус"

    assert (await db.get_player(1)).energy == 0

    # Наступил новый день — энергия должна восстановиться при чтении.
    import aiosqlite

    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute("UPDATE players SET energy_date = '2000-01-01' WHERE tg_id = 1")
        await conn.commit()

    assert (await db.get_player(1)).energy == config.MAX_ENERGY


async def test_upsert_keeps_progress(clean_db):
    """Повторный вход не должен обнулять прокачку."""
    await db.upsert_player(1, "Дмитрий", "old_nick")

    import aiosqlite

    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute("UPDATE players SET level = 7, exp = 12, atk = 20 WHERE tg_id = 1")
        await conn.commit()

    await db.upsert_player(1, "Дмитрий", "new_nick")

    player = await db.get_player(1)
    assert (player.level, player.exp, player.atk) == (7, 12, 20)
    assert player.username == "new_nick"


def test_exp_thresholds():
    """Первый уровень — пара побед в первый же день, последний — месяц игры."""
    assert exp_to_next(1) == 2
    assert exp_to_next(19) > 1000
    thresholds = [exp_to_next(lvl) for lvl in range(1, 20)]
    assert thresholds == sorted(thresholds)


REMINDER_KEYS = sorted({r.key for r in REMINDERS})


def test_phrase_sets_are_big_enough():
    assert len(load("phrases", "jokes", "general.txt")) >= 300
    assert len(load("phrases", "jokes", "personal.txt")) >= 200
    for key in REMINDER_KEYS:
        assert len(load("phrases", "reminders", f"{key}.txt")) >= 25, key


@pytest.mark.parametrize("parts", [
    ("phrases", "jokes", "general.txt"),
    ("phrases", "jokes", "personal.txt"),
    *[("phrases", "reminders", f"{key}.txt") for key in REMINDER_KEYS],
])
def test_no_duplicate_phrases(parts):
    phrases = load(*parts)
    duplicates = {p for p in phrases if phrases.count(p) > 1}
    assert not duplicates, f"повторы в {'/'.join(parts)}: {duplicates}"


def test_personal_jokes_all_have_mention():
    for phrase in load("phrases", "jokes", "personal.txt"):
        assert "{mention}" in phrase, phrase
        assert phrase.format(mention="@кто-то").count("{") == 0


def test_general_jokes_have_no_placeholders():
    """В общих шутках подстановки нет — лишняя скобка выдала бы сырой шаблон в чат."""
    for phrase in load("phrases", "jokes", "general.txt"):
        assert "{" not in phrase and "}" not in phrase, phrase


def test_reminder_texts_are_formattable():
    """Каждый текст должен читаться при любом сроке своего события."""
    for reminder in REMINDERS:
        for text in reminder.texts:
            assert "{when}" in text, text
            filled = text.format(when=reminder.when)
            assert "{" not in filled and "}" not in filled, filled


def test_only_hydra_and_chimera_are_reminded():
    assert REMINDER_KEYS == ["chimera", "hydra"]


def test_reminder_schedule():
    """Оба события — за сутки и за 2 часа: Гидра до обновления в среду в 11:00
    МСК, Химера до закрытия боёв в четверг в 14:30 МСК."""
    schedule = {(r.key, r.weekday, r.at.hour, r.at.minute) for r in REMINDERS}
    assert schedule == {
        ("hydra", 1, 11, 0),
        ("hydra", 2, 9, 0),
        ("chimera", 2, 14, 30),
        ("chimera", 3, 12, 30),
    }


async def test_reminders_can_be_switched_off(clean_db, monkeypatch):
    """Выключатель должен убирать сами задачи, а не просто молчать в лог."""
    monkeypatch.setattr(config, "REMINDERS_ENABLED", False)

    bot = FakeBot()
    sched = await scheduler.setup(bot)
    try:
        assert [j for j in sched.get_jobs() if j.id.startswith("reminder_")] == []
        assert sched.get_job("plan_jokes") is not None, "шутки выключать не просили"
    finally:
        sched.shutdown(wait=False)


async def test_reminders_are_scheduled(clean_db, monkeypatch):
    monkeypatch.setattr(config, "REMINDERS_ENABLED", True)
    bot = FakeBot()
    sched = await scheduler.setup(bot)
    try:
        reminder_jobs = [j for j in sched.get_jobs() if j.id.startswith("reminder_")]
        assert len(reminder_jobs) == len(REMINDERS)
        assert sched.get_job("plan_jokes") is not None
    finally:
        sched.shutdown(wait=False)


async def test_reminder_jobs_fire_on_the_right_weekday(clean_db, monkeypatch):
    """Гидра — недельное событие: cron без дня недели слал бы её ежедневно."""
    monkeypatch.setattr(config, "REMINDERS_ENABLED", True)
    sched = await scheduler.setup(FakeBot())
    try:
        for reminder in REMINDERS:
            fields = {f.name: str(f) for f in sched.get_job(reminder.job_id).trigger.fields}
            assert fields["day_of_week"] == str(reminder.weekday), reminder.job_id
            assert fields["hour"] == str(reminder.at.hour), reminder.job_id
            assert fields["minute"] == str(reminder.at.minute), reminder.job_id
    finally:
        sched.shutdown(wait=False)


@pytest.mark.parametrize("reminder", REMINDERS, ids=lambda r: r.job_id)
async def test_reminder_sends_text(clean_db, reminder):
    bot = FakeBot()
    await scheduler._send_reminder(bot, reminder.job_id)

    chat_id, text = bot.sent[0]
    assert chat_id == config.CLAN_CHAT_ID
    assert reminder.when.lower() in text.lower()
    assert "{" not in text
    assert text[0].isupper(), text


async def test_joke_mentions_player(clean_db, monkeypatch):
    await db.upsert_player(1, "Дмитрий", "dmitry")
    monkeypatch.setattr(config, "JOKE_MENTION_CHANCE", 1.0)

    bot = FakeBot()
    await scheduler._send_joke(bot)

    assert "@dmitry" in bot.sent[0][1]


async def test_joke_mentions_player_without_username(clean_db, monkeypatch):
    await db.upsert_player(1, "Дмитрий", None)
    monkeypatch.setattr(config, "JOKE_MENTION_CHANCE", 1.0)

    bot = FakeBot()
    await scheduler._send_joke(bot)

    assert 'tg://user?id=1' in bot.sent[0][1]


async def test_restart_does_not_repeat_jokes(clean_db, monkeypatch):
    """Дневная норма шуток уже отправлена — перезапуск не должен слать их заново."""
    monkeypatch.setattr(config, "JOKES_PER_DAY", (2, 3))
    for _ in range(3):
        await db.mark_joke_sent()

    bot = FakeBot()
    sched = await scheduler.setup(bot)
    try:
        assert [j for j in sched.get_jobs() if j.id.startswith(scheduler.JOKE_JOB_PREFIX)] == []
    finally:
        sched.shutdown(wait=False)


async def test_two_or_three_jokes_a_day(clean_db, monkeypatch):
    """Норма плавающая: чат не должен угадывать, сколько раз бот пошутит."""
    monkeypatch.setattr(config, "JOKE_HOURS", (0, 24))  # окно на все сутки
    # Норма задаётся в .env; механику проверяем на фиксированных границах.
    monkeypatch.setattr(config, "JOKES_PER_DAY", (2, 3))

    counts = set()
    for _ in range(30):
        sched = await scheduler.setup(FakeBot())
        try:
            counts.add(len([j for j in sched.get_jobs()
                            if j.id.startswith(scheduler.JOKE_JOB_PREFIX)]))
        finally:
            sched.shutdown(wait=False)

    assert counts <= {2, 3}, counts
    assert counts == {2, 3}, "число шуток должно меняться день ото дня"


async def test_sent_joke_is_counted(clean_db):
    bot = FakeBot()
    assert await db.jokes_sent_today() == 0
    await scheduler._send_joke(bot)
    assert await db.jokes_sent_today() == 1


async def test_joke_works_on_empty_clan(clean_db, monkeypatch):
    """Без единого игрока адресную шутку слать не из кого — не должно падать."""
    monkeypatch.setattr(config, "JOKE_MENTION_CHANCE", 1.0)

    bot = FakeBot()
    await scheduler._send_joke(bot)

    assert "{mention}" not in bot.sent[0][1]
