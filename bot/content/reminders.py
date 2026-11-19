"""Расписание напоминалок: только Гидра и Химера.

Гидра обновляется раз в неделю, в среду с 08:00 до 13:00 UTC (11:00–16:00 МСК).
Ориентируемся на начало окна: после него ключи уже могут сгореть.
Бои с Химерой прекращаются в четверг ровно в 14:30 МСК.

Каждая строка ниже — один момент отправки: день недели, время и то, как назвать
срок в самом тексте ({when}). Никакой арифметики с минутами: и напоминание за
несколько суток, и напоминание за два часа читаются в тексте одинаково
естественно. Тексты берутся случайно из reminders/<key>.txt, а у последнего,
злого напоминания за полчаса — из reminders/<key>_angry.txt.
"""

from dataclasses import dataclass
from datetime import time

from bot.content.loader import load

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


@dataclass(frozen=True)
class Reminder:
    key: str
    weekday: int  # 0 — понедельник, как day_of_week в CronTrigger
    at: time
    when: str
    angry: bool = False

    @property
    def job_id(self) -> str:
        return f"reminder_{self.key}_{self.weekday}_{self.at:%H%M}"

    @property
    def texts(self) -> tuple[str, ...]:
        name = f"{self.key}_angry" if self.angry else self.key
        return load("phrases", "reminders", f"{name}.txt")


REMINDERS: tuple[Reminder, ...] = (
    # Гидра: за сутки и за 2 часа до обновления, за полчаса — уже со злостью.
    Reminder(key="hydra", weekday=TUE, at=time(11, 0), when="завтра в 11:00"),
    Reminder(key="hydra", weekday=WED, at=time(9, 0), when="через 2 часа"),
    Reminder(key="hydra", weekday=WED, at=time(10, 30), when="через полчаса", angry=True),
    # Химера: так же, до закрытия боёв.
    Reminder(key="chimera", weekday=WED, at=time(14, 30), when="завтра в 14:30"),
    Reminder(key="chimera", weekday=THU, at=time(12, 30), when="через 2 часа"),
    Reminder(key="chimera", weekday=THU, at=time(14, 0), when="через полчаса", angry=True),
)
