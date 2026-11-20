"""Мини-тесты core/shared.py — по предложению Qwen 4 MAX (секция B).

Запуск: python -m unittest discover -s tests -v
"""
import unittest

from core import shared


class FakeProc:
    def __init__(self, text):
        self.stdout = text.encode("utf-8")


class TestShared(unittest.TestCase):
    def test_page_state_empty(self):
        self.assertEqual(shared.page_state([]), (1, 1))

    def test_page_state_dark_is_current(self):
        # (x, y, яркость) — текущая страница = самая тёмная (последняя)
        self.assertEqual(
            shared.page_state([(10, 342, 90), (20, 342, 40)]), (2, 2))
        self.assertEqual(
            shared.page_state([(10, 342, 40), (20, 342, 90)]), (1, 2))

    def test_norm(self):
        self.assertEqual(shared.norm("Привет ЁЖ"), "привет еж")

    def test_extract_search_query(self):
        self.assertEqual(
            shared.extract_search_query(
                "Поисковый запрос | микрозайм онлайн | Скопировать"),
            "микрозайм онлайн")

    def test_third_party(self):
        self.assertEqual(
            shared.third_party_packages(
                lambda *a: FakeProc("package:ru.a\npackage:ru.b\n")),
            ["ru.a", "ru.b"])


if __name__ == "__main__":
    unittest.main()
