"""Интервью как конечный автомат, без привязки к Telegram.

Состояние — обычный dict, его можно сохранить в базу как JSON и продолжить позже.
"""
import copy
from datetime import datetime, timezone

from . import industries as ind
from .clarifier import Clarifier, RuleClarifier, is_vague
from .questionnaire import Followup, Question, load_express_ids, load_questions

WELCOME = (
    "Привет! Я задам вопросы о вашем бизнесе — коротко за 5 минут или подробно за 15–20.\n"
    "По ответам мы соберём карту бизнеса, схемы процессов и найдём узкие места.\n\n"
    "Отвечайте своими словами, как другу. Если ответ получится коротким, я уточню.\n"
    "Под каждым вопросом есть кнопки: пропустить вопрос или вернуться к прошлому."
)
CONSENT = (
    "Перед началом — коротко о данных.\n\n"
    "Ваши ответы о бизнесе и контакт мы используем только для разбора и отчёта и никому не передаём. "
    "Удалить всё можно в любой момент — просто напишите нам.\n\n"
    "Нажимая «Согласен», вы соглашаетесь на обработку персональных данных."
)
CONSENT_REMIND = "Чтобы начать, нажмите «✅ Согласен» под сообщением о данных."
MODE = (
    "Как пройдём?\n\n"
    "⚡ Экспресс — {express} главных вопросов, около 5 минут. Покажем главные узкие места.\n"
    "🔍 Полный разбор — {full} вопросов, 15–20 минут. Карта бизнеса, схемы процессов и подробный разбор."
)
MODE_REMIND = "Выберите режим кнопкой под сообщением: ⚡ Экспресс или 🔍 Полный разбор."
MODES = ("express", "full")
CONTACT = (
    "Последний шаг: как с вами связаться, когда разбор будет готов?\n\n"
    "Нажмите кнопку ниже, чтобы отправить номер, или напишите удобный способ связи — "
    "телефон, почту, Telegram."
)
HISTORY_LIMIT = 100  # сколько шагов назад можно вернуться

