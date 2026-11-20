"""Мини-тесты core/shared.py — по предложению Qwen 4 MAX (секция B).

Запуск: python -m unittest discover -s tests -v
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

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


class TestPredicatesAndClock(unittest.TestCase):
    """Qwen Q2.3: детерминированные часы + инвариант двух предикатов."""

    def _clock(self):
        clk = SimpleNamespace(t=1000.0)
        clk.monotonic = lambda: clk.t
        def _sleep(s):
            clk.t += s
        return clk, _sleep

    def test_go_to_page_taps_with_deterministic_clock(self):
        clk, _sleep = self._clock()
        fake_dev = SimpleNamespace(
            read_screen=lambda n: ("list", "PAGE1", "", []),
            tap=lambda x, y: None,
            swipe=lambda *a: None,
        )
        with patch("core.shared.time", SimpleNamespace(
                monotonic=clk.monotonic, sleep=_sleep)):
            ok = shared.go_to_page(
                1, 2, fake_dev, lambda k, a: True,
                lambda top, target: not top,
                buttons_fn=lambda a: [(100, 342, 40)])
        self.assertTrue(ok)

    def test_predicate_invariant_hunter_vs_tasks(self):
        # Главный инвариант B: один и тот же top/target даёт разное решение.
        top = [(100, 342, 50)]
        target = 2
        hunter_scroll = lambda top, target: len(top) < target  # 1 < 2 -> True
        tasks_scroll = lambda top, target: not top             # top есть -> False
        self.assertTrue(hunter_scroll(top, target))
        self.assertFalse(tasks_scroll(top, target))
