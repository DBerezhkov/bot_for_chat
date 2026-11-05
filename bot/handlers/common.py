import html

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from bot import db

router = Router(name="common")

HELP = (
    "<b>Интендант</b>\n\n"
    "Я веду учёт отряда: напоминаю про Гидру и Химеру, иногда язвлю "
    "и развлекаю, чтобы вы тут не закисли.\n\n"
    "<b>Каждый день:</b>\n"
    "/hero — 🏅 герой дня\n"
    "/clown — 🤡 клоун дня\n"
    "/predict или слово «гадание» — 🔮 гадание на день лично тебе\n"
    "/who <i>вопрос</i> — 🤔 кто сегодня… (пример: /who сливает ключи)\n\n"
    "<b>Игры:</b>\n"
    "/croc — 🐊 крокодил: ведущий объясняет, чат угадывает (+15 🪙)\n"
    "/wordle — 🟩 вордл: слово из 5 букв, догадка — /w слово\n"
    "/quiz — 🧠 вопрос викторины, 30 секунд на ответ (+10 🪙)\n"
    "/roulette — 🔫 русская рулетка на 2–6 человек\n"
    "/roll — 🎲 кости: ответом на сообщение — вызов ему, без ответа — любому\n"
    "/slots — 🎰 слоты (или просто кинь 🎰 в чат)\n\n"
    "<b>Монеты:</b>\n"
    "/daily — 🪙 ежедневные 50 монет · /balance — сколько есть\n"
    "/slots 20, /roll 50 — играть на монеты\n"
    "/give 50 — перевести (ответом на сообщение) · /rich — богачи\n\n"
    "<b>Поболтать:</b> тегни меня, ответь на моё сообщение или начни с «Бот, …»\n\n"
    "/top — 🏆 доска почёта\n"
    "/init — перепись отряда: кто нажал кнопку, тот участвует в розыгрышах\n"
    "/commands, /help — эта справка"
)


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        f"Явился, {html.escape(message.from_user.first_name)}. Внёс тебя в реестр отряда.\n\n"
        "Дальше — /help, там всё написано."
    )


@router.message(Command("help", "commands"))
async def help_cmd(message: Message) -> None:
    await message.answer(HELP)


JOIN_BUTTON = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(text="⚔️ Я в деле", callback_data="init:join")
]])


async def _roster(names: list[str]) -> str:
    listed = "\n".join(f"• {html.escape(name)}" for name in sorted(names))
    return (
        "📋 <b>Перепись отряда</b>\n\n"
        "Кто жмёт кнопку — тот в реестре: участвует в розыгрышах «X дня» "
        "и попадает под мои шутки.\n\n"
        f"<b>В строю ({len(names)}):</b>\n{listed}"
    )


@router.message(Command("init"))
async def init_squad(message: Message) -> None:
    """Перепись отряда.

    Список участников группы Telegram боту не отдаёт — такого метода в Bot API
    просто нет. Поэтому записываем всех, до кого дотягиваемся: администраторов
    забираем сразу, остальные жмут кнопку. Плюс любой, кто просто пишет в чат,
    попадает в реестр сам собой (см. middlewares.RegisterPlayer).
    """
    try:
        admins = await message.bot.get_chat_administrators(message.chat.id)
    except TelegramAPIError:
        admins = []  # личка или нет прав — не беда, кнопка всё равно работает

    for admin in admins:
        if not admin.user.is_bot:
            await db.upsert_player(admin.user.id, admin.user.full_name,
                                   admin.user.username)

    names = [player.name for player in await db.all_players()]
    await message.answer(await _roster(names), reply_markup=JOIN_BUTTON)


@router.callback_query(F.data == "init:join")
async def join_squad(callback: CallbackQuery) -> None:
    user = callback.from_user
    known = await db.get_player(user.id)
    await db.upsert_player(user.id, user.full_name, user.username)

    await callback.answer("Ты уже в реестре." if known else "Записал. Добро пожаловать.",
                          show_alert=True)
    if known:
        return

    names = [player.name for player in await db.all_players()]
    try:
        await callback.message.edit_text(await _roster(names), reply_markup=JOIN_BUTTON)
    except TelegramAPIError:
        pass  # текст не изменился — Telegram ругается, это не беда


@router.message(Command("chatid"))
async def chat_id(message: Message) -> None:
    """Нужна один раз при настройке: вписать выданный ID в CLAN_CHAT_ID."""
    await message.answer(
        f"ID этого чата: <code>{message.chat.id}</code>\n\n"
        "Впиши его в .env как CLAN_CHAT_ID и перезапусти меня."
    )
