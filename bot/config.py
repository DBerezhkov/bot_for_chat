import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Не задана переменная окружения {name} (см. .env.example)")
    return value


BOT_TOKEN = _require("BOT_TOKEN")

# Чат клана, куда бот сам пишет напоминалки и шутки. Пока не задан —
# всё остальное работает, но рассылать некуда: узнать ID можно командой
# /chatid прямо в группе.
_chat_id = os.getenv("CLAN_CHAT_ID", "").strip()
CLAN_CHAT_ID = int(_chat_id) if _chat_id else None

TZ = ZoneInfo(os.getenv("TZ", "Europe/Moscow"))

DB_PATH = os.getenv("DB_PATH", "data/bot.db")

def _flag(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# Напоминалки про Гидру и Химеру. Выключаются целиком, отдельно от шуток.
REMINDERS_ENABLED = _flag("REMINDERS_ENABLED")

# Сколько раз в день бот шутит (число выбирается случайно в этих границах)
# и в каком окне (часы по TZ).
JOKES_PER_DAY = (
    int(os.getenv("JOKES_PER_DAY_MIN", "2")),
    int(os.getenv("JOKES_PER_DAY_MAX", "3")),
)
JOKE_HOURS = (int(os.getenv("JOKE_HOUR_FROM", "12")), int(os.getenv("JOKE_HOUR_TO", "22")))

# Вероятность, что шутка будет адресной — с тегом случайного участника.
JOKE_MENTION_CHANCE = float(os.getenv("JOKE_MENTION_CHANCE", "0.3"))

# Стартовые характеристики персонажа. Числа намеренно маленькие: в бою
# «камень-ножницы-бумага» статы двигают только пороги «сколько раундов надо
# выиграть», а пороги целые. При сотнях HP одно очко не значит ничего,
# при десятке — переводит бой из проигрышного в выигрышный.
START_HP = 10
START_ATK = 5
START_DEF = 0
MAX_ENERGY = 2

# Прибавка за уровень. Защита растёт вдвое медленнее: она делит входящий урон
# и в связке с HP работает мультипликативно, иначе перс быстро становится
# неубиваемым.
LEVEL_HP = 3
LEVEL_ATK = 1
LEVEL_DEF_EVERY = 2  # +1 защиты на каждом чётном уровне

# Собеседник на Gemini. Пустой ключ — функция выключена, остальное работает.
# Ключ: aistudio.google.com → Get API key.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest").strip()
# Лимиты держат запросы в бесплатной квоте и не дают одному шутнику съесть её всю.
AI_PER_USER_DAY = int(os.getenv("AI_PER_USER_DAY", "15"))
AI_PER_CHAT_DAY = int(os.getenv("AI_PER_CHAT_DAY", "200"))
