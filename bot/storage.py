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
        """)

    def load_session(self, chat_id: int) -> dict | None:
        row = self.db.execute("SELECT state FROM sessions WHERE chat_id = ?", (chat_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_session(self, chat_id: int, user: str, state: dict) -> None:
        self.db.execute(
            "INSERT INTO sessions (chat_id, user, state, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET user = excluded.user, state = excluded.state, "
            "updated_at = excluded.updated_at",
            (chat_id, user, json.dumps(state, ensure_ascii=False), _now()),
        )
        self.db.commit()

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
