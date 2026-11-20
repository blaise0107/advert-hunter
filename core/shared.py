"""Общие функции hunter2/tasks. Создано apply_shared.py."""
import re
import time
import numpy as np


def norm(s):
    return s.lower().replace("ё", "е")


def page_buttons(a):
    """Кнопки страниц «1 2 …» у списка: [(x, y, яркость)].

    Список заказов разбит на страницы по 20 штук. Кнопки — цветные
    квадраты под заголовком, а после прокрутки до конца — внизу.
    Выбранная страница темнее остальных.
    """
    if a is None:
        return []
    for y0, y1 in ((280, 420), (2090, 2230)):
        band = a[y0:y1]
        if band.size == 0:
            continue
        spread = band.max(axis=2).astype(int) - band.min(axis=2).astype(int)
        mask = (spread > 45) & (band.max(axis=2) > 100)
        columns = np.flatnonzero(mask.sum(axis=0) >= 15)
        if not len(columns):
            continue
        found = []
        for group in np.split(columns, np.flatnonzero(np.diff(columns) > 4) + 1):
            left, right = int(group[0]), int(group[-1]) + 1
            if not 55 <= right - left <= 95:
                continue
            if not 400 <= left + (right - left) // 2 <= 700:
                continue
            rows = np.flatnonzero(mask[:, left:right].sum(axis=1) >= 10)
            if not len(rows) or not 55 <= int(rows[-1]) - int(rows[0]) + 1 <= 95:
                continue
            zone = band[rows[0]:rows[-1] + 1, left:right]
            found.append((left + (right - left) // 2,
                          y0 + int((rows[0] + rows[-1]) // 2),
                          float(zone.mean())))
        if found:
            found.sort(key=lambda b: b[0])
            return found[:6]
    return []


def extract_search_query(text):
    """Достаёт поисковый запрос из инструкции «Зайдите в Google Play»."""
    text = norm(text)
    # OCR-строки склеены через « | »: запрос обычно лежит следующей строкой
    # после «Поисковый запрос:» и может быть кириллическим («займ онлайн»).
    parts = [part.strip(" .:—-") for part in re.split(r"[|\n]", text)]
    for i, part in enumerate(parts):
        if "поисков" in part and "запрос" in part:
            tail = re.sub(r"^.*?запрос\w*\s*[:：]?\s*", "", part)
            if tail and not tail.startswith(("скопир", "ваше", "зайд")):
                return tail
            for nxt in parts[i + 1:]:
                if not nxt or nxt.startswith(
                        ("скопир", "ваше", "зайд", "из буфера")):
                    continue
                return nxt
    match = re.search(
        r"поисков\w*\s+запрос\w*[^a-z0-9]{0,14}([a-z0-9][a-z0-9_.\-]{1,30})",
        text)
    if match:
        return match.group(1).strip(" .-")
    match = re.search(r"встав\w*[^a-z0-9]{0,20}([a-z0-9][a-z0-9_.\-]{1,30})", text)
    return match.group(1).strip(" .-") if match else None


def third_party_packages(adb):
    out = adb("shell", "pm list packages -3").stdout.decode("utf-8", "replace")
    return [line.split(":", 1)[1].strip() for line in out.splitlines()
            if line.startswith("package:")]




def page_state(buttons):
    if not buttons:
        return 1, 1
    dark = min(buttons, key=lambda b: b[2])
    return buttons.index(dark) + 1, len(buttons)


def go_to_page(target, timeout, dev, screen_ok, scroll_when, buttons_fn=None):
    buttons_fn = buttons_fn or page_buttons
    end = time.monotonic(); last_tap = 0.0; last_scroll = 0.0
    while time.monotonic() < end:
        kind, a, path, lines = dev.read_screen("pg.png")
        if screen_ok(kind, a):
            buttons = buttons_fn(a)
            if page_state(buttons)[0] == target:
                return True
            top = [b for b in buttons if b[1] < 1000]
            if target <= len(top) and time.monotonic() - last_tap > 2.5:
                dev.tap(top[target - 1][0], top[target - 1][1]); last_tap = time.monotonic()
            elif scroll_when(top, target) and time.monotonic() - last_scroll > 0.8:
                dev.swipe(540, 450, 540, 2000, 600); last_scroll = time.monotonic()
        time.sleep(0.5)
    return False
