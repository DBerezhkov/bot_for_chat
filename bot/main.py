import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from bot import config, db
# RPG (походы, дуэли, профиль) выключена: код лежит в bot/game и
# bot/handlers/{rpg,raid,duel}.py, вернуть — подключить роутеры обратно.
from bot.handlers import ai_chat, coins, common, crocodile, dice, fun, quiz, roulette, wordle
from bot.middlewares import RegisterPlayer
from bot.services import scheduler

COMMANDS = [
    BotCommand(command="hero", description="Герой дня"),
    BotCommand(command="clown", description="Клоун дня"),
    BotCommand(command="who", description="Кто сегодня… (напиши вопрос после команды)"),
    BotCommand(command="predict", description="Гадание на день (или просто напиши «гадание»)"),
    BotCommand(command="croc", description="Крокодил: объясни слово, не называя"),
    BotCommand(command="wordle", description="Вордл: угадать слово из 5 букв"),
    BotCommand(command="w", description="Догадка в вордле: /w слово"),
    BotCommand(command="quiz", description="Вопрос викторины"),
    BotCommand(command="roulette", description="Русская рулетка"),
    BotCommand(command="roll", description="Кости: /roll или /roll 50 на монеты"),
    BotCommand(command="slots", description="Слоты: /slots или /slots 20 на монеты"),
    BotCommand(command="daily", description="Ежедневные монеты"),
    BotCommand(command="balance", description="Сколько у меня монет"),
    BotCommand(command="give", description="Перевести монеты (ответом на сообщение)"),
    BotCommand(command="rich", description="Самые богатые"),
    BotCommand(command="top", description="Доска почёта"),
    BotCommand(command="init", description="Перепись отряда"),
    BotCommand(command="commands", description="Список команд и что они делают"),
    BotCommand(command="help", description="Что я умею"),
]


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    await db.init()

    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.set_my_commands(COMMANDS)

    dp = Dispatcher()
    # outer: срабатывает на каждое сообщение, а не только на пойманные обработчиком.
    dp.message.outer_middleware(RegisterPlayer())
    # Крокодил последним: он ловит обычные сообщения с догадками.
    dp.include_routers(common.router, coins.router, fun.router, dice.router,
                       roulette.router, quiz.router, wordle.router)
    # Собеседник — до крокодила: обращение к боту не должно уйти в догадки.
    if config.GEMINI_API_KEY:
        dp.message.outer_middleware(ai_chat.ChatLog())
        dp.include_router(ai_chat.router)
        logging.info("Собеседник включён, модель %s", config.GEMINI_MODEL)
    else:
        logging.warning("GEMINI_API_KEY не задан — собеседник выключен.")
    dp.include_router(crocodile.router)

    await scheduler.setup(bot)

    logging.info("Интендант заступил на пост")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