FINISHED = (
    "Готово, спасибо! Все ответы сохранены.\n\n"
    "Мы разберём их и пришлём сюда ссылку на ваш разбор — обычно в течение 2 рабочих дней.\n"
    "Узнать, как идут дела: /status"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Interview:
    def __init__(self, state: dict | None = None, questions: list[Question] | None = None,
                 clarifier: Clarifier | None = None, industries: tuple[ind.Industry, ...] | None = None):
        self.all_questions = questions or load_questions()
        self.express_ids = load_express_ids()
        self.clarifier = clarifier or RuleClarifier()
        self.industries = industries if industries is not None else ind.load_industries()
        self.state = state or {
            "index": 0,
            "records": {},
            "pending": None,
            "finished": False,
            "started_at": _now(),
            "finished_at": None,
            "industry": None,
            "history": [],
            "step": 0,
            "consent_at": None,
            "contact": None,
            "mode": None,
        }

    @property
    def express(self) -> bool:
        return self.state.get("mode") == "express"

    @property
    def questions(self) -> list[Question]:
        """Вопросы выбранного режима; индекс в state считается по этому списку."""
        if self.express:
            return [q for q in self.all_questions if q.id in self.express_ids]
        return self.all_questions

    # --- публичное API -------------------------------------------------------

    @property
    def finished(self) -> bool:
        return self.state["finished"]

    @property
    def pending_kind(self) -> str | None:
        """main, clarify, followup — вопросы анкеты; consent — ждём согласия; contact — ждём контакт."""
        pending = self.state["pending"]
        return pending["kind"] if pending else None

    def start(self, require_consent: bool = False) -> list[str]:
        """require_consent — сначала спросить согласие на обработку данных (пока выключено)."""
        if require_consent:
            self.state["pending"] = {"kind": "consent", "text": CONSENT}
            return [WELCOME, CONSENT]
        return [WELCOME, self._ask_mode()]

    def accept_consent(self) -> list[str]:
        if self.pending_kind != "consent":
            return [self.current_prompt()] if self.current_prompt() else []
        self.state["consent_at"] = _now()
        return [self._ask_mode()]

    def _ask_mode(self) -> str:
        text = MODE.format(express=len(self.express_ids), full=len(self.all_questions))
        self.state["pending"] = {"kind": "mode", "text": text}
        return text

    def choose_mode(self, mode: str) -> list[str]:
        if self.pending_kind != "mode" or mode not in MODES:
            return [self.current_prompt()] if self.current_prompt() else []
        self.state["mode"] = mode
        q = self.questions[0]
        return [self._block_header(q) + "\n\n" + self._ask_main(q)]

    def set_contact(self, phone: str, name: str = "") -> list[str]:
        """Контакт кнопкой «Отправить номер»."""
        if self.pending_kind != "contact":
            return [self.current_prompt()] if self.current_prompt() else []
        self._remember()
        self.state["contact"] = {"phone": phone, "name": name}
        return self._finish()

    @property
    def can_go_back(self) -> bool:
        return not self.finished and bool(self.state.get("history"))

    @property
    def step(self) -> int:
        """Номер шага: меняется после каждого ответа, пропуска и возврата — чтобы старые кнопки не срабатывали."""
        return self.state.get("step", 0)

    def current_prompt(self) -> str | None:
        pending = self.state["pending"]
        return pending["text"] if pending else None

    def reply(self, text: str) -> list[str]:
        if self.finished:
            return ["Интервью уже пройдено. /restart — пройти заново."]
        text = text.strip()
        if not text:
            return [self.current_prompt()]
        if self.pending_kind == "consent":
            return [CONSENT_REMIND]
        if self.pending_kind == "mode":
            return [MODE_REMIND]
        self._remember()
        if self.pending_kind == "contact":
            self.state["contact"] = {"text": text}
            return self._finish()
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
        if self.pending_kind == "consent":
            return [CONSENT_REMIND]
        if self.pending_kind == "mode":
            return [MODE_REMIND]
        self._remember()
        if self.pending_kind == "contact":
            return self._finish()
        q = self._question()
        rec = self._record(q)
        if not rec["answers"]:
            rec["skipped"] = True
        return self._advance()

    def back(self) -> list[str]:
        """Вернуться к прошлому вопросу (уточнению): последний ответ или пропуск отменяется."""
        if self.finished:
            return ["Интервью уже пройдено. /restart — пройти заново."]
        history = self.state.get("history") or []
        if not history:
            return ["Это первый вопрос — назад некуда.", self.current_prompt()]
        self.state = {**history.pop(), "history": history, "step": self.step + 1}
        return ["⬅️ Вернулись назад. Ответьте заново — прежний ответ я заменю.", self.current_prompt()]

    @property
    def industry(self) -> ind.Industry | None:
        return ind.find(self.state.get("industry"), self.industries)

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
        industry = self.industry
        return {
            "questionnaire_version": "1.0",
            "industry": {"id": industry.id, "title": industry.title} if industry else None,
            "mode": self.state.get("mode") or "full",
            "consent_at": self.state.get("consent_at"),
            "contact": self.state.get("contact"),
            "started_at": self.state["started_at"],
            "finished_at": self.state["finished_at"],
            "items": items,
        }

    # --- внутренняя логика ---------------------------------------------------

    def _remember(self) -> None:
        snapshot = copy.deepcopy({k: v for k, v in self.state.items() if k not in ("history", "step")})
        history = self.state.setdefault("history", [])
        history.append(snapshot)
        del history[:-HISTORY_LIMIT]
        self.state["step"] = self.step + 1

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
            # В экспресс-режиме — одно уточнение и без углубления
            question = None if self.express and asked >= 1 else self.clarifier.clarify(q, rec["answers"], asked)
            if question:
                rec["clarify_qs"].append(question)
                self.state["pending"] = {"kind": "clarify", "text": question}
                return [question]
            rec["needs_review"] = is_vague(q, " ".join(rec["answers"]))
            start = 0
        else:
            start = pending["index"] + 1

        followups = [] if self.express else self._followups(q)
        said = " ".join(rec["answers"] + [f["a"] for f in rec["followups"]]).lower()
        for i in range(start, len(followups)):
            f = followups[i]
            if any(word in said for word in f.skip_if):
                continue
            self.state["pending"] = {"kind": "followup", "text": f.text, "index": i}
            return [f.text]

        return self._advance()

    def _followups(self, q: Question) -> list[Followup]:
        """Углубляющие вопросы анкеты, а за ними — вопросы из справки по отрасли клиента."""
        industry = self.industry
        return q.followups + (industry.questions.get(q.id, []) if industry else [])

    def _detect_industry(self) -> None:
        # Отрасль — по ответам первого блока; вопросы из справки начинаются со второго
        first_block = self.questions[0].block_id
        texts = []
        for q in self.questions:
            rec = self.state["records"].get(q.id)
            if q.block_id == first_block and rec:
                texts += rec["answers"] + [f["a"] for f in rec["followups"]]
        found = ind.detect(" ".join(texts), self.industries)
        self.state["industry"] = found.id if found else None

    def _advance(self) -> list[str]:
        prev = self._question()
        if prev.block_id == self.questions[0].block_id:
            self._detect_industry()
        self.state["index"] += 1
        if self.state["index"] >= len(self.questions):
            self.state["pending"] = {"kind": "contact", "text": CONTACT}
            return [f"✅ Блок «{prev.block_title}» готов.", CONTACT]
        q = self._question()
        if q.block_id != prev.block_id:
            return [f"✅ Блок «{prev.block_title}» готов.\n\n{self._block_header(q)}\n\n{self._ask_main(q)}"]
        return [self._ask_main(q)]

    def _finish(self) -> list[str]:
        self.state["finished"] = True
        self.state["finished_at"] = _now()
        self.state["pending"] = None
        return [FINISHED]
