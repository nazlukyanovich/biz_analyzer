"""Telegram-бот: проводит интервью по анкете и присылает ответы владельцу сервиса.

Запуск:
    python -m bot.main

Настройки в .env (см. .env.example): BOT_TOKEN, ADMIN_CHAT_ID.
"""
import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .export import to_markdown
from .interview import Interview
from .storage import Storage

log = logging.getLogger("bizbot")
storage: Storage


def _user(update: Update) -> str:
    u = update.effective_user
    name = " ".join(x for x in (u.first_name, u.last_name) if x)
    return f"{name} (@{u.username})" if u.username else name


async def _send(update: Update, messages: list[str]) -> None:
    for text in messages:
        await update.effective_chat.send_message(text)


async def _save(update: Update, context: ContextTypes.DEFAULT_TYPE, interview: Interview) -> None:
    chat_id = update.effective_chat.id
    user = _user(update)
    storage.save_session(chat_id, user, interview.state)
    if not interview.finished:
        return
    export = interview.export()
    markdown = to_markdown(export, user)
    json_path, md_path = storage.save_finished(chat_id, user, export, markdown)
    log.info("Интервью завершено: %s → %s", user, json_path)
    admin = os.getenv("ADMIN_CHAT_ID")
    if admin:
        await context.bot.send_message(int(admin), f"Новые ответы на анкету: {user}")
        for path in (md_path, json_path):
            with path.open("rb") as f:
                await context.bot.send_document(int(admin), f, filename=path.name)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = storage.load_session(update.effective_chat.id)
    if state and not state["finished"]:
        interview = Interview(state)
        await _send(update, ["Продолжаем с того места, где остановились. /restart — начать заново.",
                             interview.current_prompt()])
        return
    await restart(update, context)


async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    interview = Interview()
    await _send(update, interview.start())
    await _save(update, context, interview)


async def skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = storage.load_session(update.effective_chat.id)
    if not state:
        await start(update, context)
        return
    interview = Interview(state)
    await _send(update, interview.skip())
    await _save(update, context, interview)


async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = storage.load_session(update.effective_chat.id)
    if not state:
        await start(update, context)
        return
    interview = Interview(state)
    await _send(update, interview.reply(update.message.text))
    await _save(update, context, interview)


async def voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = storage.load_session(update.effective_chat.id)
    prompt = Interview(state).current_prompt() if state and not state["finished"] else None
    await _send(update, ["Голосовые пока не умею разбирать, напишите, пожалуйста, текстом."]
                + ([prompt] if prompt else []))


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send(update, [f"Ваш chat id: {update.effective_chat.id}\n"
                         "Впишите его в ADMIN_CHAT_ID в .env, чтобы получать ответы клиентов."])


def main() -> None:
    global storage
    load_dotenv()
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Нет BOT_TOKEN. Скопируйте .env.example в .env и впишите токен от @BotFather.")
    storage = Storage()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("restart", restart))
    app.add_handler(CommandHandler("skip", skip))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE, voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, answer))
    log.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
