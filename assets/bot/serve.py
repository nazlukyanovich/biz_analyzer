"""Локальный сервер для make.html: отдаёт страницу и сохраняет присланные ею картинки рядом.

    python assets/bot/serve.py   → открыть http://localhost:8766/make.html
"""
import base64
import http.server
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALLOWED = {"welcome.jpg", "finish.jpg", "icon.png"}


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HERE), **kwargs)

    def do_POST(self):
        name = self.path.split("name=", 1)[-1]
        if not self.path.startswith("/save?") or name not in ALLOWED:
            self.send_error(400)
            return
        data = self.rfile.read(int(self.headers["Content-Length"])).decode()
        (HERE / name).write_bytes(base64.b64decode(data.split(",", 1)[1]))
        self.send_response(200)
        self.end_headers()
        print("Сохранено:", name)


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", 8766), Handler).serve_forever()
