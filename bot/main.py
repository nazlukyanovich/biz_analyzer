"""Telegram-бот: проводит интервью по анкете и присылает ответы владельцу сервиса.

Запуск:
    python -m bot.main

Настройки в .env (см. .env.example): BOT_TOKEN, ADMIN_CHAT_ID, YANDEX_API_KEY.
"""
import asyncio
import copy
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telegram import (BotCommand, BotCommandScopeChat, InlineKeyboardButton, InlineKeyboardMarkup,
                      KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update)
from telegram.constants import ChatAction
from telegram.error import Forbidden, TelegramError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from . import reminders
from .export import to_markdown
from .interview import FINISHED, Interview
from .storage import Storage
from .transcriber import Transcriber, TranscriptionError, make_transcriber

log = logging.getLogger("bizbot")
storage: Storage
transcriber: Transcriber | None

UNDO_BUTTON = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Переписать ответ", callback_data="undo")]])
RESUME_BUTTON = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Продолжить", callback_data="resume")]])

# Кнопки под полем ввода на шаге «контакт»
CONTACT_SKIP = "Пропустить"
CONTACT_BACK = "⬅️ Назад"
CONTACT_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("📱 Отправить номер", request_contact=True)], [CONTACT_BACK, CONTACT_SKIP]],
    resize_keyboard=True, one_time_keyboard=True,
)

PRIVACY_DETAILS = (
    "Что мы собираем: ответы на вопросы о вашем бизнесе, имя и ник в Telegram, контакт, если вы его оставите.\n\n"
    "Зачем: чтобы разобрать ваш бизнес и прислать отчёт — карту бизнеса, схемы процессов и узкие места.\n\n"
    "Кто видит: только мы. Данные не продаём и не передаём третьим лицам. "
    "Голосовые сообщения распознаёт сервис Yandex SpeechKit, аудио у себя мы не храним.\n\n"
    "Как удалить: напишите нам — сотрём ваши ответы и контакт."
)


TYPING_SECONDS = 0.7  # пауза с «печатает…» перед ответом бота
WELCOME_IMAGE = Path(__file__).resolve().parent.parent / "assets" / "bot" / "welcome.jpg"
FINISH_IMAGE = WELCOME_IMAGE.with_name("finish.jpg")

USER_COMMANDS = [
    BotCommand("start", "Начать или продолжить"),
    BotCommand("back", "Вернуться к прошлому вопросу"),
    BotCommand("skip", "Пропустить вопрос"),
    BotCommand("status", "Как дела с моим разбором"),
    BotCommand("restart", "Начать заново"),
]
ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand("report", "Отправить клиенту отчёт: /report chat_id ссылка"),
]


def _admin_id() -> int | None:
    admin = os.getenv("ADMIN_CHAT_ID")
    return int(admin) if admin else None


def _is_admin(update: Update) -> bool:
    return update.effective_chat.id == _admin_id()


def _user(update: Update) -> str:
    u = update.effective_user
    name = " ".join(x for x in (u.first_name, u.last_name) if x)
    return f"{name} (@{u.username})" if u.username else name


def _privacy_button() -> InlineKeyboardButton:
    url = os.getenv("PRIVACY_URL")  # ссылка на полную политику, когда она будет опубликована
    if url:
        return InlineKeyboardButton("📄 Подробнее", url=url)
    return InlineKeyboardButton("📄 Подробнее", callback_data="privacy")


async def _send(update: Update, messages: list[str], markup=None, first_markup=None) -> Message | None:
    """markup — под последним сообщением, first_markup — под первым (если сообщений несколько)."""
    msg = None
    for i, text in enumerate(messages):
        if i == len(messages) - 1:
            reply_markup = markup
        else:
            reply_markup = first_markup if i == 0 else None
        msg = await update.effective_chat.send_message(text, reply_markup=reply_markup)
    return msg


def _keyboard(interview: Interview):
    if interview.finished:
        return ReplyKeyboardRemove()
    kind = interview.pending_kind
    if kind == "consent":
        return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Согласен", callback_data="consent"),
                                      _privacy_button()]])
    if kind == "mode":
        return InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Экспресс, 5 минут", callback_data="mode:express")],
                                     [InlineKeyboardButton("🔍 Полный разбор", callback_data="mode:full")]])
    if kind == "contact":
        return CONTACT_KEYBOARD
    # В callback_data — номер шага: кнопка под старым вопросом не сработает
    row = []
    if interview.can_go_back:
        row.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"back:{interview.step}"))
    row.append(InlineKeyboardButton("⏭ Пропустить", callback_data=f"skip:{interview.step}"))
    return InlineKeyboardMarkup([row])


