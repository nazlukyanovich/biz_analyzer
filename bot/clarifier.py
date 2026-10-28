"""Решает, нужен ли уточняющий вопрос.

Сейчас — правила без нейронки: длина ответа, цифры там, где их ждут, размытые фразы.
Нейронку потом подключаем как ещё одну реализацию Clarifier, интервью при этом не меняется.
"""
import re
from typing import Protocol

from .questionnaire import Question

MAX_CLARIFY = 2  # interview_rules: не больше двух уточнений подряд

GENERIC_CLARIFY = "Можно чуть подробнее? Пример из последней недели очень поможет."

# Фразы, после которых картина всё ещё непонятна
VAGUE_MARKERS = [
    "не знаю", "хз", "по-разному", "по разному", "как получится", "как когда", "когда как",
    "как-то", "как то", "всякое", "разное", "всё делает", "все делает", "всем занимается",
    "ну так", "обычно как у всех", "сложно сказать", "трудно сказать",
]

NUMBER_WORDS = re.compile(
    r"\b(один|одна|два|две|три|четыре|пять|шесть|семь|восемь|девять|десят\w*|двадцат\w*|"
    r"тридцат\w*|сорок\w*|пятьдесят|сто|сотн\w*|тысяч\w*|миллион\w*|млн|тыс|пар[ауы]?|несколько)\b"
)
# Короткий, но честный ответ — уточнять нечего
FINAL_SHORT = {"нет", "не", "никак", "нету", "ничего", "никто", "нигде", "не считаем", "не смотрим"}


def word_count(text: str) -> int:
    return len(re.findall(r"\w+", text))


def has_number(text: str) -> bool:
    return bool(re.search(r"\d", text)) or bool(NUMBER_WORDS.search(text.lower()))


def is_vague(question: Question, text: str) -> bool:
    low = text.lower().strip(" .!?")
    if low in FINAL_SHORT:
        return False
    if question.expect == "number" and not has_number(text):
        return True
    if word_count(text) < question.min_words:
        return True
    # Размытая фраза в коротком ответе; в длинном развёрнутом ответе она не страшна
    return word_count(text) < 20 and any(m in low for m in VAGUE_MARKERS)


class Clarifier(Protocol):
    def clarify(self, question: Question, answers: list[str], asked: int) -> str | None:
        """Уточняющий вопрос или None, если ответа достаточно.

        answers — основной ответ и ответы на прошлые уточнения, asked — сколько уточнений уже задано.
        """


class RuleClarifier:
    def clarify(self, question: Question, answers: list[str], asked: int) -> str | None:
        if asked >= MAX_CLARIFY:
            return None
        if not is_vague(question, " ".join(answers)):
            return None
        if asked < len(question.clarify):
            return question.clarify[asked]
        return GENERIC_CLARIFY if asked == 0 else None
