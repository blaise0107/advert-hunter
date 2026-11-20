# -*- coding: utf-8 -*-
"""Чтение баланса AdvertApp с навигацией по интерфейсу.

Баланс в приложении виден в двух местах:
  * на главной странице сверху (строка «Баланс»);
  * «Деньги» → «Карта» → «Ваш баланс».

Модуль сам приводит приложение в главное меню, ищет строку баланса и,
если её нет, проходит по указанному маршруту. Кнопку «Вывести» не трогает.
"""
from __future__ import annotations

import re
import time

from core import device

try:
    import hunter2 as hunter
except Exception:  # панель должна работать и без OCR
    hunter = None

APP = "com.advert.mobileearnings.app"
MONEY_WORDS = ("деньги", "кошелёк")
CARD_WORDS = ("карта", "карту", "карты")

MONEY = r"(\d[\d\s.,]*)\s*(?:₽|руб\w*)"
BALANCE_RE = re.compile(
    r"(?:ваш\s+баланс|баланс|доступн\w*|к\s+выводу|можно\s+вывести)"
    r"[^0-9]{0,40}" + MONEY,
    re.IGNORECASE,
)
TOTAL_RE = re.compile(
    r"заработано\s+всего[^0-9]{0,40}" + MONEY,
    re.IGNORECASE,
)


def _text(lines) -> str:
    return " ".join(str(line.get("text", "")) for line in lines)


def _number(raw: str) -> str:
    value = raw.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    value = value.rstrip(".")
    try:
        return f"{float(value):.2f}"
    except ValueError:
        return raw.strip()


def parse(text: str) -> tuple[str | None, str | None]:
    """Возвращает ("balance"|"total", значение) или (None, None)."""
    match = BALANCE_RE.search(text)
    if match:
        return "balance", _number(match.group(1))
    match = TOTAL_RE.search(text)
    if match:
        return "total", _number(match.group(1))
    return None, None


def _ensure_app() -> None:
    device.wake()
    if not device.app_in_foreground():
        device.start_app()
        time.sleep(4)


def _to_menu(max_back: int = 6) -> list | None:
    """Возвращает строки OCR главного меню или None."""
    for _ in range(max_back):
        kind, _a, _path, lines = hunter.read_screen("bal_nav.png", check_focus=False)
        if kind == "menu":
            return lines
        if not device.app_in_foreground():
            # Приложение закрылось или ещё не поднялось — запускаем и ждём.
            device.start_app()
            time.sleep(3)
            continue
        low = _text(lines).lower()
        if ("подключ" in low and "сервер" in low) or "failed to connect" in low:
            # Окно «Нет подключения к серверу»: жмём «Повтор/Connect».
            pos = _tap_word(lines, ("повтор", "повторить", "connect"))
            if pos:
                hunter.tap(*pos)
                time.sleep(3)
                continue
        hunter.key_back()
        time.sleep(0.8)
    kind, _a, _path, lines = hunter.read_screen("bal_nav.png", check_focus=False)
    return lines if kind == "menu" else None


def _tap_word(lines, words: tuple[str, ...]):
    for word in words:
        pos = hunter.find_word(lines, word)
        if pos:
            return pos
    return None


def read_balance() -> tuple[bool, str]:
    """Возвращает (ok, "BALANCE|13.63" | "BALANCE_TOTAL|..." | текст ошибки)."""
    if hunter is None:
        return False, "OCR-модуль hunter2.py недоступен."
    try:
        from core import processes

        if processes.bot_pids():
            return False, ("Идёт цикл бота: навигация по экрану помешает ему. "
                           "Сначала остановите цикл, потом считайте баланс.")
    except Exception:
        pass
    try:
        _ensure_app()
        lines = _to_menu()
        if lines is None:
            return False, "Не удалось выйти в главное меню AdvertApp."
        kind, value = parse(_text(lines))
        if kind == "balance":
            return True, "BALANCE|" + value

        pos = _tap_word(lines, MONEY_WORDS)
        if pos:
            hunter.tap(*pos)
            time.sleep(2.5)
            _kind, _a, _path, money_lines = hunter.read_screen(
                "bal_money.png", check_focus=False)
            kind, value = parse(_text(money_lines))
            if kind == "balance":
                return True, "BALANCE|" + value

            pos = _tap_word(money_lines, CARD_WORDS)
            if pos:
                hunter.tap(*pos)
                time.sleep(2.5)
                _kind, _a, _path, card_lines = hunter.read_screen(
                    "bal_card.png", check_focus=False)
                kind, value = parse(_text(card_lines))
                if kind == "balance":
                    return True, "BALANCE|" + value
                kind, value = parse(_text(card_lines))
                if kind == "total":
                    return True, "BALANCE_TOTAL|" + value

        if kind == "total":
            return True, "BALANCE_TOTAL|" + value
        return False, "Баланс не найден ни на главной, ни в «Деньги» → «Карта»."
    except Exception as error:  # noqa: BLE001 - текст показываем в панели
        return False, f"Ошибка чтения баланса: {error}"
