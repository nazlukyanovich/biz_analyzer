"""Согласие, режимы, контакт, удаление данных, напоминания и отчёты: python -m unittest discover tests"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot import reminders
from bot.export import to_markdown
from bot.interview import CONSENT_REMIND, CONTACT, FINISHED, MODE_REMIND, Interview
from bot.questionnaire import load_express_ids
from bot.storage import Storage

ANSWER = ("Отвечаю подробно: 12 человек, всё фиксируем в CRM, клиент не ответил — перезваниваем, "
          "статус пишем клиенту, теряем на доставке, отваливаются на цене, задачи в чате, ошибки бывают")


def _to_contact_step() -> Interview:
    iv = Interview()
    iv.start()
    iv.choose_mode("full")
    while iv.pending_kind != "contact":
        iv.reply(ANSWER)
    return iv


class ConsentTest(unittest.TestCase):
    def test_no_consent_by_default(self):
        iv = Interview()
        out = iv.start()
        self.assertEqual(iv.pending_kind, "mode")
        self.assertIn("Экспресс", out[-1])
        self.assertEqual(iv.reply("давай"), [MODE_REMIND])
        out = iv.choose_mode("full")
        self.assertIn("(1/", out[0])
        self.assertFalse(iv.can_go_back)  # к выбору режима назад не возвращаемся

    def test_consent_when_enabled(self):
        iv = Interview()
        out = iv.start(require_consent=True)
        self.assertEqual(iv.pending_kind, "consent")
        self.assertIn("персональных данных", out[-1])
        self.assertEqual(iv.reply("Флористика"), [CONSENT_REMIND])
        self.assertEqual(iv.skip(), [CONSENT_REMIND])
        out = iv.accept_consent()
        self.assertIn("Экспресс", out[0])
        self.assertIsNotNone(iv.state["consent_at"])


class ExpressTest(unittest.TestCase):
    def _express(self) -> Interview:
        iv = Interview()
        iv.start()
        iv.choose_mode("express")
        return iv

    def test_only_express_questions(self):
        iv = self._express()
        self.assertEqual([q.id for q in iv.questions], load_express_ids())
        self.assertIn(f"(1/{len(load_express_ids())})", iv.current_prompt())

    def test_one_clarification_no_followups(self):
        iv = self._express()
        iv.reply("цветы")  # размыто → одно уточнение
        out = iv.reply("букеты")  # всё ещё размыто, но второго уточнения нет
        self.assertIn("(2/", out[0])
        while iv.questions[iv.state["index"]].id != "q_sales_flow":
            iv.skip()
        out = iv.reply("Заявка приходит в директ, менеджер за десять минут отвечает, уточняет задачу, "
                       "записывает в таблицу, выставляет счёт, а клиент оплачивает по ссылке")
        self.assertIn("(8/", out[0])  # углубляющих вопросов нет

    def test_full_run_and_export(self):
        iv = self._express()
        while not iv.finished:
            iv.reply(ANSWER)
        export = iv.export()
        self.assertEqual(export["mode"], "express")
        self.assertEqual(len(export["items"]), len(load_express_ids()))
        self.assertIn("Режим: экспресс", to_markdown(export))

    def test_express_state_survives_restart(self):
        iv = self._express()
        iv.reply(ANSWER)
        iv = Interview(json.loads(json.dumps(iv.state)))
        self.assertTrue(iv.express)
        self.assertIn("(2/", iv.current_prompt())


class ContactTest(unittest.TestCase):
    def test_contact_asked_after_last_question(self):
        iv = _to_contact_step()
        self.assertEqual(iv.current_prompt(), CONTACT)
        self.assertFalse(iv.finished)

    def test_contact_by_button(self):
        iv = _to_contact_step()
        self.assertEqual(iv.set_contact("+375291234567", "Иван"), [FINISHED])
        self.assertTrue(iv.finished)
        export = iv.export()
        self.assertEqual(export["contact"], {"phone": "+375291234567", "name": "Иван"})
        self.assertIn("Контакт: Иван +375291234567", to_markdown(export))

    def test_contact_by_text_and_skip(self):
        iv = _to_contact_step()
        iv.reply("пишите в телеграм @ivan")
        self.assertEqual(iv.export()["contact"], {"text": "пишите в телеграм @ivan"})
        iv = _to_contact_step()
        iv.skip()
        self.assertTrue(iv.finished)
        self.assertIn("Контакт: не оставил", to_markdown(iv.export()))

    def test_back_from_contact(self):
        iv = _to_contact_step()
        iv.back()
        self.assertNotEqual(iv.pending_kind, "contact")
        iv.reply(ANSWER)
        self.assertEqual(iv.pending_kind, "contact")


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name))

    def tearDown(self):
        self.storage.db.close()
        self.tmp.cleanup()

    def test_delete_user(self):
        iv = _to_contact_step()
        iv.skip()
        export = iv.export()
        self.storage.save_session(1, "Тест", iv.state)
        self.storage.save_finished(1, "Тест", export, to_markdown(export))
        self.storage.save_finished(2, "Другой", export, to_markdown(export))
        self.assertEqual(self.storage.delete_user(1), 2)  # .json и .md
        self.assertIsNone(self.storage.load_session(1))
        self.assertEqual(len(list(self.storage.answers_dir.glob("2_*"))), 2)  # чужие на месте
        rows = self.storage.db.execute("SELECT chat_id FROM interviews").fetchall()
        self.assertEqual(rows, [(2,)])

    def test_reminder_counter_resets_on_answer(self):
        iv = Interview()
        iv.start()
        self.storage.save_session(1, "Тест", iv.state)
        self.storage.mark_reminded(1, 1)
        self.assertEqual(self.storage.unfinished_sessions()[0][3], 1)
        self.storage.save_session(1, "Тест", iv.state)
        self.assertEqual(self.storage.unfinished_sessions()[0][3], 0)


class RemindersTest(unittest.TestCase):
    AFTER = [timedelta(hours=24), timedelta(hours=72)]
    # 12:00 по Минску
    NOON = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)

    def test_due_after_a_day(self):
        self.assertFalse(reminders.due(self.NOON - timedelta(hours=23), 0, self.NOON, self.AFTER))
        self.assertTrue(reminders.due(self.NOON - timedelta(hours=25), 0, self.NOON, self.AFTER))

    def test_second_after_three_days_then_stop(self):
        self.assertFalse(reminders.due(self.NOON - timedelta(hours=48), 1, self.NOON, self.AFTER))
        self.assertTrue(reminders.due(self.NOON - timedelta(hours=73), 1, self.NOON, self.AFTER))
        self.assertFalse(reminders.due(self.NOON - timedelta(days=30), 2, self.NOON, self.AFTER))

    def test_not_at_night(self):
        night = datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc)  # 23:00 по Минску
        self.assertFalse(reminders.due(night - timedelta(days=2), 0, night, self.AFTER))

    def test_text(self):
        total = len(Interview().questions)
        iv = Interview()
        iv.start()
        self.assertIn("так и не начали", reminders.text(iv.state, total, 0))
        iv.choose_mode("full")
        iv.reply(ANSWER)
        self.assertIn(f"вопросе 2 из {total}", reminders.text(iv.state, total, 0))
        self.assertIn("последний раз", reminders.text(iv.state, total, 1))
        iv = _to_contact_step()
        self.assertIn("последний шаг", reminders.text(iv.state, total, 0).lower())



class ReportStorageTest(unittest.TestCase):
    def test_report_and_status_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp))
            self.assertIsNone(storage.last_report(1))
            iv = _to_contact_step()
            iv.skip()
            export = iv.export()
            storage.save_finished(1, "Тест", export, to_markdown(export))
            self.assertEqual(storage.last_finished_at(1), export["finished_at"])
            storage.add_report(1, "https://example.com/r1")
            self.assertEqual(storage.last_report(1)[0], "https://example.com/r1")
            storage.delete_user(1)
            self.assertIsNone(storage.last_report(1))
            storage.db.close()


if __name__ == "__main__":
    unittest.main()
