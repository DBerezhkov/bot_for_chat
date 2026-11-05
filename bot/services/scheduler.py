import html
import logging
import random
from datetime import datetime, timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from bot import config, db
from bot.content.loader import load
from bot.content.reminders import REMINDERS

log = logging.getLogger(__name__)

JOKE_JOB_PREFIX = "joke_"


async def _send_reminder(bot: Bot, job_id: str) -> None:
    reminder = next(r for r in REMINDERS if r.job_id == job_id)
    text = random.choice(reminder.texts).format(when=reminder.when)
    # Часть фраз начинается прямо с {when} — без этого выходит «через 2 часа
    # обнуление Гидры» со строчной буквы в начале сообщения.
    text = text[0].upper() + text[1:]
    await bot.send_message(config.CLAN_CHAT_ID, text)


def _mention(player: db.Player) -> str:
    if player.username:
        return f"@{player.username}"
    return f'<a href="tg://user?id={player.tg_id}">{html.escape(player.name)}</a>'


async def _send_joke(bot: Bot) -> None:
    players = await db.all_players()
    personal = players and random.random() < config.JOKE_MENTION_CHANCE

    if personal:
        text = random.choice(load("phrases", "jokes", "personal.txt")).format(
            mention=_mention(random.choice(players))
        )
    else:
        text = random.choice(load("phrases", "jokes", "general.txt"))

    await bot.send_message(config.CLAN_CHAT_ID, text)
    await db.mark_joke_sent()


async def _plan_jokes(bot: Bot, scheduler: AsyncIOScheduler) -> None:
    """Расставляет шутки на сегодня по случайным моментам внутри окна.

    Считает уже отправленные за сутки, иначе перезапуск бота днём завалил бы
    чат повтором тех шуток, что отряд уже слышал.
    """
    for job in scheduler.get_jobs():
        if job.id.startswith(JOKE_JOB_PREFIX):
            job.remove()

    today = random.randint(*config.JOKES_PER_DAY)
    left = today - await db.jokes_sent_today()
    if left <= 0:
        return

    now = datetime.now(config.TZ)
    hour_from, hour_to = config.JOKE_HOURS
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Планируем только вперёд: иначе перезапуск в середине окна съел бы часть
    # нормы — моменты, выпавшие на уже прошедшие часы, просто терялись бы.
    start = max(now, midnight + timedelta(hours=hour_from))
    end = midnight + timedelta(hours=hour_to)
    if start >= end:
        return

    window = int((end - start).total_seconds() // 60)

    for i in range(left):
        at = start + timedelta(minutes=random.randint(0, window))
        scheduler.add_job(
            _send_joke, DateTrigger(run_date=at), args=[bot],
            id=f"{JOKE_JOB_PREFIX}{i}", replace_existing=True,
        )
        log.info("Шутка запланирована на %s", at.strftime("%H:%M"))


async def setup(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=config.TZ)

    if config.CLAN_CHAT_ID is None:
        log.warning("CLAN_CHAT_ID не задан — напоминалки и шутки отключены. "
                    "Позовите бота в группу и выполните там /chatid.")
        scheduler.start()
        return scheduler

    for reminder in (REMINDERS if config.REMINDERS_ENABLED else ()):
        scheduler.add_job(
            _send_reminder,
            CronTrigger(
                day_of_week=reminder.weekday,
                hour=reminder.at.hour,
                minute=reminder.at.minute,
                timezone=config.TZ,
            ),
            args=[bot, reminder.job_id],
            id=reminder.job_id,
        )

    if not config.REMINDERS_ENABLED:
        log.warning("Напоминалки выключены (REMINDERS_ENABLED=false). Шутки работают.")

    scheduler.add_job(
        _plan_jokes, CronTrigger(hour=0, minute=1, timezone=config.TZ),
        args=[bot, scheduler], id="plan_jokes",
    )

    scheduler.start()
    await _plan_jokes(bot, scheduler)
    return scheduler
