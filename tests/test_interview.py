"""Проверка логики интервью без Telegram: python -m unittest discover tests"""
import json
import unittest

from bot.clarifier import is_vague
from bot.export import to_markdown
from bot.interview import Interview
from bot.questionnaire import load_questions

QUESTIONS = {q.id: q for q in load_questions()}


class ClarifierTest(unittest.TestCase):
    def test_short_answer_is_vague(self):
        self.assertTrue(is_vague(QUESTIONS["q_company"], "цветы"))

    def test_detailed_answer_is_ok(self):
        self.assertFalse(is_vague(QUESTIONS["q_company"], "Флора Люкс, собираем и доставляем букеты по Минску"))

    def test_number_expected(self):
        self.assertTrue(is_vague(QUESTIONS["q_size"], "небольшая команда"))
        self.assertFalse(is_vague(QUESTIONS["q_size"], "7 человек"))
        self.assertFalse(is_vague(QUESTIONS["q_size"], "пять человек"))
        self.assertTrue(is_vague(QUESTIONS["q_products"], "Продаем ПО битрикс24 и амоцрм, настраиваем их"))
        self.assertFalse(is_vague(QUESTIONS["q_products"], "Битрикс24 и настройка, чек 2000$"))
        self.assertFalse(is_vague(QUESTIONS["q_products"], "Внедрение CRM, средний чек 3к рублей"))
        self.assertTrue(is_vague(QUESTIONS["q_products"], "Продаем лицензии 1С и внедряем их клиентам"))

    def test_vague_marker(self):
        self.assertTrue(is_vague(QUESTIONS["q_decisions"], "ну по-разному бывает"))

    def test_honest_no(self):
        self.assertFalse(is_vague(QUESTIONS["q_double_entry"], "нет"))


class InterviewTest(unittest.TestCase):
    def test_vague_answer_gets_clarification_then_moves_on(self):
        iv = Interview()
        iv.start()
        out = iv.reply("цветы")
        self.assertIn("что конкретно", out[0].lower())
        out = iv.reply("Собираем и доставляем букеты по городу за два часа")
        self.assertIn("(2/", out[0])

    def test_max_two_clarifications(self):
        iv = Interview()
        iv.start()
        iv.state["index"] = list(QUESTIONS).index("q_products")
        iv._ask_main(QUESTIONS["q_products"])
        iv.reply("букеты")
        iv.reply("разные")
        out = iv.reply("не знаю")
        self.assertIn("(3/", out[0])
        self.assertTrue(iv.state["records"]["q_products"]["needs_review"])

    def test_followups_skip_what_was_already_said(self):
        iv = Interview()
        iv.start()
        iv.state["index"] = list(QUESTIONS).index("q_sales_flow")
        iv._ask_main(QUESTIONS["q_sales_flow"])
        out = iv.reply(
            "Заявка приходит в инстаграм, менеджер отвечает за 10 минут, записывает в таблицу, "
            "согласует букет и отправляет ссылку на оплату"
        )
        # время ответа и таблица уже названы — сразу вопрос про «подумаю»
        self.assertIn("подумаю", out[0])

    def test_full_run_and_state_roundtrip(self):
        iv = Interview()
        iv.start()
        while not iv.finished:
            state = json.loads(json.dumps(iv.state))  # как будто бот перезапустили
            iv = Interview(state)
            iv.reply("Отвечаю подробно: 12 человек, всё фиксируем в CRM, клиент не ответил — перезваниваем, "
                     "статус пишем клиенту, теряем на доставке, отваливаются на цене, задачи в чате, ошибки бывают")
        export = iv.export()
        self.assertEqual(len(export["items"]), len(QUESTIONS))
        self.assertIn("## Знакомство", to_markdown(export, "Тест"))

    def test_skip(self):
        iv = Interview()
        iv.start()
        out = iv.skip()
        self.assertIn("(2/", out[0])
        self.assertTrue(iv.state["records"]["q_company"]["skipped"])


if __name__ == "__main__":
    unittest.main()