async def _ask(update: Update, context: ContextTypes.DEFAULT_TYPE, interview: Interview,
               messages: list[str]) -> None:
    """Сообщения бота; кнопки — только под последним вопросом."""
    old = context.chat_data.pop("nav_msg", None)
    if old:
        try:
            await context.bot.edit_message_reply_markup(update.effective_chat.id, old, reply_markup=None)
        except TelegramError:
            pass  # сообщение удалено или кнопок уже нет
    await update.effective_chat.send_action(ChatAction.TYPING)
    await asyncio.sleep(TYPING_SECONDS)
    markup = _keyboard(interview)
    # Ушли с шага «контакт» (назад, заново) — убираем кнопки под полем ввода
    first_markup = None
    if context.chat_data.pop("contact_kb", False) and markup is not CONTACT_KEYBOARD:
        if isinstance(markup, InlineKeyboardMarkup) and len(messages) == 1:
            messages = ["Хорошо."] + messages
        first_markup = ReplyKeyboardRemove()
    if interview.finished and messages and messages[-1] == FINISHED and FINISH_IMAGE.exists():
        # Финальное сообщение — подписью к картинке
        await _send(update, messages[:-1])
        with FINISH_IMAGE.open("rb") as f:
            await update.effective_chat.send_photo(f, caption=FINISHED, reply_markup=markup)
        return
    msg = await _send(update, messages, markup, first_markup)
    if markup is CONTACT_KEYBOARD:
        context.chat_data["contact_kb"] = True
    elif isinstance(markup, InlineKeyboardMarkup) and msg:
        context.chat_data["nav_msg"] = msg.message_id


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
    admin = _admin_id()
    if admin:
        contact = export.get("contact") or {}
        contact_text = " ".join(x for x in (contact.get("name"), contact.get("phone"), contact.get("text")) if x)
        mode = "экспресс" if export.get("mode") == "express" else "полный разбор"
        await context.bot.send_message(
            admin, f"Новые ответы на анкету: {user}\nРежим: {mode}\nКонтакт: {contact_text or 'не оставил'}\n\n"
                   f"Когда отчёт будет готов, отправьте клиенту:\n/report {chat_id} ссылка")
        for path in (md_path, json_path):
            with path.open("rb") as f:
                await context.bot.send_document(admin, f, filename=path.name)


async def _handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Общий путь для текста и распознанного голоса."""
    state = storage.load_session(update.effective_chat.id)
    if not state:
        await start(update, context)
        return
    before = copy.deepcopy(state)
    interview = Interview(state)
    was_finished = interview.finished
    await _ask(update, context, interview, interview.reply(text))
    await _save(update, context, interview, was_finished)
    # Для «Переписать ответ»: состояние до ответа и отпечаток состояния после него
    context.chat_data["undo"] = None if interview.finished else {
        "before": before, "after": json.dumps(interview.state, sort_keys=True),
    }


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = storage.load_session(update.effective_chat.id)
    if state and not state["finished"]:
        interview = Interview(state)
        await _ask(update, context, interview, ["Продолжаем с того места, где остановились. "
                                                "/restart — начать заново.", interview.current_prompt()])
        return
    await restart(update, context)


async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    interview = Interview()
    # Согласие на обработку данных пока выключено; включить — CONSENT_REQUIRED=1 в .env
    messages = interview.start(require_consent=os.getenv("CONSENT_REQUIRED") == "1")
    if transcriber:
        messages[0] += "\n\n🎙 Можно отвечать голосовыми — так быстрее."
    if WELCOME_IMAGE.exists():
        # Приветствие — подписью к картинке, дальше вопросы как обычно
        with WELCOME_IMAGE.open("rb") as f:
            await update.effective_chat.send_photo(f, caption=messages[0])
        messages = messages[1:]
    await _ask(update, context, interview, messages)
    await _save(update, context, interview)
    context.chat_data["undo"] = None


async def consent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    state = storage.load_session(update.effective_chat.id)
    if not state:
        await start(update, context)
        return
    interview = Interview(state)
    await _ask(update, context, interview, interview.accept_consent())
    await _save(update, context, interview)


async def mode_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    state = storage.load_session(update.effective_chat.id)
    if not state:
        await start(update, context)
        return
    interview = Interview(state)
    await _ask(update, context, interview, interview.choose_mode(query.data.split(":")[1]))
    await _save(update, context, interview)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    report = storage.last_report(chat_id)
    if report:
        await _send(update, [f"✅ Ваш разбор готов: {report[0]}"])
        return
    finished_at = storage.last_finished_at(chat_id)
    if finished_at:
        day = datetime.fromisoformat(finished_at).astimezone(reminders.LOCAL_TZ).strftime("%d.%m")
        await _send(update, [f"⏳ Разбор готовится. Ответы получили {day}, обычно это занимает "
                             "до 2 рабочих дней — пришлём ссылку сюда."])
        return
    state = storage.load_session(chat_id)
    if state and not state["finished"]:
        await _send(update, ["Интервью ещё не закончено. /start — продолжить с того же места."])
        return
    await _send(update, ["Вы ещё не проходили разбор. /start — начать."])


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Админ: /report <chat_id> <ссылка> — отправить клиенту готовый отчёт."""
    if not _is_admin(update):
        return
    if len(context.args) != 2 or not context.args[0].lstrip("-").isdigit():
        await _send(update, ["Формат: /report chat_id ссылка\nchat_id есть в сообщении о новых ответах."])
        return
    chat_id, url = int(context.args[0]), context.args[1]
    try:
        await context.bot.send_message(
            chat_id, f"🎉 Ваш разбор бизнеса готов: {url}\n\nЕсли будут вопросы — пишите прямо сюда.")
    except TelegramError as e:
        await _send(update, [f"Не получилось отправить: {e.message}"])
        return
    storage.add_report(chat_id, url)
    await _send(update, ["Отправил ✅"])


