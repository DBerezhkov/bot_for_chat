import html
import logging
import random
import re
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from bot import config, db
from bot.content.loader import load
from bot.content.reminders import REMINDERS

log = logging.getLogger(__name__)

JOKE_JOB_PREFIX = "joke_"
UNPIN_JOB_PREFIX = "unpin_"


async def _send_reminder(bot: Bot, scheduler: AsyncIOScheduler, job_id: str) -> None:
    reminder = next(r for r in REMINDERS if r.job_id == job_id)
    # Часть фраз начинает с {when} предложение — в начале сообщения или после
    # «!» и «.». Там срок нужен с заглавной, иначе выходит «Лентяи! через
    # полчаса Химера закроется».
    when = reminder.when
    text = re.sub(r"(^|[.!?…]\s+)\{when\}",
                  lambda m: m.group(1) + when[0].upper() + when[1:],
                  random.choice(reminder.texts))
    text = text.format(when=when)

    if reminder.pin_minutes:
        await pin_for(bot, scheduler, text, timedelta(minutes=reminder.pin_minutes))
        return

    await bot.send_message(config.CLAN_CHAT_ID, text + "\n\n" + await _mention_all())


async def _mention_all() -> str:
    # В Telegram нет @all: «тегнуть всех» — значит упомянуть каждого, кого
    # бот видел в чате.
    return " ".join(_mention(p) for p in await db.all_players())


async def _unpin(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.unpin_chat_message(chat_id, message_id=message_id)
    except TelegramAPIError as e:
        # Уже открепили руками или права отобрали — повторять незачем.
        log.warning("Не удалось открепить сообщение %s: %s", message_id, e)
    await db.drop_pending_unpin(chat_id, message_id)


def _schedule_unpin(bot: Bot, scheduler: AsyncIOScheduler,
                    chat_id: int, message_id: int, at: datetime) -> None:
    scheduler.add_job(
        _unpin, DateTrigger(run_date=max(at, datetime.now(config.TZ))),
        args=[bot, chat_id, message_id],
        id=f"{UNPIN_JOB_PREFIX}{chat_id}_{message_id}", replace_existing=True,
    )


async def pin_for(bot: Bot, scheduler: AsyncIOScheduler, text: str,
                  ttl: timedelta) -> None:
    """Пишет в клан-чат, закрепляет с уведомлением всем и через ttl открепляет.

    Без права «Закреплять сообщения» закреп не выйдет — сообщение остаётся
    обычным, без тегов: тегать всех в последнем напоминании не просили.
    """
    chat_id = config.CLAN_CHAT_ID
    message = await bot.send_message(chat_id, text)
    try:
        await bot.pin_chat_message(chat_id, message.message_id,
                                   disable_notification=False)
    except TelegramAPIError as e:
        log.warning("Не удалось закрепить напоминание (нет прав?): %s", e)
        return

    unpin_at = datetime.now(config.TZ) + ttl
    await db.add_pending_unpin(chat_id, message.message_id, unpin_at)
    _schedule_unpin(bot, scheduler, chat_id, message.message_id, unpin_at)


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
            args=[bot, scheduler, reminder.job_id],
            id=reminder.job_id,
        )

    # Закрепы, которые не успели открепить до перезапуска.
    for chat_id, message_id, at in await db.pending_unpins():
        _schedule_unpin(bot, scheduler, chat_id, message_id, at)

    if not config.REMINDERS_ENABLED:
        log.warning("Напоминалки выключены (REMINDERS_ENABLED=false). Шутки работают.")

    scheduler.add_job(
        _plan_jokes, CronTrigger(hour=0, minute=1, timezone=config.TZ),
        args=[bot, scheduler], id="plan_jokes",
    )

    scheduler.start()
    await _plan_jokes(bot, scheduler)
    return scheduler
