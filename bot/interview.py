"""Интервью как конечный автомат, без привязки к Telegram.

Состояние — обычный dict, его можно сохранить в базу как JSON и продолжить позже.
"""
from datetime import datetime, timezone

from .clarifier import Clarifier, RuleClarifier, is_vague
from .questionnaire import Question, load_questions

WELCOME = (
    "Привет! Я задам около 25 вопросов о вашем бизнесе — это займёт 15–20 минут.\n"
    "По ответам мы соберём карту бизнеса, схемы процессов и найдём узкие места.\n\n"
    "Отвечайте своими словами, как другу. Если ответ получится коротким, я уточню.\n"
    "/skip — пропустить вопрос, /restart — начать заново."
)
FINISHED = (
    "Готово, спасибо! Все ответы сохранены.\n"
    "Мы разберём их и пришлём карту бизнеса и схемы процессов."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Interview:
    def __init__(self, state: dict | None = None, questions: list[Question] | None = None,
                 clarifier: Clarifier | None = None):
        self.questions = questions or load_questions()
        self.clarifier = clarifier or RuleClarifier()
        self.state = state or {
            "index": 0,
            "records": {},
            "pending": None,
            "finished": False,
            "started_at": _now(),
            "finished_at": None,
        }

    # --- публичное API -------------------------------------------------------

    @property
    def finished(self) -> bool:
        return self.state["finished"]

    def start(self) -> list[str]:
        q = self.questions[0]
        return [WELCOME, self._block_header(q) + "\n\n" + self._ask_main(q)]

    def current_prompt(self) -> str | None:
        pending = self.state["pending"]
        return pending["text"] if pending else None

    def reply(self, text: str) -> list[str]:
        if self.finished:
            return ["Интервью уже пройдено. /restart — пройти заново."]
        text = text.strip()
        if not text:
            return [self.current_prompt()]
        q = self._question()
        rec = self._record(q)
        pending = self.state["pending"]
        if pending["kind"] == "followup":
            rec["followups"].append({"q": pending["text"], "a": text})
        else:
            rec["answers"].append(text)
        return self._next_for(q, rec)

    def skip(self) -> list[str]:
        if self.finished:
            return ["Интервью уже пройдено. /restart — пройти заново."]
        q = self._question()
        rec = self._record(q)
        if not rec["answers"]:
            rec["skipped"] = True
        return self._advance()

    def export(self) -> dict:
        """Ответы в виде, удобном для разбора: вопрос → ответ, уточнения, пометки."""
        items = []
        for q in self.questions:
            rec = self.state["records"].get(q.id)
            if rec is None:
                continue
            items.append({
                "question_id": q.id,
                "block": q.block_title,
                "question": q.text,
                "fills": q.fills,
                "answers": rec["answers"],
                "clarifications": rec["clarify_qs"],
                "followups": rec["followups"],
                "skipped": rec["skipped"],
                "needs_review": rec["needs_review"],
            })
        return {
            "questionnaire_version": "1.0",
            "started_at": self.state["started_at"],
            "finished_at": self.state["finished_at"],
            "items": items,
        }

    # --- внутренняя логика ---------------------------------------------------

    def _question(self) -> Question:
        return self.questions[self.state["index"]]

    def _record(self, q: Question) -> dict:
        return self.state["records"].setdefault(q.id, {
            "answers": [], "clarify_qs": [], "followups": [],
            "skipped": False, "needs_review": False,
        })

    def _ask_main(self, q: Question) -> str:
        n, total = self.state["index"] + 1, len(self.questions)
        text = f"({n}/{total}) {q.text}"
        self.state["pending"] = {"kind": "main", "text": text}
        return text

    def _block_header(self, q: Question) -> str:
        blocks = list(dict.fromkeys(x.block_id for x in self.questions))
        return f"Блок {blocks.index(q.block_id) + 1} из {len(blocks)}: «{q.block_title}»"

    def _next_for(self, q: Question, rec: dict) -> list[str]:
        pending = self.state["pending"]

        if pending["kind"] in ("main", "clarify"):
            asked = len(rec["clarify_qs"])
            question = self.clarifier.clarify(q, rec["answers"], asked)
            if question:
                rec["clarify_qs"].append(question)
                self.state["pending"] = {"kind": "clarify", "text": question}
                return [question]
            rec["needs_review"] = is_vague(q, " ".join(rec["answers"]))
            start = 0
        else:
            start = pending["index"] + 1

        said = " ".join(rec["answers"] + [f["a"] for f in rec["followups"]]).lower()
        for i in range(start, len(q.followups)):
            f = q.followups[i]
            if any(word in said for word in f.skip_if):
                continue
            self.state["pending"] = {"kind": "followup", "text": f.text, "index": i}
            return [f.text]

        return self._advance()

    def _advance(self) -> list[str]:
        prev = self._question()
        self.state["index"] += 1
        if self.state["index"] >= len(self.questions):
            self.state["finished"] = True
            self.state["finished_at"] = _now()
            self.state["pending"] = None
            return [FINISHED]
        q = self._question()
        if q.block_id != prev.block_id:
            return [f"✅ Блок «{prev.block_title}» готов.\n\n{self._block_header(q)}\n\n{self._ask_main(q)}"]
        return [self._ask_main(q)]
