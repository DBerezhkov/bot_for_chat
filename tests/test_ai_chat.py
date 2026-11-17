import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", "test:token")

from bot import config, db  # noqa: E402
from bot.content import heroes  # noqa: E402
from bot.handlers import ai_chat  # noqa: E402
from bot.services import ai  # noqa: E402

BOT_ID = 999


class User:
    def __init__(self, uid, name="Дима"):
        self.id, self.first_name, self.username = uid, name, None


class Chat:
    id = -100


class Msg:
    def __init__(self, text, uid=1, reply_to_bot=False):
        self.text, self.chat, self.from_user = text, Chat(), User(uid)
        self.reply_to_message = Msg.__new__(Msg) if reply_to_bot else None
        if reply_to_bot:
            self.reply_to_message.from_user = User(BOT_ID, "Бот")
        self.replies = []

    async def reply(self, text, **_):
        self.replies.append(text)


class FakeBot:
    actions = []

    async def me(self):
        return User(BOT_ID, "Бот").__class__.__new__(type("Me", (), {
            "id": BOT_ID, "username": "razvlekator_syndicate_bot"}))

    async def send_chat_action(self, chat_id, action):
        self.actions.append(action)


@pytest.fixture(autouse=True)
async def fresh_state(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    await db.init()
    ai_chat._used.clear()
    ai_chat._history.clear()
    monkeypatch.setattr(config, "AI_PER_CHAT_DAY", 3)


@pytest.mark.parametrize("text, reply, hit", [
    ("Бот, как дела?", False, True),
    ("интендант что думаешь", False, True),
    ("Развлекатор, привет", False, True),
    ("эй @Razvlekator_Syndicate_Bot ты тут", False, True),
    ("ну и что", True, True),
    ("ботинки купил", False, False),
    ("просто болтаем", False, False),
    ("/help", False, False),
])
async def test_addressed_to_me(text, reply, hit):
    assert await ai_chat.addressed_to_me(Msg(text, reply_to_bot=reply), FakeBot()) is hit


def test_quota_only_per_chat():
    # Один и тот же человек может выбрать весь лимит чата — личного лимита нет.
    for _ in range(3):
        assert ai_chat.take_quota(-1) is None
    assert "охрип" in ai_chat.take_quota(-1)
    assert ai_chat.take_quota(-2) is None, "лимит у каждого чата свой"


def test_persona_loaded_from_file():
    assert ai_chat.PERSONA.startswith("Ты — Интендант")
    assert "Простой текст." in ai_chat.PERSONA


def test_prompt_contains_recent_history():
    for i in range(ai_chat.HISTORY + 5):
        ai_chat.remember(-1, "Вася", f"сообщение {i}")
    prompt = ai_chat.build_prompt(-1, "Вася")
    assert "сообщение 34" in prompt and "сообщение 4\n" not in prompt
    assert prompt.count("Вася:") == ai_chat.HISTORY


def test_parse_gemini_answer_skips_thoughts():
    data = {"candidates": [{"content": {"parts": [
        {"text": "думаю...", "thought": True}, {"text": "Привет, боец."}]}}]}
    assert ai._text(data) == "Привет, боец."
    with pytest.raises(ai.AIError):
        ai._text({"candidates": [{"content": {"parts": []}}]})


async def test_reply_is_escaped_and_remembered(monkeypatch):
    async def fake_generate(system, prompt):
        assert "Интендант" in system and "Дима" in prompt
        return "Ну <b>что</b> & как?"
    monkeypatch.setattr(ai, "generate", fake_generate)
    msg = Msg("Бот, как дела?")
    ai_chat.remember(Chat.id, "Дима", msg.text)
    await ai_chat.chat(msg, FakeBot())
    assert msg.replies == ["Ну &lt;b&gt;что&lt;/b&gt; &amp; как?"]
    assert ai_chat._history[Chat.id][-1].startswith("Интендант: Ну <b>")


async def test_gemini_failure_refunds_quota(monkeypatch):
    async def broken(system, prompt):
        raise ai.AIError("HTTP 429")
    monkeypatch.setattr(ai, "generate", broken)
    for _ in range(5):
        msg = Msg("Бот?")
        await ai_chat.chat(msg, FakeBot())
        assert "задумался" in msg.replies[0]
    assert ai_chat.take_quota(Chat.id) is None, "сбой Gemini не должен съедать лимит"


async def test_chat_log_skips_commands():
    log = ai_chat.ChatLog()

    async def handler(event, data):
        return None

    from datetime import datetime
    from aiogram.types import Chat as TgChat, Message, User as TgUser
    for text in ("бот ты чо молчишь", "/slots 20", "ну"):
        msg = Message(message_id=1, date=datetime.now(), text=text,
                      chat=TgChat(id=-7, type="supergroup"),
                      from_user=TgUser(id=1, is_bot=False, first_name="Дима"))
        await log(handler, msg, {})
    assert list(ai_chat._history[-7]) == ["Дима: бот ты чо молчишь", "Дима: ну"]


async def test_retry_once_on_503(monkeypatch):
    calls = []

    async def flaky(system, prompt):
        calls.append(1)
        if len(calls) == 1:
            raise ai.AIError("HTTP 503: overloaded")
        return "ок"

    async def no_sleep(_):
        return None

    monkeypatch.setattr(ai, "_generate_once", flaky)
    monkeypatch.setattr(ai.asyncio, "sleep", no_sleep)
    assert await ai.generate("s", "p") == "ок" and len(calls) == 2

    calls.clear()

    async def quota(system, prompt):
        calls.append(1)
        raise ai.AIError("HTTP 429: quota")

    monkeypatch.setattr(ai, "_generate_once", quota)
    with pytest.raises(ai.AIError):
        await ai.generate("s", "p")
    assert len(calls) == 1, "лимит квоты повторять бессмысленно"


# --- Герои -----------------------------------------------------------------

def test_hero_search_by_names_and_russian_aliases():
    text = "Арбитра или варлорда на гидру? А лидию? siphi, The Primogenitor"
    assert heroes.find(text) == ["Arbiter", "Warlord", "Lydia the Deathsiren",
                                 "Siphi the Lost Bride", "The Primogenitor"]
    assert heroes.find("лидер клана, сегодня арена") == []


@pytest.mark.parametrize("text, hero", [
    ("бот, а николас?", "Sir Nicholas"),
    ("а акриция?", "Acrizia"),
    ("людоеда качать?", "Maneater"),     # перевод, а не транслит
    ("атаман на гидре", "Warlord"),
    ("с каэлем в связке", "Kael"),       # редкий, но имя собственное
    ("Лидия Восставшая", "Lydia the Deathsiren"),
])
def test_hero_search_by_official_russian_names(text, hero):
    assert heroes.find(text) == [hero]


@pytest.mark.parametrize("text", [
    "палач и лекарь пришли",             # нарицательные имена редких героев
    "капитан команды на арене",
    "перезвоню вечером",
    "синдром понедельника",
    "золотая середина, личный рекорд",
    "кого на гидру ставить сегодня вечером",
])
def test_ordinary_words_are_not_heroes(text):
    assert heroes.find(text) == []


def test_hero_aliases_point_to_real_heroes():
    heroes._matcher.cache_clear()
    heroes._matcher()  # падает, если в hero_aliases.txt опечатка в имени


def test_hero_description_has_content_ratings():
    text = heroes.describe("Arbiter")
    assert "Клан-босс" in text and "Гидра" in text and "Арена" in text
    assert heroes.describe("The Primogenitor").count("форма") == 2


async def test_teach_and_forget_hero():
    msg = Msg("бот, герой лидия топовый разломщик, в пве пригодится всегда")
    assert ai_chat._teaching(msg)
    await ai_chat.teach_hero(msg)
    assert "Lydia the Deathsiren" in msg.replies[0]
    assert await db.hero_notes("Lydia the Deathsiren") == [
        ("топовый разломщик, в пве пригодится всегда", "Дима")]

    unknown = Msg("Бот, герой Гроза — новенький, бьёт больно")
    await ai_chat.teach_hero(unknown)
    assert "такого нет" in unknown.replies[0]

    ai_chat.remember(Chat.id, "Вася", "что скажешь про лидию и грозу?")
    reference = await ai_chat.hero_reference(Chat.id)
    assert "Lydia the Deathsiren" in reference and "топовый разломщик" in reference
    assert "гроза: в базе HellHades нет" in reference and "бьёт больно" in reference

    forget = Msg("бот, забудь героя лидия")
    await ai_chat.forget_hero(forget)
    assert "Вычеркнул" in forget.replies[0]
    assert await db.hero_notes("Lydia the Deathsiren") == []


def test_question_about_hero_is_not_a_lesson():
    assert not ai_chat._teaching(Msg("бот, герой Арбитр хорош на гидре?"))
    assert not ai_chat._teaching(Msg("бот, как там Арбитр"))


async def test_hero_reference_goes_into_prompt(monkeypatch):
    seen = {}

    async def fake_generate(system, prompt):
        seen["prompt"] = prompt
        return "ок"
    monkeypatch.setattr(ai, "generate", fake_generate)
    msg = Msg("Бот, Арбитр нужен на Гидру?")
    ai_chat.remember(Chat.id, "Дима", msg.text)
    await ai_chat.chat(msg, FakeBot())
    assert "Справка по упомянутым героям" in seen["prompt"]
    assert "Arbiter / Арбитр [Legendary" in seen["prompt"]
