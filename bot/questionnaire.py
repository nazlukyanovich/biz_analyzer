"""Загрузка анкеты: блоки и вопросы в плоский список, по которому идёт интервью."""
from dataclasses import dataclass, field
from pathlib import Path

import yaml

QUESTIONNAIRE_PATH = Path(__file__).resolve().parent.parent / "schema" / "questionnaire.yaml"
DEFAULT_MIN_WORDS = 5


@dataclass
class Followup:
    text: str
    skip_if: list[str] = field(default_factory=list)


@dataclass
class Question:
    id: str
    text: str
    block_id: str
    block_title: str
    fills: list[str] = field(default_factory=list)
    clarify: list[str] = field(default_factory=list)
    min_words: int = DEFAULT_MIN_WORDS
    expect: str | None = None
    followups: list[Followup] = field(default_factory=list)


def _followup(raw) -> Followup:
    if isinstance(raw, str):
        return Followup(text=raw)
    return Followup(text=raw["text"], skip_if=[w.lower() for w in raw.get("skip_if", [])])


def load_express_ids(path: Path = QUESTIONNAIRE_PATH) -> list[str]:
    """Вопросы экспресс-режима (поле express в анкете)."""
    return yaml.safe_load(path.read_text(encoding="utf-8")).get("express", [])


def load_questions(path: Path = QUESTIONNAIRE_PATH) -> list[Question]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    questions = []
    for block in data["blocks"]:
        for q in block["questions"]:
            questions.append(Question(
                id=q["id"],
                text=q["text"],
                block_id=block["id"],
                block_title=block["title"],
                fills=q.get("fills", []),
                clarify=q.get("clarify", []),
                min_words=q.get("min_words", DEFAULT_MIN_WORDS),
                expect=q.get("expect"),
                followups=[_followup(f) for f in q.get("followups", [])],
            ))
    return questions
