"""Напоминания тем, кто бросил интервью на середине.

Через сутки и через трое суток после последнего ответа, только днём по Минску/Москве.
Сроки можно поменять для проверки: REMINDER_HOURS=0.05,0.1 в .env.
"""
import os
from datetime import datetime, timedelta, timezone

LOCAL_TZ = timezone(timedelta(hours=3))  # Минск и Москва
DAY_HOURS = (10, 20)  # пишем с 10:00 до 20:00
DEFAULT_HOURS = (24, 72)


def remind_after() -> list[timedelta]:
    raw = os.getenv("REMINDER_HOURS")
    hours = [float(x) for x in raw.split(",")] if raw else DEFAULT_HOURS
    return [timedelta(hours=h) for h in hours]


def due(updated_at: datetime, reminded: int, now: datetime, after: list[timedelta] | None = None) -> bool:
    """Пора ли отправить следующее напоминание."""
    after = after if after is not None else remind_after()
    if reminded >= len(after):
        return False
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    if not DAY_HOURS[0] <= now.astimezone(LOCAL_TZ).hour < DAY_HOURS[1]:
        return False
    return now - updated_at >= after[reminded]


def text(state: dict, total: int, reminded: int) -> str:
    """reminded — сколько напоминаний уже было (0 — первое)."""
    if not state.get("consent_at") and not state.get("records"):
        return ("Вы хотели пройти разбор бизнеса, но мы так и не начали. "
                "Это 15–20 минут — а на выходе карта бизнеса и узкие места. Начнём?")
    index = state.get("index", 0)
    if index >= total and reminded == 0:
        return "Остался последний шаг — оставить контакт, чтобы мы прислали разбор. Продолжим?"
    left = max(total - index, 1)
    minutes = max(round(left * 0.7), 2)
    if reminded == 0:
        return (f"Вы остановились на вопросе {index + 1} из {total}. "
                f"Осталось примерно {minutes} мин — продолжим? Можно голосом, так быстрее.")
    return ("Напоминаю последний раз 🙂 Ваши ответы сохранены, можно продолжить с того же места. "
            "Если сейчас не до этого — просто не отвечайте, больше не побеспокою.")
