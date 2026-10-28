"""Справки по отраслям: как узнать отрасль клиента и какие вопросы ему добавить.

Справка — industries/<id>.yaml. Боту сейчас нужны markers (узнать отрасль) и questions
(дополнительные вопросы к вопросам анкеты). Остальное — воронка, боли, что автоматизировать —
для разбора ответов и отчёта, потом это же будет читать нейронка.
"""
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from .questionnaire import Followup, _followup

INDUSTRIES_DIR = Path(__file__).resolve().parent.parent / "industries"


@dataclass
class Industry:
    id: str
    title: str
    group: str
    markers: list[str]
    questions: dict[str, list[Followup]] = field(default_factory=dict)
    data: dict = field(default_factory=dict)  # вся справка как есть


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def _marker_pattern(marker: str) -> re.Pattern:
    # Каждое слово маркера — начало слова в тексте: «разработк сайт» найдёт «разработка сайтов».
    # «$» в конце — только целое слово: «сто$» найдёт «СТО», но не «стоимость»
    marker = _norm(marker).strip()
    whole = marker.endswith("$")
    words = [re.escape(w) for w in re.split(r"\s+", marker.rstrip("$"))]
    return re.compile(r"(?<!\w)" + r"\w*[\s-]+".join(words) + (r"(?!\w)" if whole else ""))


def load_industry(path: Path) -> Industry:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Industry(
        id=data["id"],
        title=data["title"],
        group=data.get("group", ""),
        markers=[str(m) for m in data.get("markers", [])],
        questions={qid: [_followup(f) for f in items]
                   for qid, items in (data.get("questions") or {}).items()},
        data=data,
    )


@lru_cache(maxsize=None)
def load_industries(directory: Path = INDUSTRIES_DIR) -> tuple[Industry, ...]:
    return tuple(load_industry(p) for p in sorted(directory.glob("*.yaml")))


def detect(text: str, industries: tuple[Industry, ...] | None = None) -> Industry | None:
    """Отрасль по тексту ответов или None, если ни один маркер не нашёлся.

    Очки — суммарная длина найденных маркеров: длинный маркер точнее короткого
    («интернет-магазин» перевешивает «магазин»).
    """
    text = _norm(text)
    best, best_score = None, 0
    for ind in industries if industries is not None else load_industries():
        score = sum(len(m.rstrip("$")) for m in ind.markers if _marker_pattern(m).search(text))
        if score > best_score:
            best, best_score = ind, score
    return best


def find(industry_id: str | None, industries: tuple[Industry, ...] | None = None) -> Industry | None:
    for ind in industries if industries is not None else load_industries():
        if ind.id == industry_id:
            return ind
    return None
