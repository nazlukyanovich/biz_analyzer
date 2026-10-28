"""SQLite: текущие интервью (чтобы продолжать после перезапуска бота) и завершённые ответы."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class Storage:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = data_dir
        self.answers_dir = data_dir / "answers"
        self.answers_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(data_dir / "bot.db")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                chat_id INTEGER PRIMARY KEY,
                user TEXT,
                state TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS interviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user TEXT,
                finished_at TEXT NOT NULL,
                answers TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                sent_at TEXT NOT NULL
            );
        """)
        # Сколько напоминаний отправлено с последнего действия клиента (для старых баз — добавляем столбец)
        columns = [row[1] for row in self.db.execute("PRAGMA table_info(sessions)")]
        if "reminded" not in columns:
            self.db.execute("ALTER TABLE sessions ADD COLUMN reminded INTEGER NOT NULL DEFAULT 0")
            self.db.commit()

    def load_session(self, chat_id: int) -> dict | None:
        row = self.db.execute("SELECT state FROM sessions WHERE chat_id = ?", (chat_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_session(self, chat_id: int, user: str, state: dict) -> None:
        self.db.execute(
            "INSERT INTO sessions (chat_id, user, state, updated_at, reminded) VALUES (?, ?, ?, ?, 0) "
            "ON CONFLICT(chat_id) DO UPDATE SET user = excluded.user, state = excluded.state, "
            "updated_at = excluded.updated_at, reminded = 0",
            (chat_id, user, json.dumps(state, ensure_ascii=False), _now()),
        )
        self.db.commit()

    def unfinished_sessions(self) -> list[tuple[int, dict, datetime, int]]:
        """Незавершённые интервью: chat_id, состояние, время последнего действия, сколько раз напомнили."""
        rows = self.db.execute("SELECT chat_id, state, updated_at, reminded FROM sessions").fetchall()
        result = []
        for chat_id, state, updated_at, reminded in rows:
            state = json.loads(state)
            if not state.get("finished"):
                result.append((chat_id, state, datetime.fromisoformat(updated_at), reminded))
        return result

    def last_finished_at(self, chat_id: int) -> str | None:
        row = self.db.execute("SELECT MAX(finished_at) FROM interviews WHERE chat_id = ?", (chat_id,)).fetchone()
        return row[0]

    def add_report(self, chat_id: int, url: str) -> None:
        self.db.execute("INSERT INTO reports (chat_id, url, sent_at) VALUES (?, ?, ?)", (chat_id, url, _now()))
        self.db.commit()

    def last_report(self, chat_id: int) -> tuple[str, str] | None:
        """Последний отправленный отчёт: ссылка и когда отправлен."""
        return self.db.execute("SELECT url, sent_at FROM reports WHERE chat_id = ? ORDER BY id DESC LIMIT 1",
                               (chat_id,)).fetchone()

    def mark_reminded(self, chat_id: int, reminded: int) -> None:
        # updated_at не трогаем: напоминание — не действие клиента
        self.db.execute("UPDATE sessions SET reminded = ? WHERE chat_id = ?", (reminded, chat_id))
        self.db.commit()

    def delete_user(self, chat_id: int) -> int:
        """Удаляет всё о клиенте: текущее интервью, готовые ответы в базе и файлы. Возвращает число файлов."""
        self.db.execute("DELETE FROM sessions WHERE chat_id = ?", (chat_id,))
        self.db.execute("DELETE FROM interviews WHERE chat_id = ?", (chat_id,))
        self.db.execute("DELETE FROM reports WHERE chat_id = ?", (chat_id,))
        self.db.commit()
        files = list(self.answers_dir.glob(f"{chat_id}_*"))
        for path in files:
            path.unlink()
        return len(files)

    def save_finished(self, chat_id: int, user: str, export: dict, markdown: str) -> tuple[Path, Path]:
        """Сохраняет готовые ответы в базу и в файлы data/answers/*.json и *.md."""
        self.db.execute(
            "INSERT INTO interviews (chat_id, user, finished_at, answers) VALUES (?, ?, ?, ?)",
            (chat_id, user, export["finished_at"], json.dumps(export, ensure_ascii=False)),
        )
        self.db.commit()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = self.answers_dir / f"{chat_id}_{stamp}"
        json_path, md_path = base.with_suffix(".json"), base.with_suffix(".md")
        json_path.write_text(json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8")
        md_path.write_text(markdown, encoding="utf-8")
        return json_path, md_path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
