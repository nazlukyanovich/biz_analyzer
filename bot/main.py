"""Telegram-бот: проводит интервью по анкете и присылает ответы владельцу сервиса.

Запуск:
    python -m bot.main

Настройки в .env (см. .env.example): BOT_TOKEN, ADMIN_CHAT_ID, YANDEX_API_KEY.
"""
import copy
import json
import logging
import os

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from .export import to_markdown
from .interview import Interview
from .storage import Storage
from .transcriber import Transcriber, TranscriptionError, make_transcriber

log = logging.getLogger("bizbot")
storage: Storage
transcriber: Transcriber | None

UNDO_BUTTON = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Переписать ответ", callback_data="undo")]])


def _user(update: Update) -> str:
    u = update.effective_user
    name = " ".join(x for x in (u.first_name, u.last_name) if x)
    return f"{name} (@{u.username})" if u.username else name


async def _send(update: Update, messages: list[str]) -> None:
    for text in messages:
        await update.effective_chat.send_message(text)


async def _save(update: Update, context: ContextTypes.DEFAULT_TYPE, interview: Interview,
                was_finished: bool = False) -> None:
    chat_id = update.effective_chat.id
    user = _user(update)
    storage.save_session(chat_id, user, interview.state)
    if was_finished or not interview.finished:
        return  # ответы отправляем один раз — в момент завершения
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


async def _handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Общий путь для текста и распознанного голоса."""
    state = storage.load_session(update.effective_chat.id)
    if not state:
        await start(update, context)
        return
    before = copy.deepcopy(state)
    interview = Interview(state)
    was_finished = interview.finished
    await _send(update, interview.reply(text))
    await _save(update, context, interview, was_finished)
    # Для «Переписать ответ»: состояние до ответа и отпечаток состояния после него
    context.chat_data["undo"] = None if interview.finished else {
        "before": before, "after": json.dumps(interview.state, sort_keys=True),
    }


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
    messages = interview.start()
    if transcriber:
        messages[0] += "\n\n🎙 Можно отвечать голосовыми — так быстрее."
    await _send(update, messages)
    await _save(update, context, interview)
    context.chat_data["undo"] = None


async def skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = storage.load_session(update.effective_chat.id)
    if not state:
        await start(update, context)
        return
    interview = Interview(state)
    was_finished = interview.finished
    await _send(update, interview.skip())
    await _save(update, context, interview, was_finished)
    context.chat_data["undo"] = None


async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_answer(update, context, update.message.text)


async def voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not transcriber:
        await _send(update, ["Голосовые пока не подключены, напишите, пожалуйста, текстом."])
        return
    v = update.message.voice
    status = await update.message.reply_text("🎧 Слушаю…")
    try:
        file = await v.get_file()
        audio = bytes(await file.download_as_bytearray())
        text = await transcriber.transcribe(audio, v.duration or 0)
    except TranscriptionError as e:
        log.warning("Не удалось распознать голосовое: %s", e)
        await status.edit_text("Не получилось разобрать голосовое 😕 Попробуйте ещё раз или напишите текстом.")
        return
    if not text:
        await status.edit_text("Не расслышал — запишите, пожалуйста, ещё раз, чуть громче или ближе к микрофону.")
        return
    await status.edit_text(f"📝 Записал: «{text}»", reply_markup=UNDO_BUTTON)
    await _handle_answer(update, context, text)


async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(None)
    saved = context.chat_data.get("undo")
    state = storage.load_session(update.effective_chat.id)
    if not saved or not state or json.dumps(state, sort_keys=True) != saved["after"]:
        await _send(update, ["Этот ответ уже не переписать — после него были другие. "
                             "Можно дописать уточнение в ответ на текущий вопрос."])
        return
    interview = Interview(saved["before"])
    await _save(update, context, interview)
    context.chat_data["undo"] = None
    await _send(update, ["Хорошо, ответьте ещё раз:", interview.current_prompt()])


async def unsupported_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send(update, ["Такой формат не разберу — запишите голосовое сообщение или напишите текстом."])


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send(update, [f"Ваш chat id: {update.effective_chat.id}\n"
                         "Впишите его в ADMIN_CHAT_ID в .env, чтобы получать ответы клиентов."])


def main() -> None:
    global storage, transcriber
    load_dotenv()
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Нет BOT_TOKEN. Скопируйте .env.example в .env и впишите токен от @BotFather.")
    storage = Storage()
    transcriber = make_transcriber()
    if not transcriber:
        log.warning("YANDEX_API_KEY не задан — голосовые отключены")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("restart", restart))
    app.add_handler(CommandHandler("skip", skip))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CallbackQueryHandler(undo, pattern="^undo$"))
    app.add_handler(MessageHandler(filters.VOICE, voice))
    app.add_handler(MessageHandler(filters.AUDIO | filters.VIDEO_NOTE, unsupported_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, answer))
    log.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
