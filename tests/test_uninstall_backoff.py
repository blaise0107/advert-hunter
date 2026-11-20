import unittest

import hunter2 as h


class TestUninstallBackoffKey(unittest.TestCase):
    """Регресс OCR-устойчивости ключа ЧС для uninstall-модалок.

    Иначе «Яндекc» (латинская c), регистр и обрезка имени плодят разные
    ключи — и 400-кратный retry-шторм возвращается через запасные имена.
    """

    def test_yandex_case_variants_same_key(self):
        variants = [
            "удалите приложение «Яндекс» с вашего устройства",
            "удалите приложение «яндекс» с вашего устройства",
            "удалите приложение «Яндекc» с вашего устройства",  # латинская c
            "удалите приложение «Яндекс» с вашего",
        ]
        keys = {h.uninstall_backoff_key(v) for v in variants}
        self.assertEqual(len(keys), 1, f"ключи разошлись: {keys}")

    def test_different_apps_different_key(self):
        a = h.uninstall_backoff_key("удалите приложение «Казино Слоты» с вашего устройства")
        b = h.uninstall_backoff_key("удалите приложение «Яндекс» с вашего устройства")
        self.assertNotEqual(a, b)

    def test_no_name_falls_back_to_stable_text(self):
        # Имя не извлечено (нет кавычек) — ключ не пустой и стабильный.
        text = "чтобы выполнить заказ удалите приложение с вашего устройства"
        key = h.uninstall_backoff_key(text)
        self.assertTrue(key.startswith("uninstall:"))
        self.assertGreater(len(key), len("uninstall:"))
        self.assertEqual(key, h.uninstall_backoff_key(text))


if __name__ == '__main__':
    unittest.main()