async def privacy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await _send(update, [PRIVACY_DETAILS])


async def _navigate(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str) -> None:
    """Пропустить вопрос или вернуться назад — общий путь для команд и кнопок."""
    state = storage.load_session(update.effective_chat.id)
    if not state:
        await start(update, context)
        return
    interview = Interview(state)
    was_finished = interview.finished
    messages = interview.skip() if action == "skip" else interview.back()
    await _ask(update, context, interview, messages)
    await _save(update, context, interview, was_finished)
    context.chat_data["undo"] = None


async def skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _navigate(update, context, "skip")


async def back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _navigate(update, context, "back")


async def nav_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, step = query.data.split(":")
    state = storage.load_session(update.effective_chat.id)
    if not state or Interview(state).step != int(step):
        await query.answer("Этот вопрос уже позади")
        await query.edit_message_reply_markup(None)
        return
    await query.answer()
    await _navigate(update, context, action)


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка «Продолжить» в напоминании."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(None)
    await start(update, context)


async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    state = storage.load_session(update.effective_chat.id)
    if state and state.get("pending", {}) and state["pending"]["kind"] == "contact":
        if text == CONTACT_SKIP:
            await _navigate(update, context, "skip")
            return
        if text == CONTACT_BACK:
            await _navigate(update, context, "back")
            return
    await _handle_answer(update, context, text)


async def contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = storage.load_session(update.effective_chat.id)
    if not state:
        await start(update, context)
        return
    c = update.message.contact
    interview = Interview(state)
    was_finished = interview.finished
    name = " ".join(x for x in (c.first_name, c.last_name) if x)
    await _ask(update, context, interview, interview.set_contact(c.phone_number, name))
    await _save(update, context, interview, was_finished)
    context.chat_data["undo"] = None


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
    await _ask(update, context, interview, ["Хорошо, ответьте ещё раз:", interview.current_prompt()])


async def unsupported_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send(update, ["Такой формат не разберу — запишите голосовое сообщение или напишите текстом."])


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send(update, [f"Ваш chat id: {update.effective_chat.id}\n"
                         "Впишите его в ADMIN_CHAT_ID в .env, чтобы получать ответы клиентов."])


async def _send_reminders(app: Application) -> None:
    now = datetime.now(timezone.utc)
    for chat_id, state, updated_at, reminded in storage.unfinished_sessions():
        if not reminders.due(updated_at, reminded, now):
            continue
        total = len(Interview(state).questions)  # в экспресс-режиме вопросов меньше
        try:
            await app.bot.send_message(chat_id, reminders.text(state, total, reminded), reply_markup=RESUME_BUTTON)
        except Forbidden:
            storage.mark_reminded(chat_id, 99)  # клиент заблокировал бота — больше не пишем
            continue
        storage.mark_reminded(chat_id, reminded + 1)
        log.info("Напоминание №%d отправлено: %s", reminded + 1, chat_id)


async def _reminder_loop(app: Application) -> None:
    shortest = min(reminders.remind_after()).total_seconds()
    every = max(30, min(600, shortest / 4))  # проверяем раз в 10 минут, для коротких сроков чаще
    while True:
        await asyncio.sleep(every)
        try:
            await _send_reminders(app)
        except Exception:
            log.exception("Ошибка при отправке напоминаний")


async def _post_init(app: Application) -> None:
    # Меню команд рядом с полем ввода; у владельца — ещё /report
    await app.bot.set_my_commands(USER_COMMANDS)
    admin = _admin_id()
    if admin:
        await app.bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(admin))
    app.bot_data["reminder_task"] = asyncio.create_task(_reminder_loop(app))


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

    app = Application.builder().token(token).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("restart", restart))
    app.add_handler(CommandHandler("skip", skip))
    app.add_handler(CommandHandler("back", back))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CallbackQueryHandler(undo, pattern="^undo$"))
    app.add_handler(CallbackQueryHandler(consent, pattern="^consent$"))
    app.add_handler(CallbackQueryHandler(mode_button, pattern="^mode:(express|full)$"))
    app.add_handler(CallbackQueryHandler(privacy, pattern="^privacy$"))
    app.add_handler(CallbackQueryHandler(resume, pattern="^resume$"))
    app.add_handler(CallbackQueryHandler(nav_button, pattern=r"^(skip|back):\d+$"))
    app.add_handler(MessageHandler(filters.VOICE, voice))
    app.add_handler(MessageHandler(filters.CONTACT, contact))
    app.add_handler(MessageHandler(filters.AUDIO | filters.VIDEO_NOTE, unsupported_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, answer))
    log.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
