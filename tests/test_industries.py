"""Справки по отраслям: python -m unittest discover tests"""
import unittest

from bot.industries import INDUSTRIES_DIR, detect, load_industries
from bot.interview import Interview
from bot.questionnaire import load_questions

QUESTIONS = {q.id: q for q in load_questions()}
FIRST_BLOCK = load_questions()[0].block_id
INDUSTRIES = load_industries()

# Как клиент описывает бизнес в первых ответах → какую отрасль ждём
SAMPLES = {
    "metalwork": "Делаем металлоконструкции, ворота и навесы, есть лазерная резка и гибка",
    "furniture": "Изготавливаем кухни и шкафы-купе на заказ из ЛДСП и МДФ",
    "windows_doors": "Ставим пластиковые окна, остекление балконов и межкомнатные двери",
    "construction": "Ремонт квартир под ключ, своя бригада и прораб, делаем сметы",
    "building_materials": "Производим газобетон и тротуарную плитку, продаём пиломатериалы",
    "equipment": "Продаём и сдаём в аренду спецтехнику: погрузчики и экскаваторы",
    "advertising_print": "Типография: печать визиток, листовок, баннеров и вывески",
    "wholesale": "Оптовая поставка бытовой химии в торговые точки, мы дистрибьютор",
    "ecommerce": "Интернет-магазин одежды, продаём ещё на Wildberries и Ozon",
    "retail": "Розничный магазин детской одежды в торговом центре",
    "auto": "СТО и детейлинг: ремонт, полировка, керамика",
    "agency": "Агентство: SMM, таргет и разработка сайтов для малого бизнеса",
    "it_crm": "Продаём ПО Битрикс24 и амоцрм, внедряем CRM",
    "professional_services": "Бухгалтерское обслуживание и юридические услуги для ИП",
    "logistics": "Грузоперевозки по РБ и РФ, свои фуры и экспедирование",
    "cleaning": "Клининг офисов и уборка квартир после ремонта",
    "beauty": "Салон красоты: маникюр, брови, ресницы, косметолог",
    "medical": "Стоматологическая клиника, 5 врачей, лечение и имплантация",
    "education": "Детский центр: кружки робототехники, шахматы и подготовка к школе",
    "infobusiness": "Я эксперт по нутрициологии, продаю онлайн-курсы и наставничество через вебинары",
    "fitness": "Фитнес-клуб с бассейном и групповыми занятиями йогой",
    "real_estate": "Агентство недвижимости, риелторы, новостройки и ипотека",
    "travel": "Турагентство: подбираем туры и путёвки, оформляем визы",
    "events": "Организация свадеб и корпоративов, свой ведущий и декор",
    "food_service": "Сеть кофеен и пекарня, есть доставка еды",
}


class GuidesTest(unittest.TestCase):
    def test_all_guides_are_valid(self):
        ids = [i.id for i in INDUSTRIES]
        self.assertEqual(len(ids), len(set(ids)), "id справок повторяются")
        for ind in INDUSTRIES:
            with self.subTest(ind.id):
                self.assertTrue((INDUSTRIES_DIR / f"{ind.id}.yaml").exists(), "id ≠ имя файла")
                self.assertTrue(ind.markers)
                for key in ("sales_model", "funnel", "sales_pains", "questions"):
                    self.assertIn(key, ind.data)
                for qid, followups in ind.questions.items():
                    self.assertIn(qid, QUESTIONS, f"нет такого вопроса в анкете: {qid}")
                    self.assertNotEqual(QUESTIONS[qid].block_id, FIRST_BLOCK,
                                        "в первом блоке отрасль ещё не известна")
                    for f in followups:
                        self.assertTrue(f.text)

    def test_every_industry_has_a_sample(self):
        self.assertEqual(set(SAMPLES), {i.id for i in INDUSTRIES})


class DetectTest(unittest.TestCase):
    def test_samples(self):
        for expected, text in SAMPLES.items():
            with self.subTest(expected):
                found = detect(text)
                self.assertIsNotNone(found, text)
                self.assertEqual(found.id, expected, text)

    def test_more_cases(self):
        cases = {
            "Мойка самообслуживания на 6 постов": "auto",
            "Шиномонтаж и продажа запчастей": "auto",
            "Языковая школа английского для детей и взрослых": "education",
            "Запускаем курсы экспертов, я продюсер, работаем на GetCourse": "infobusiness",
            "Интернет-магазин косметики": "ecommerce",
            "Барбершоп, 4 мастера": "beauty",
            "Веб-студия, делаем лендинги и SEO": "agency",
        }
        for text, expected in cases.items():
            with self.subTest(text):
                found = detect(text)
                self.assertEqual(found.id if found else None, expected)

    def test_unknown_business(self):
        self.assertIsNone(detect("Мы небольшая компания, работаем пять лет"))

    def test_word_start_only(self):
        # «сто» и подобное не должно срабатывать внутри слов
        self.assertIsNone(detect("Стоимость услуг обсуждаем индивидуально"))


class InterviewWithIndustryTest(unittest.TestCase):
    def _run_until(self, iv: Interview, qid: str, answer: str):
        while iv.questions[iv.state["index"]].id != qid:
            iv.reply(answer)

    def test_industry_question_added(self):
        iv = Interview()
        iv.start()
        iv.choose_mode("full")
        iv.reply("Цех металлоконструкций: ворота, навесы, лестницы, лазерная резка")
        self._run_until(iv, "q_sales_flow", "Подробный ответ на вопрос, 10 человек, всё ведём в таблице")
        self.assertEqual(iv.state["industry"], "metalwork")
        out = iv.reply("Заявка приходит на почту, менеджер записывает в таблицу и передаёт инженеру")
        # базовые вопросы анкеты идут первыми, потом — из справки
        asked = [out[0]]
        while iv.questions[iv.state["index"]].id == "q_sales_flow":
            asked.append(iv.reply("Обычно звоним клиенту через пару дней, иногда забываем")[0])
        self.assertTrue(any("чертёж" in q for q in asked), asked)
        self.assertEqual(iv.export()["industry"]["id"], "metalwork")

    def test_no_industry_keeps_base_questionnaire(self):
        iv = Interview()
        iv.start()
        iv.choose_mode("full")
        iv.reply("Небольшая компания, работаем пять лет на рынке")
        self._run_until(iv, "q_sales_flow", "Подробный ответ на вопрос, 10 человек, всё ведём в таблице")
        self.assertIsNone(iv.state["industry"])
        self.assertIsNone(iv.export()["industry"])


if __name__ == "__main__":
    unittest.main()
