"""Задания по активным заказам AdvertApp.

Что делает:
  1. Открывает «Активные», находит заказы с зелёным действием
     («Открыть на 2 день» подсвечено зелёным).
  2. Открывает карточку, находит зелёную кнопку «Открыть», нажимает её,
     запоминает пакет запущенного приложения и держит его в фокусе
     столько, сколько требует задание (60 секунд / 2 минуты).
  3. Заказы, пропавшие из «Активных» и появившиеся в «Истории», завершённые:
     приложение удаляется, только если пакет известен, не нужен другому
     активному заказу и уже есть в бэкапе Swift Backup.

Запуск:
    python tasks.py            # один проход
    python tasks.py --dry-run  # только показать, что нашёл
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timedelta

import numpy as np
from PIL import Image

# Консоль Windows в cp1251 не умеет стрелки в логах — принудительно UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hunter2 as h
try:
    from core.config import load_settings as _load_panel_settings
except Exception:  # бот должен работать и без нового пакета core
    def _load_panel_settings():
        return {}


def active_strategy():
    """Возвращает (предел страниц, сколько пустых экранов подряд останавливают)."""
    settings = _load_panel_settings() or {}
    cfg = settings.get("active", {}) if isinstance(settings, dict) else {}
    try:
        max_pages = int(cfg.get("max_pages", 0) or 0)
    except (TypeError, ValueError):
        max_pages = 0
    try:
        hard_cap = max(1, int(cfg.get("hard_cap", 25) or 25))
    except (TypeError, ValueError):
        hard_cap = 25
    try:
        empty_stop = max(1, int(cfg.get("empty_stop", 2) or 2))
    except (TypeError, ValueError):
        empty_stop = 2
    return (max_pages if max_pages > 0 else hard_cap), empty_stop

MAP_PATH = os.path.join(h.BASE_DIR, "order_apps.json")
# Постоянная база «заказ → приложение». Не чистится вместе с order_apps.json:
# задания часто повторяются, и при повторе приложение сразу ставится из бэкапа.
KNOWN_PATH = os.path.join(h.BASE_DIR, "known_apps.json")
# Когда заказ последний раз видели в «Активных». Нужен, чтобы не удалить
# приложение, если карточка просто не попала в кадр при сканировании.
SEEN_PATH = os.path.join(h.BASE_DIR, "seen_orders.json")
# Отметки hunter2.py о заказах, взятых из «Новых»: такие идут первыми.
NEW_TAKEN_PATH = os.path.join(h.BASE_DIR, "new_taken.json")
SWIFT_ROOT = "/sdcard/SwiftBackup/accounts"
SWIFT_PKG = "org.swiftapps.swiftbackup"
SWIFT_HOME = "org.swiftapps.swiftbackup/.intro.IntroActivity"

# Слова, которые встречаются в половине пакетов и не годятся для поиска.
GENERIC = {"com", "org", "net", "android", "app", "apps", "mobile", "client", "ru", "en"}

# Если вместо целевого приложения открылся магазин или системный диалог,
# пакет нельзя записывать как приложение заказа.
NOT_TARGET = {
    "com.android.vending",
    "com.android.chrome",
    "com.android.packageinstaller",
    "com.google.android.packageinstaller",
    "com.android.permissioncontroller",
    "com.google.android.permissioncontroller",
    "com.android.settings",
    "com.android.systemui",
    *h.APP_PACKAGES,
}


def green_ratio(a, line):
    """Доля зелёных пикселей в строке — так отличается активное действие."""
    words = line.get("words") or []
    if words:
        x0 = max(0, min(w["x"] for w in words))
        y0 = max(0, min(w["y"] for w in words))
        x1 = min(a.shape[1], max(w["x"] + w["w"] for w in words))
        y1 = min(a.shape[0], max(w["y"] + w["h"] for w in words))
    else:
        x0, y0 = max(0, line["x"]), max(0, line["y"])
        x1, y1 = min(a.shape[1], x0 + 200), min(a.shape[0], y0 + 40)
    band = a[y0:y1, x0:x1]
    if band.size == 0:
        return 0.0
    return float(h.green_mask(band).mean())


def restart_to_menu():
    """Крайняя мера: перезапуск AdvertApp без очистки данных."""
    h.adb("shell", "input keyevent KEYCODE_WAKEUP")
    h.adb("shell", "wm dismiss-keyguard")
    h.adb("shell", "am force-stop " + h.active_app())
    h.start_app()
    time.sleep(5)
    kind, a, path, lines = h.read_screen("tm.png", check_focus=False)
    return (a, path, lines) if kind == "menu" else (None, None, None)


def ensure_menu(timeout=20):
    """Возвращает меню, не выходя из приложения вслепую.

    После `am start` приложение может ещё грузиться. Слепые BACK в этот
    момент выходят из AdvertApp, поэтому BACK жмём только когда экран уже
    распознан как список/карточка/активные.
    """
    end = time.monotonic() + timeout
    restarted = False
    while time.monotonic() < end:
        if not h.app_in_focus():
            if restarted:
                time.sleep(1.0)
                continue
            h.start_app()
            restarted = True
            time.sleep(3.0)
            continue
        if h.dismiss_keyboard():
            time.sleep(0.4)
            continue
        kind, a, path, lines = h.read_screen("tm.png", check_focus=False)
        if kind == "menu":
            return a, path, lines
        if kind == "modal":
            # Не игнорируем: напоминание об уже взятом заказе открываем,
            # предложение нового — принимаем и помечаем для «Активных».
            h.handle_dialog("unprompted", a, path, "navigation", navigation=True)
            end = max(end, time.monotonic() + 10)
            time.sleep(0.5)
            continue
        if kind in ("list", "active", "detail"):
            try:
                h.key_back()
            except RuntimeError:
                h.tap(*h.BACK_BTN)
            time.sleep(1.0)
            continue
        # other/none — приложение ещё грузится или показывает заставку.
        time.sleep(0.7)
    return restart_to_menu()


def restart_app_only():
    """Перезапуск AdvertApp без очистки данных (ввод иногда зависает)."""
    h.adb("shell", "am force-stop " + h.active_app())
    h.start_app()
    time.sleep(4)


def active_wait_seconds():
    """Сколько ждать открытия «Активных»: сервер AdvertApp отвечает медленно."""
    try:
        value = int((h._load_panel_settings() or {}).get("active", {}).get("open_wait_seconds", 75))
    except (TypeError, ValueError):
        value = 75
    return max(15, min(300, value))


def open_active():
    wait = active_wait_seconds()
    for attempt in range(3):
        a, path, lines = ensure_menu()
        if a is None:
            continue
        pos = h.find_word(lines, "активные")
        if not pos:
            continue
        h.tap(*pos)
        end = time.monotonic() + wait
        retap = False
        while time.monotonic() < end:
            kind, a, path, lines = h.read_screen("al.png")
            if is_active_screen(kind, a):
                return a, path, lines
            if kind == "modal":
                # Всплывающее окно с предложением перекрыло меню и съело
                # тап: разбираем окно и повторяем тап по «Активные».
                h.handle_dialog("unprompted", a, path, "navigation", navigation=True)
                retap = True
                break
            time.sleep(0.3)
        if retap:
            print("окно перекрыло меню — повторяю тап по «Активные»")
            time.sleep(0.5)
            continue
        # Экран не изменился за отведённое время. Раньше здесь был
        # перезапуск приложения, но серверу нужно до минуты: перезапуск
        # сбивал загрузку. Теперь просто ждём и пробуем ещё раз.
        print(f"«Активные» не открылись за {wait} с — повторяю")
        time.sleep(2)
    print("не удалось открыть «Активные»: ввод в приложении не работает")
    return None, None, None


STEP_WORDS = ("найти", "открыть", "установить", "загрузить", "скачать")
# За один cleanup бэкапим не больше этого числа заказов: 13 бэкапов подряд
# занимают ~7 минут и блокируют всю стратегию.
CLEANUP_MAX_ORDERS = 3
# OCR читает галочку выполненного шага как «Э» (иногда «З» или «✓»).
DONE_MARKS = "ЭэЗз✓✔Vv"


def step_done(text):
    """Отмечен ли шаг галочкой (значит, он уже выполнен)."""
    cleaned = (text or "").lstrip("«(\\[ ")
    return bool(cleaned) and cleaned[0] in DONE_MARKS


def _box_pixels(a, word):
    """Пиксели рамки OCR-слова (None, если рамка пустая)."""
    x0, y0 = int(word["x"]), int(word["y"])
    x1 = x0 + int(word.get("w", 0))
    y1 = y0 + int(word.get("h", 0))
    if x1 <= x0 or y1 <= y0:
        return None
    band = a[max(0, y0):y1, max(0, x0):x1]
    if band.size == 0:
        return None
    return band.reshape(-1, 3).astype(int)


def step_marker(a, line):
    """Цвет кружка-маркера шага: 'green' / 'yellow' / '' — неизвестно.

    У выполненного шага кружок зелёный с галочкой (0,222,42). У текущего
    шага цвет зависит от сборки: teamdev — жёлтый кружок с часиками
    (255,204,0), старая сборка — кружок пустой (цвет фона). По тексту их
    не различить: OCR читает оба одинаково как «О». Маркер — отдельное
    слово строки, его рамка — words[0].
    """
    words = line.get("words") or []
    if not words:
        return ""
    word = words[0]
    token = h.norm(word.get("t", "")).strip()
    if not token or len(token) > 2:
        return ""
    flat = _box_pixels(a, word)
    if flat is None:
        return ""
    red, green, blue = flat[:, 0], flat[:, 1], flat[:, 2]
    yellow = (red > 180) & (green > 120) & (blue < 120)
    is_green = (green > 140) & (red < 120) & (blue < 120)
    need = max(3, int(flat.shape[0] * 0.05))
    if int(yellow.sum()) >= need:
        return "yellow"
    if int(is_green.sum()) >= need:
        return "green"
    return ""


def step_text_green(a, line):
    """Зелёная подпись шага — на Realme/старой сборке так выделен текущий шаг."""
    words = (line.get("words") or [])[1:]
    if not words:
        return False
    for word in words:
        flat = _box_pixels(a, word)
        if flat is None:
            continue
        red, green, blue = flat[:, 0], flat[:, 1], flat[:, 2]
        is_green = (green > 120) & (green > red + 25) & (green > blue + 25)
        need = max(2, int(flat.shape[0] * 0.04))
        if int(is_green.sum()) >= need:
            return True
    return False


def step_text_active(a, line):
    """Подпись шага выделена как текущая (готова к действию).

    Realme: текст шага становится зелёным. Samsung: активный шаг выделяется
    тёмным/чёрным текстом, тогда как неготовые будущие шаги остаются серыми.
    Жёлтый кружок-маркер сам по себе не гарантирует готовность (он бывает и у
    будущих шагов «Открыть на N день»), поэтому при жёлтом маркере дополнительно
    проверяем, что текст действительно активный.
    """
    if step_text_green(a, line):
        return True
    words = (line.get("words") or [])[1:]
    if not words:
        return False
    dark_hits = 0
    checked = 0
    for word in words:
        flat = _box_pixels(a, word)
        if flat is None or flat.shape[0] < 8:
            continue
        checked += 1
        red, green, blue = flat[:, 0], flat[:, 1], flat[:, 2]
        bright = np.maximum(np.maximum(red, green), blue)
        spread = bright - np.minimum(np.minimum(red, green), blue)
        # Тёмные нейтральные пиксели текста (чёрный/очень тёмно-серый) на
        # светлом фоне карточки — признак активного шага на Samsung.
        dark_text = (bright >= 18) & (bright <= 110) & (spread <= 35)
        # Неактивный серый текст обычно заметно светлее; зелёный уже пойман выше.
        need = max(3, int(flat.shape[0] * 0.05))
        if int(dark_text.sum()) >= need:
            dark_hits += 1
    # Требуем устойчивый признак на словах подписи, чтобы не реагировать на шум.
    return checked > 0 and dark_hits >= max(1, (checked + 1) // 2)


def step_state(a, line):
    """Состояние шага: 'done' (выполнен), 'pending' (ждёт), '' — неизвестно.

    Зелёный кружок с галочкой = шаг выполнен (одинаково на обеих сборках).
    Текущий шаг: teamdev — жёлтый кружок, старая сборка — кружок пустой,
    но подпись шага зелёная/активная. Жёлтый маркер без активной подписи
    (серый текст будущего шага «Открыть на N день») не считаем готовым.
    """
    # По логике пользователя маркеры (жёлтые часы/зелёная галочка) — не
    # показатель готовности. Главный признак: ЗЕЛЁНЫЙ (активный) ТЕКСТ шага.
    # Такой шаг нужно выполнять сразу, даже при жёлтом маркере.
    if step_text_active(a, line):
        return "pending"
    # Зелёный маркер = шаг уже выполнен (галочка) — но только если текст
    # при этом не активный (иначе пользовательское правило выше важнее).
    if step_marker(a, line) == "green":
        return "done"
    return ""


def deadline_margin_hours():
    """На сколько часов до срока заказа считать, что выполнять уже пора.

    0 — выполнять всегда, как раньше. По умолчанию 24 часа: заказ
    «Открыть на 16 дней» с дедлайном через двое суток выполнять рано —
    это сбивает счётчик дней у приложения.
    """
    try:
        value = int((h._load_panel_settings() or {}).get("active", {})
                    .get("deadline_margin_hours", 24))
    except Exception:
        value = 24
    return max(0, value)


def order_due(lines):
    """Срок заказа со страницы заказа: «не позднее 11:24 14.11.26».

    Возвращает datetime или None, если срока на странице нет.
    """
    text = h.norm(" ".join(str(line.get("text", "")) for line in lines))
    m = re.search(r"не позднее\s+(\d{1,2}):(\d{2})\s+(\d{1,2})\.(\d{1,2})\.(\d{2,4})", text)
    if not m:
        m = re.search(r"не позднее\s+(\d{1,2})\.(\d{1,2})\.(\d{2,4})\s+(\d{1,2}):(\d{2})", text)
        if m:
            day, month, year, hour, minute = (int(g) for g in m.groups())
        else:
            return None
    else:
        hour, minute, day, month, year = (int(g) for g in m.groups())
    if year < 100:
        year += 2000
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def step_due(lines, step_line):
    """Срок, относящийся к конкретному шагу («не позднее …»), если он указан рядом.

    На странице задания дедлайн следующего шага может быть виден ниже текущего;
    брать его для текущего шага нельзя (иначе готовый шаг ошибочно откладывается).
    Ищем «не позднее» в блоке текущего шага: от его строки до следующей строки шага.
    """
    if not lines or not step_line:
        return None
    y0 = int(step_line.get("y", 0))
    ordered = sorted(lines, key=lambda l: (int(l.get("y", 0)), int(l.get("x", 0))))
    next_steps = [int(l.get("y", 0)) for l in ordered
                  if int(l.get("y", 0)) > y0 + 30
                  and any(w in h.norm(l.get("text") or "") for w in STEP_WORDS)]
    y1 = min(next_steps) if next_steps else y0 + 900
    block = [l for l in ordered if y0 - 20 <= int(l.get("y", 0)) < y1]
    return order_due(block)


def pending_step(a, lines):
    """Строка текущего шага заказа.

    В новой сборке AdvertApp зелёной кнопки на странице заказа нет:
    у выполненных шагов зелёная галочка, у текущего — жёлтый кружок
    (teamdev) или зелёная подпись (старая сборка).
    None — шагов к выполнению нет, заказ закрыт.
    """
    for line in lines:
        text = h.norm(line.get("text") or "")
        if not any(w in text for w in STEP_WORDS):
            continue
        if step_state(a, line) == "pending":
            return line
    return None


def active_cards(a, lines):
    headers = sorted([l for l in lines if h.is_order_header(l) and 250 <= l["y"] < h.CONTENT_BOTTOM],
                     key=lambda l: l["y"])
    cards = []
    for i, hd in enumerate(headers):
        top = hd["y"]
        bottom = headers[i + 1]["y"] if i + 1 < len(headers) else h.CONTENT_BOTTOM
        block = [l for l in lines if top - 5 <= l["y"] < bottom]
        # Действием может быть не только «Открыть», но и «Установить»
        # (у заданий, где кнопка ведёт в Google Play).
        steps = [l for l in block
                 if any(w in h.norm(l["text"]) for w in STEP_WORDS)]
        # В новой сборке зелёная полоса — это бейдж с ценой, а не кнопка.
        # Текущий шаг отмечается жёлтым кружком (teamdev) или зелёной
        # подписью (старая сборка), выполненный — зелёной галочкой.
        pending = [l for l in steps if step_state(a, l) == "pending"]
        # На всякий случай берём первый по y (верхний текущий шаг).
        pending.sort(key=lambda l: l.get("y", 0))
        action = pending[0] if pending else None
        step_ys = [l["y"] for l in block if l["y"] > top + 10]
        # Иконка задания — слева от шагов, по центру блока шагов.
        icon_top = int(sum(step_ys) / len(step_ys)) - 100 if step_ys else top + 40
        # Цена задания — число вида 5.00 рядом с «руб.» справа.
        # В новой сборке перед ценой OCR читает кружок бейджа («О 3.60»),
        # поэтому ищем число внутри строки, а не точное совпадение.
        price = 0.0
        for line in block:
            if line["x"] < 700:
                continue
            m = re.search(r"(?<!\d)(\d{1,4})[.,](\d{2})(?!\d)", (line["text"] or "").strip())
            if m:
                price = float(m.group(1) + "." + m.group(2))
                break
        cards.append({
            "order": h.order_number(hd),
            "top": top,
            "title": hd["text"],
            "icon_top": icon_top,
            "actionable": bool(action),
            "green": action["text"] if action else None,
            # Координаты строки-действия: по ним открываем страницу заказа.
            "action_x": action["x"] + 60 if action else 0,
            "action_y": action["y"] if action else 0,
            "action_text": action["text"] if action else "",
            "price": price,
            # Весь текст карточки: по нему определяем нужный магазин.
            "text": h.norm(" ".join(l["text"] for l in block)),
        })
    return cards


class ActiveCards(list):
    """Список карточек «Активных» с флагом полноты прочитанного списка.

    Обычный `list` не допускает записи новых атрибутов: `cards.incomplete = …`
    молча падает с AttributeError, из-за чего все guard'ы cleanup (в tasks.py
    и strategy_engine.py) никогда не срабатывали. Возвращаем подкласс.
    """


# OCR одного кадра на ферме занимает 10–16 с. Старые таймауты 20–25 с
# хватало на 1–2 кадра, поэтому переходы между страницами почти всегда
# прерывались по таймауту, и скан помечался неполным (или историю не удавалось
# дочитать). Таймаут должен покрывать свайп + тап + подтверждающий кадр.
NAV_PAGE_TIMEOUT = 75


from core import shared as _shared

def page_buttons(a):
    return _shared.page_buttons(a)


def page_state(a):
    return _shared.page_state(_shared.page_buttons(a))


def extract_search_query(text):
    return _shared.extract_search_query(text)


def third_party_packages():
    return _shared.third_party_packages(h.adb)


def go_to_page(target, timeout=NAV_PAGE_TIMEOUT):
    return _shared.go_to_page(target, timeout, dev=h,
        screen_ok=is_active_screen,
        scroll_when=lambda top, target: not top)





def is_active_screen(kind, a):
    """Надёжное распознавание «Активных» по розовой шапке.

    Когда список прокручен, OCR читает обрезанный заголовок «Активные заказы»
    как страницу заказа (kind="detail" от read_screen). Старая проверка
    `kind in ("active", "list")` в go_to_page() из‑за этого переставала и
    нажимать кнопки страниц, и свайпить к верху: цикл 75 с крутился вхолостую,
    страница 2 не открывалась, и scan_active помечал скан неполным.
    Розовая шапка видна в любом положении списка (проверено на кадрах
    16:24–16:25), поэтому используем её как дополнительный признак.
    """
    return kind in ("active", "list") or (a is not None and h.active_header(a))




def scan_active(max_pages=None):
    """Все заказы в «Активных»: обе страницы списка, каждая до конца."""
    limit, empty_stop = active_strategy()
    if max_pages is not None:
        limit = max_pages
    a, path, lines = open_active()
    if a is None:
        return None
    # Список «Активных» мог открыться прокрученным в середину: тогда кнопки
    # страниц не видны, page_state() даёт (1,1), и вторая страница (где часто
    # лежат свежевзятые заказы вроде 1436991) никогда не сканируется. Перед
    # чтением числа страниц прокручиваем список к самому верху.
    h._scroll_top()
    kind, a, path, lines = h.read_screen("astop.png")
    if not is_active_screen(kind, a):
        kind, a, path, lines = h.read_screen("astop2.png")
    orders, cards = {}, ActiveCards()
    current, total = page_state(a)
    # Флаг неполного скана: если страница не открылась или кадр не распознан,
    # список «Активных» урезан. Такой скан НЕЛЬЗЯ использовать для cleanup —
    # иначе активный заказ ошибочно считается завершённым, и бот лезет в
    # Swift Backup / удаляет ещё нужное приложение, хотя шаг зелёный.
    incomplete = False
    for target in range(1, total + 1):
        if target != current and not go_to_page(target):
            print(f"  не удалось открыть страницу {target} «Активных»")
            incomplete = True
            break
        current = target
        stagnant = 0
        for page in range(limit):
            kind, a, path, lines = h.read_screen(f"as{target}_{page}.png")
            if not is_active_screen(kind, a):
                # Один кадр может попасться на переходе/модалке — перечитаем,
                # и только повторный провал помечает скан неполным.
                kind, a, path, lines = h.read_screen(f"asr{target}_{page}.png")
                if not is_active_screen(kind, a):
                    incomplete = True
                    break
            page_cards = active_cards(a, lines)
            new = 0
            for card in page_cards:
                old = orders.get(card["order"])
                if old is None:
                    orders[card["order"]] = card
                    cards.append(card)
                    new += 1
                    continue
                # Карточка могла попасть в кадр обрезанной у края: при повторе
                # дополняем её ценой и, если появилось действие, свежими координатами.
                old["price"] = max(old.get("price", 0.0), card.get("price", 0.0))
                if card["actionable"] and not old.get("actionable"):
                    old.update({k: card[k] for k in ("top", "icon_top", "actionable", "green")})
            stagnant = stagnant + 1 if new == 0 else 0
            if stagnant >= empty_stop:
                break
            h.swipe(1048, 1900, 1048, 850, 250)
            time.sleep(0.6)
    # Если кнопки страниц не распознались, page_state() даёт (1, 1) и вторая
    # страница просто не читается. При 20+ карточках на первой странице это
    # почти наверняка урезанный скан — помечаем его неполным.
    if total == 1 and len(cards) >= 20:
        incomplete = True
    cards.incomplete = incomplete
    return cards


# «История» распознаётся хуже «Активных»: классификатор часто называет её
# "detail". Поэтому смотрим на признаки текста и явно отсекаем главное меню.
HISTORY_OPEN_WAIT = 75
HISTORY_STAGNANT = 3
HISTORY_MAX_SCREENS = 30
# В истории бывает 200+ страниц — перебирать их все нельзя. Завершённые заказы
# появляются на первой странице, поэтому её чтение обязательно. Глубокие
# страницы читаем только если нужных заказов не нашли (не фатально: всё, что
# реально замечено в истории, можно удалять и при неполном обходе).
HISTORY_SCAN_PAGES = 3
# Центр плитки «История» в меню (1080×2412). Прежняя fallback-точка
# (228, 1286) попадала в пустой промежуток между плитками «История» и
# «Профиль» — тап терялся, и история не открывалась.
MENU_HISTORY_TILE = (228, 1060)


def history_numbers(lines):
    """Номера заказов из OCR-строк экрана «История»."""
    numbers = set()
    for row in h.merge_rows(lines):
        if not h.is_order_header(row):
            continue
        number = str(h.order_number(row)).strip()
        if re.fullmatch(r"\d{5,8}", number):
            numbers.add(number)
    return numbers


def is_history_screen(kind, a, lines, confirmed=False):
    """Распознаёт историю по содержимому, а не по `kind` от read_screen.

    Важно: h.looks_like_order_detail() считает историю страницей заказа,
    потому что первый заголовок «Заказ №…» лежит выше y=500. Из-за этого
    h.read_screen() возвращает kind="detail" для нормального списка истории
    (проверено на runs/fab1701/frames/hst_000117.png). Опираемся на текст.
    `confirmed=True` означает, что история уже опознана на первом кадре —
    тогда достаточно одного заголовка (последняя страница списка).
    """
    if kind == "menu" or h.menu_score(lines) >= 3:
        return False
    if kind == "active" or h.is_active_list(lines, a):
        return False
    if h.has_gplay_button(a):
        return False
    heads = [row for row in h.merge_rows(lines) if h.is_order_header(row)]
    if not heads:
        return False
    if confirmed or len(heads) >= 2:
        return True
    # Один заголовок может быть страницей одного заказа. Дополнительная
    # улика списка истории — кнопки пагинации (1, 2, 3, …).
    try:
        return page_state(a)[1] > 1
    except Exception:
        return False


def open_history():
    """Открывает вкладку «История». Возвращает (kind, a, lines) или None.

    None здесь принципиален: scan_history вернёт None, и cleanup не удалит
    ни одного приложения, если историю не удалось открыть.
    """
    for attempt in range(3):
        a, path, lines = ensure_menu()
        if a is None:
            continue
        # RapidOCR иногда читает плитку не целиком — пробуем полное слово,
        # затем префикс, затем заранее проверенную координату плитки.
        pos = h.find_word(lines, "история") or h.find_word(lines, "истори")
        if pos:
            h.tap(*pos)
        else:
            print("  плитка «История» не распознана OCR — тапаю по известной координате")
            h.tap(*MENU_HISTORY_TILE)
        end = time.monotonic() + HISTORY_OPEN_WAIT
        menu_frames = 0
        retap = False
        while time.monotonic() < end:
            kind, a, path, lines = h.read_screen("hst.png", check_focus=False)
            # Признаки текста надёжнее классификатора: история часто приходит
            # как kind="detail" из-за заголовка в upper half экрана.
            if is_history_screen(kind, a, lines):
                return kind, a, lines
            if kind == "menu" or h.menu_score(lines) >= 3:
                # Тап мог не дойти; даём экрану несколько кадров на переход,
                # но главное меню за историю никогда не принимаем.
                menu_frames += 1
                if menu_frames >= 4:
                    break
                time.sleep(0.4)
                continue
            if kind == "modal":
                h.handle_dialog("unprompted", a, path, "navigation", navigation=True)
                retap = True
                break
            time.sleep(0.3)
        if retap:
            time.sleep(0.5)
            continue
        print(f"  «История» не открылась за {HISTORY_OPEN_WAIT} с — повторяю")
    return None


def history_page_state(a, lines):
    """(текущая, всего, [(номер, x, y), ...]) по OCR-номерам пагинации.

    В histories 206 страниц, а tasks.page_buttons() видит только 2 светлых
    квадрата: тёмный квадрат текущей страницы отсекается порогом яркости.
    Поэтому берём номера из OCR (1, 2, 3, 4, 205, 206 на y≈300), а текущую
    страницу — по самому тёмному квадрату.

    Возвращает None, если номера не распознаны: в этом случае scan_history
    не должен удалять ничего.
    """
    if a is None or not lines:
        return None
    marks = {}
    for line in lines:
        text = (line.get("text") or "").strip()
        y = line.get("y", 0)
        # y≈300 — строка квадратов страниц; y<250 в кадре — статусбар,
        # его цифры («3» в углу) к пагинации отношения не имеют.
        if not (250 <= y <= 370) or not re.fullmatch(r"\d{1,4}", text):
            continue
        number = int(text)
        if 1 <= number <= 9999 and number not in marks:
            # OCR даёт левый край цифры; центр квадрата — примерно +30/+40.
            marks[number] = (number, line.get("x", 0) + 30, y + 40)
    if len(marks) < 2:
        return None
    items = sorted(marks.values())
    total = max(marks)
    means = []
    for _num, x, y in items:
        patch = a[max(0, y - 15):y + 15, max(0, x - 15):x + 15]
        means.append(float(patch.mean()) if patch.size else 255.0)
    ranked = sorted(range(len(items)), key=lambda i: means[i])
    first, second = ranked[0], ranked[1]
    # Текущая страница залита темнее остальных (разница ~25 единиц яркости).
    current = items[first][0] if means[second] - means[first] >= 8 else min(marks)
    return min(current, total), total, items


def history_goto_page(target, a, lines, timeout=NAV_PAGE_TIMEOUT):
    """Переходит на страницу истории по номеру из OCR.

    Возвращает (ok, a, lines, marks). `marks` — найденные OCR-номера страниц.
    """
    end = time.monotonic() + timeout
    last_tap = 0.0
    while time.monotonic() < end:
        state = history_page_state(a, lines)
        if state is None:
            # Список прокручен — поднимаемся к шапке, где видны номера.
            h.swipe(540, 450, 540, 2000, 600)
            time.sleep(0.5)
            kind, a, path, lines = h.read_screen("hpg.png", check_focus=False)
            if not is_history_screen(kind, a, lines, confirmed=True):
                return False, a, lines, None
            continue
        current, _total, marks = state
        if current == target:
            return True, a, lines, marks
        hit = next((m for m in marks if m[0] == target), None)
        if hit and time.monotonic() - last_tap > 2.5:
            h.tap(hit[1], hit[2])
            last_tap = time.monotonic()
            time.sleep(1.0)
        kind, a, path, lines = h.read_screen("hpg.png", check_focus=False)
        if not is_history_screen(kind, a, lines, confirmed=True):
            return False, a, lines, None
    return False, a, lines, None


def _read_history_page(page_no):
    """Читает текущую страницу «Истории» до конца прокруткой.

    Возвращает (номера, дочитана_ли). Второй элемент False означает, что
    скан оборвался (мешающий экран/лимит кадров) — вернутые номера при этом
    всё равно достоверны, просто список мог быть не полным.
    """
    numbers = set()
    stagnant = 0
    for screen in range(HISTORY_MAX_SCREENS):
        kind, a, path, lines = h.read_screen(
            f"hsc{page_no}_{screen}.png", check_focus=False)
        # Классификатор ошибочно называет историю "detail" — смотрим на текст.
        # Меню, «Активные» и модалку за историю не принимаем.
        if not is_history_screen(kind, a, lines, confirmed=(screen > 0)):
            return numbers, False
        found = history_numbers(lines)
        added = len(found - numbers)
        numbers |= found
        stagnant = stagnant + 1 if added == 0 else 0
        if stagnant >= HISTORY_STAGNANT:
            return numbers, True
        h.swipe(1048, 1900, 1048, 850, 250)
        time.sleep(0.6)
    return numbers, False


def scan_history(max_pages=None, wanted=None):
    """Номера заказов в «Истории» AdvertApp.

    Свежие завершённые заказы появляются на первой странице, поэтому она
    читается обязательно и до конца. Дальнейшие страницы — только если
    нужных заказов там не оказалось; неудачный переход НЕ выбрасывает уже
    прочитанные номера: удаление всё равно требует положительного совпадения
    номера с историей (см. confirm_missing).

    Возвращает set замеченных номеров; None — если историю не удалось
    открыть либо не прочитан ни один заказ (тогда удалять нельзя ничего).
    """
    opened = open_history()
    if opened is None:
        return None
    kind, a, lines = opened
    try:
        need = set(wanted) if wanted else None
        limit = HISTORY_SCAN_PAGES if max_pages is None else max(1, int(max_pages))
        orders = set()
        pages_done = 0
        for page_no in range(1, limit + 1):
            found, complete = _read_history_page(page_no)
            orders |= found
            if not complete:
                print(f"  страница {page_no} «Истории» дочитана не полностью — "
                      f"использую только замеченные заказы")
                break
            pages_done += 1
            if need and not (need - orders):
                break
            if page_no >= limit:
                break
            ok, a, lines, _marks = history_goto_page(page_no + 1, a, lines)
            if not ok:
                print(f"  не удалось открыть страницу {page_no + 1} «Истории» — "
                      f"ограничиваюсь прочитанными заказами")
                break
        if not orders:
            return None
        print(f"  «История»: прочитано {len(orders)} заказов с {pages_done} стр.")
        return orders
    except Exception as exc:
        print(f"  «История» прочитана с ошибкой ({exc}) — ничего не удаляю")
        return None


def confirm_in_active(order, budget_s=20):
    """Постфактум-подтверждение неопределённого исхода (предложение Qwen 4 MAX).

    unknown — это не исход, а незавершённый вопрос: заказ либо в «Активных»
    (значит взят), либо нет. Вызывается, когда NAV_TAB ACTIVE уже выполнен.
    Возвращает True, если номер найден в списке «Активных». Неполный скан
    (страница не открылась) → False с пометкой «не подтверждено», чтобы не
    превратить неизвестность в ложное unavailable. Хелпер НЕ включается в
    горячий путь: вызов под флагом strategy.confirm_unknown.enabled (по
    умолчанию выключен).
    """
    end = time.monotonic() + budget_s
    page = 1
    incomplete = False
    while time.monotonic() < end:
        kind, a, path, lines = h.read_screen("cf.png")
        if h.is_active_list(lines, a) or is_active_screen(kind, a):
            cards = active_cards(a, lines)
            if any(h.order_matches(order, c["order"]) for c in cards):
                return True
            page += 1
            if not go_to_page(page, timeout=6):
                incomplete = True
                break
        time.sleep(0.5)
    return False


def locate(order, max_pages=None):
    """Ищет заказ заново и возвращает карточку с актуальными координатами.

    Вызывать только после open_active(): список открывается с начала.
    Просматривает обе страницы «Активных».
    """
    limit, _empty_stop = active_strategy()
    if max_pages is not None:
        limit = max_pages
    kind, a, path, lines = h.read_screen("loc0.png")
    if kind not in ("active", "list"):
        return None
    current, total = page_state(a)
    for target in range(1, total + 1):
        if target != current and not go_to_page(target):
            break
        current = target
        for page in range(limit):
            kind, a, path, lines = h.read_screen(f"loc{target}_{page}.png")
            if kind not in ("active", "list"):
                return None
            for card in active_cards(a, lines):
                if h.order_matches(order, card["order"]):
                    # Карточка у нижнего края перекрыта меню — поднимаем её,
                    # иначе тап попадает в нижнюю навигацию, а не в заказ.
                    for lift in range(3):
                        if card["top"] < 1900:
                            break
                        h.swipe(540, 1900, 540, 1400, 250)
                        time.sleep(0.5)
                        kind, a, path, lines = h.read_screen(
                            f"loc{target}_{page}_lift{lift}.png")
                        if kind not in ("active", "list"):
                            return None
                        found = next((c for c in active_cards(a, lines)
                                      if h.order_matches(order, c["order"])), None)
                        if found is None:
                            return None
                        card = found
                    card["a"] = a
                    return card
            h.swipe(1048, 1900, 1048, 850, 250)
            time.sleep(0.5)
    return None


def wait_text(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not h.stop_requested():
        time.sleep(1.0)


PERMISSION_WORDS = ("разрешить", "пока использую", "только в этот раз",
                    "запретить", "не разрешать")


def allow_permission_dialog():
    """Закрывает системный запрос разрешений, чтобы он не съедал удержание."""
    try:
        kind, a, path, lines = h.read_screen("perm.png", check_focus=False)
    except Exception:
        lines = []
    for word in PERMISSION_WORDS:
        pos = h.find_word(lines, word)
        if pos:
            print(f"    диалог разрешений: жму «{word}»")
            if isinstance(pos, dict):
                h.tap(*line_center(pos))
            else:
                h.tap(*pos)
            return True
    print("    диалог разрешений: кнопки не распознаны, жму «Разрешить» по шаблону")
    h.tap(540, 2024)
    return True


def hold_package(pkg, seconds, component=None):
    """Держит целевое приложение на переднем плане всё заданное время."""
    h.LAST_HELD_PKG = pkg
    end = time.monotonic() + seconds
    lost = 0
    while time.monotonic() < end and not h.stop_requested():
        time.sleep(2.0)
        cur = h.focus()
        if pkg in cur:
            lost = 0
            continue
        if "permissioncontroller" in cur or "GrantPermissions" in cur:
            allow_permission_dialog()
            lost = 0
            continue
        lost += 1
        if lost >= 3:
            print(f"    {pkg} потерял фокус — запускаю снова")
            if component:
                h.adb("shell", f"am start -n {component}")
            else:
                h.adb("shell", f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
            lost = 0


TMP_DIR = os.path.join(os.environ.get("TEMP", r"C:\Users\RZE\AppData\Local\Temp"), "opencode")


def configure_device(serial, slot=None):
    """Персональные файлы задания для устройства + общий кэш иконок.

    order_apps.json и known_apps.json раньше были общими. При нескольких
    телефонах одновременная запись затирала бы чужие записи, а заказы
    разных аккаунтов могут не совпадать. Поэтому у каждого устройства
    свой файл, а при первом запуске он копируется из общего — накопленные
    знания не теряются.
    """
    h.configure_device(serial, slot)
    global MAP_PATH, KNOWN_PATH, SEEN_PATH, TMP_DIR, NEW_TAKEN_PATH
    run_dir = h.RUN_DIR
    MAP_PATH = os.path.join(run_dir, "order_apps.json")
    KNOWN_PATH = os.path.join(run_dir, "known_apps.json")
    SEEN_PATH = os.path.join(run_dir, "seen_orders.json")
    NEW_TAKEN_PATH = os.path.join(run_dir, "new_taken.json")
    TMP_DIR = os.path.join(run_dir, "tmp")
    os.makedirs(TMP_DIR, exist_ok=True)
    for name in ("order_apps.json", "known_apps.json"):
        dst = os.path.join(run_dir, name)
        src = os.path.join(h.BASE_DIR, name)
        if not os.path.exists(dst) and os.path.exists(src):
            try:
                shutil.copy2(src, dst)
            except OSError as error:
                print(f"  не удалось скопировать {name}: {error!r}")
    return run_dir


def installed(pkg):
    out = h.adb("shell", f"pm path {pkg} 2>/dev/null").stdout.decode("utf-8", "ignore")
    return "package:" in out


def package_from_intent():
    """Пакет из ссылки, которую открыла кнопка задания (market:// или play.google.com)."""
    out = h.adb("shell", "dumpsys activity activities").stdout.decode("utf-8", "ignore")
    for pattern in (r"market://details\?id=([A-Za-z0-9_.]+)",
                    r"play\.google\.com/store/apps/details\?id=([A-Za-z0-9_.]+)",
                    r"[?&]id=([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)"):
        m = re.search(pattern, out)
        if m:
            return m.group(1)
    return None


def install_from_backup(pkg):
    """Ставит приложение из Swift Backup: base .app + сплиты из .splits."""
    out = h.adb("shell", f"ls {SWIFT_ROOT}/*/backups/apps/local/{pkg}/*/{pkg}.app 2>/dev/null").stdout.decode("utf-8", "ignore")
    sources = [x.strip() for x in out.splitlines() if x.strip().endswith(".app")]
    if not sources:
        return False
    src = sorted(sources)[-1]
    os.makedirs(TMP_DIR, exist_ok=True)
    local = os.path.join(TMP_DIR, pkg + ".apk")
    print(f"    {pkg}: беру из бэкапа {src}")
    h.adb("pull", src, local, timeout=300)
    apks = [local]
    out = h.adb("shell", f"ls {SWIFT_ROOT}/*/backups/apps/local/{pkg}/*/{pkg}.splits 2>/dev/null").stdout.decode("utf-8", "ignore")
    splits = [x.strip() for x in out.splitlines() if x.strip().endswith(".splits")]
    if splits:
        sp = sorted(splits)[-1]
        local_sp = os.path.join(TMP_DIR, pkg + ".splits")
        h.adb("pull", sp, local_sp, timeout=300)
        outdir = os.path.join(TMP_DIR, pkg + "_splits")
        os.makedirs(outdir, exist_ok=True)
        subprocess.run([sys.executable, os.path.join(h.BASE_DIR, "tools", "sba_extract.py"),
                        local_sp, outdir], check=False)
        apks += [os.path.join(outdir, n) for n in os.listdir(outdir) if n.endswith(".apk")]
    result = h.adb("install-multiple", "-r", "-d", *apks, timeout=600)
    text = (result.stdout + result.stderr).decode("utf-8", "ignore").strip()
    print("   ", text.splitlines()[-1] if text else "install: нет ответа")
    return "Success" in text


ICON_CACHE = os.path.join(h.BASE_DIR, "icon_cache")
_icon_index = None


def icon_vector(image):
    """Нормализованный вектор картинки 16×16 для сравнения иконок."""
    image = image.convert("RGB")
    gray = image.convert("L")
    if np.asarray(gray).std() < 6:
        return None
    # Обрезаем однотонные поля: у карточки задания иконка с белой рамкой,
    # в кэше Swift Backup — без неё.
    array = np.asarray(image).astype("int16")
    corner = array[0, 0]
    mask = (np.abs(array - corner).max(axis=2) > 25)
    ys, xs = np.where(mask)
    if len(xs):
        image = image.crop((int(xs.min()), int(ys.min()),
                            int(xs.max()) + 1, int(ys.max()) + 1))
    small = image.convert("L").resize((16, 16), Image.LANCZOS)
    data = np.asarray(small).astype("float32")
    data = (data - data.mean()) / (data.std() + 1e-6)
    return data.ravel()


def icon_index():
    """Индекс иконок всех приложений из кэша Swift Backup (458 штук)."""
    global _icon_index
    if _icon_index is None:
        _icon_index = {}
        if os.path.isdir(ICON_CACHE):
            for name in os.listdir(ICON_CACHE):
                if not name.endswith(".png"):
                    continue
                try:
                    with Image.open(os.path.join(ICON_CACHE, name)) as image:
                        vector = icon_vector(image)
                except OSError:
                    continue
                if vector is not None:
                    _icon_index[name[:-4]] = vector
        print(f"  иконок в кэше: {len(_icon_index)}")
    return _icon_index


def match_icon_cache(a, icon_top):
    """Ищет приложение по иконке среди всех бэкапов Swift Backup."""
    if a is None or not icon_top:
        return None
    image = Image.fromarray(a.astype("uint8")).crop((40, icon_top, 240, icon_top + 200))
    vector = icon_vector(image)
    if vector is None:
        return None
    scored = sorted((float(((vector - v) ** 2).mean()), pkg)
                    for pkg, v in icon_index().items())
    if not scored:
        return None
    best, pkg = scored[0]
    second = scored[1][0] if len(scored) > 1 else 99.0
    # Нужен явный отрыв от второго места, иначе иконка общая/непохожая.
    if best < 1.0 and best < 0.75 * second:
        return pkg
    return None


def install_from_market(pkg, store="google"):
    """Ставит приложение из магазина, который требует задание.

    Порядок: нужный магазин → остальные разрешённые → Google Play как
    запасной вариант. На телефонах RuStore и GetApps могут быть не
    установлены, поэтому для них проверяется наличие пакета.
    """
    chain = [store] + [m for m in allowed_markets() if m not in (store, "google")]
    if "google" not in chain:
        chain.append("google")
    for candidate in chain:
        if install_from_store(pkg, candidate):
            print(f"    {pkg}: установлено из {STORE_LABELS.get(candidate, candidate)}")
            return True
    return False


STORE_LABELS = {
    "google": "Google Play",
    "rustore": "RuStore",
    "huawei": "Huawei AppGallery",
    "xiaomi": "Xiaomi GetApps",
}

# Пакеты магазинов: по ним открывается карточка приложения.
STORE_PACKAGES = {
    "google": "com.android.vending",
    "rustore": "com.ru.store.app",
    "huawei": "com.huawei.appmarket",
    "xiaomi": "com.xiaomi.market",
}

# Слова в тексте задания, по которым угадывается нужный магазин.
STORE_WORDS = {
    "rustore": ("rustore", "русто", "рустор"),
    "huawei": ("appgallery", "huawei", "хуав", "галерея приложений"),
    "xiaomi": ("getapps", "get apps", "mi store", "mistore", "xiaomi", "ми Xiaomi"),
    "google": ("google play", "play market", "плей маркет", "google play"),
}


def detect_store(text):
    """Какой магазин требует задание: google / rustore / huawei / xiaomi."""
    text = h.norm(text or "")
    for store in ("rustore", "huawei", "xiaomi", "google"):
        if any(word in text for word in STORE_WORDS[store]):
            return store
    return "google"


def allowed_markets():
    """Магазины, разрешённые в настройках панели."""
    settings = _load_panel_settings() or {}
    markets = settings.get("markets") if isinstance(settings, dict) else None
    if not isinstance(markets, list) or not markets:
        markets = ["google"]
    return [m for m in markets if m in STORE_PACKAGES]


def store_installed(store):
    """Установлен ли магазин на телефоне."""
    package = STORE_PACKAGES.get(store)
    if not package:
        return False
    out = h.adb("shell", "pm list packages").stdout.decode("utf-8", "ignore")
    return f"package:{package}" in out


def store_intent(pkg, store):
    """Команда запуска карточки приложения в нужном магазине."""
    if store == "rustore":
        # RuStore приложения может не быть: открываем веб-каталог.
        return ["shell", "am", "start", "-a", "android.intent.action.VIEW",
                "-d", f"https://www.rustore.ru/catalog/app/{pkg}/"]
    if store == "huawei":
        return ["shell", "am", "start", "-a", "android.intent.action.VIEW",
                "-d", f"appmarket://details?id={pkg}"]
    if store == "xiaomi":
        return ["shell", "am", "start", "-a", "android.intent.action.VIEW",
                "-d", f"market://details?id={pkg}", "-p", "com.xiaomi.market"]
    return ["shell", "am", "start", "-a", "android.intent.action.VIEW",
            "-d", f"market://details?id={pkg}"]


def install_from_store(pkg, store):
    """Открывает карточку приложения в магазине и нажимает «Установить»."""
    if store in ("google", "huawei", "xiaomi") and not store_installed(store):
        print(f"    {store}: магазин не установлен — пропускаю")
        return False
    h.adb(*store_intent(pkg, store))
    time.sleep(6)
    for _ in range(30):
        kind, a, path, lines = h.read_screen("mk.png", check_focus=False)
        line = h.find_word(lines, "установить")
        if line:
            # В магазине в фокусе не AdvertApp — обычный tap там бросает исключение.
            h.tap_any(*line_center(line))
            break
        time.sleep(2)
    for _ in range(90):
        if installed(pkg):
            return True
        time.sleep(2)
    return False






def install_by_search(order, text, a=None, lines=None, bar=None):
    """Ставит приложение по инструкции в магазине, который требует задание."""
    query = extract_search_query(text)
    if not query:
        print(f"  {order}: поисковый запрос не распознан")
        return None
    # Распознавание магазина и выбор результата делается в hunter2: там же
    # работает и модалка «Новых». Один путь — меньше шансов снова поставить
    # приложение из Google Play для инструкции Huawei/Xiaomi.
    pkg = h.install_by_query(order, text, a, lines, bar)
    if pkg:
        learn_new_db(order, pkg, {"title": query})
    return pkg


def action_button_for_step(a, lines, target):
    """Точка тапа для текущего шага на странице заказа.

    В новой сборке тап по строке шага в левом списке часто не запускает
    действие: нужно нажимать кнопку действия (зелёную/кнопку «Открыть»)
    в основной области. Приоритет: широкая зелёная кнопка, затем отдельная
    кнопка действия в нижней зоне. Возвращает None, если надёжной кнопки нет
    (шаг ещё не готов/нет кнопки) — чтобы не тапать по тексту описания.
    """
    bar = h.find_green_bar(a, 500, 2260, 220)
    if bar:
        return (bar[1], bar[2])
    action_words = ("открыть", "установить", "загрузить", "скачать", "найти")
    # Фолбэк: только нижняя зона действия (не заголовки/описания шагов в середине).
    for line in sorted(lines, key=lambda l: l.get("y", 0), reverse=True):
        y = int(line.get("y", 0))
        if y < 1500:
            continue
        raw = (line.get("text") or "")
        text = h.norm(raw).strip("«»\"' ")
        # Пропускаем строки-описания вида «Открыть на 2 день:» / с пояснениями.
        if ":" in raw or "день:" in h.norm(raw):
            continue
        if text in action_words:
            words = line.get("words") or []
            if words:
                x = sum(w["x"] + w["w"] / 2 for w in words) / len(words)
                cy = sum(w["y"] + w["h"] / 2 for w in words) / len(words)
                return (int(x), int(cy))
            return (540, y + 20)
        words = line.get("words") or []
        if len(words) <= 3:
            for w in words:
                tok = h.norm(w.get("t", "")).strip("«»\"'.,:;! ")
                if tok in action_words and w.get("x", 0) >= 250 and y >= 1600:
                    return (int(w["x"] + w["w"] / 2), int(w["y"] + w["h"] / 2))
    # На странице заказа без видимой кнопки действия не угадываем (иначе тап
    # попадает в описание шага и приложение не запускается).
    if target is not None:
        return None
    return None


def order_confirmed(order):
    """Заказ реально закрыт AdvertApp: зелёного шага больше нет.

    Одно удержание приложения (шаг 5) ещё НЕ завершает заказ: по алгоритму
    AdvertApp нужно вернуться и выполнить следующий зелёный шаг. Поэтому
    считаем выполненным только исчезновение из «Активных» или явные
    зелёные галочки/подпись «выполнено» на странице заказа.
    """
    for attempt in range(2):
        if open_active()[0] is None:
            return False
        card = locate(order)
        if card is None:
            if attempt == 1:
                return True
            # Список мог просто перезагружаться — перепроверяем перед выводом.
            time.sleep(2)
            continue
        if card.get("actionable"):
            return False
        # Карточка осталась, но зелёного действия нет: смотрим страницу заказа.
        h.tap(540, card["top"] + 60)
        time.sleep(1.5)
        kind, a, path, lines = h.read_screen("ord_confirm.png")
        if a is None:
            return False
        text = h.norm(" ".join(line.get("text", "") for line in lines))
        if any(word in text for word in ("выполнено", "завершено", "начислено", "оплачено")):
            return True
        steps = [line for line in lines
                 if any(word in h.norm(line.get("text", "")) for word in STEP_WORDS)]
        if steps and all(step_state(a, line) == "done" for line in steps):
            return True
        return False
    return False


def do_task(card, dry_run=False, only_due=False, _retry=True):
    """Открывает карточку, выполняет зелёное действие и держит приложение."""
    order = card["order"]
    action = h.norm(card.get("green") or "")
    installing = any(w in action for w in ("установить", "загрузить", "скачать", "найти"))
    h.LAST_UNINSTALLED_PKG = None
    # Страницу заказа в новой сборке открывает тап по строке текущего шага.
    # Открываем заказ по ЗАГОЛОВКУ: координаты строки шага из scan_active часто
    # устаревают (список переsortirovan), и тап уходит в другой заказ.
    h.tap(540, card["top"] + 60)
    # AdvertApp открывает страницу заказа с задержкой 7–12 с. Раньше здесь
    # было time.sleep(1.4), и первый read_screen видел ещё список; затем
    # locate() на уже открытой деталке возвращал None, что ошибочно
    # трактовалось как «страница не открылась» и выкидывалось start_app().
    # Опрошиваем экран до ~14 с, пока он не станет страницей заказа.
    time.sleep(1.4)
    _kind0, _a0, _path0, _lines0 = h.read_screen("t0wait.png")
    _open_deadline = time.monotonic() + 14
    while (_kind0 in ("list", "active") or h.is_active_list(_lines0, _a0)) \
            and time.monotonic() < _open_deadline:
        time.sleep(1.2)
        _kind0, _a0, _path0, _lines0 = h.read_screen("t0wait.png")
    button = None
    step_text = ""
    target = None
    # По алгоритму AdvertApp держать приложение нужно 65–80 с. Значение
    # 120 с было запасным и только растягивало проход; если на странице
    # явно указан свой срок, он перекрывает это значение ниже.
    seconds = 75
    # Сначала ищем текущий активный/доступный шаг и кнопку действия на экране
    # заказа. Сроки на странице могут относиться к СЛЕДУЮЩИМ шагам (ниже), поэтому
    # не принимаем решение «рано» по произвольному «не позднее» со всего экрана.
    for step in range(5):
        kind, a, path, lines = h.read_screen(f"t{step}.png")
        if kind == "modal":
            modal = h.modal_lines(a, path)
            modal_text = " | ".join(line["text"] for line in modal)
            if ("поисковый запрос" in h.norm(modal_text)
                    or "открыть google play" in h.norm(modal_text)):
                bar = h.find_green_bar(a, 900, 1800, 300)
                if bar:
                    h.tap(bar[1], bar[2])
                    time.sleep(4)
                # a2/modal2 здесь не определены (они из другой функции) —
                # передаём уже прочитанные a/lines этой модалки, иначе NameError.
                pkg = install_by_search(order, modal_text, a, modal)
                h.start_app()
                time.sleep(2)
                return (pkg, None) if pkg else None
            status = h.handle_dialog(order, a, path, "open")
            if status == "uninstalled":
                # Приложение удалено и подтверждено — продолжаем карточку:
                # следующая кнопка обычно снова ставит это приложение.
                time.sleep(1.0)
                continue
            if status == "accepted" and h.LAST_HELD_PKG:
                print(f"  {order}: диалог открытия -> {h.LAST_HELD_PKG}")
                return h.LAST_HELD_PKG, None
            return None
        # Работаем ТОЛЬКО на странице заказа. В списке «Активных» подпись
        # готового шага тоже зелёная: приняв её за кнопку, бот тапает по тексту
        # списка, запускает приложение из базы и ложно пишет «выполнено»,
        # хотя заказ остаётся активным (было у 1428890 21:47 и 22:35).
        if kind in ("list", "active") or h.is_active_list(lines, a):
            if step == 0:
                # Один повтор по свежим координатам: список мог перестроиться
                # между locate() и тапом по заголовку.
                fresh = locate(order)
                if fresh is not None:
                    h.tap(540, fresh["top"] + 60)
                    # Снова ждём открытия с опросом (см. комментарий у первого
                    # тапа), а не фиксированные 1.4 с.
                    time.sleep(1.4)
                    _k2, _a2, _p2, _l2 = h.read_screen("twait.png")
                    _dl2 = time.monotonic() + 14
                    while (_k2 in ("list", "active") or h.is_active_list(_l2, _a2)) \
                            and time.monotonic() < _dl2:
                        time.sleep(1.2)
                        _k2, _a2, _p2, _l2 = h.read_screen("twait.png")
                    continue
                # locate() вернул None, потому что мы УЖЕ на деталке (он ищет
                # только в списке). Перечитаем экран: если это страница заказа —
                # продолжаем обрабатывать её, а не откладываем.
                kind2, a2, path2, lines2 = h.read_screen("trecheck.png")
                if kind2 not in ("list", "active") and not h.is_active_list(lines2, a2):
                    kind, a, path, lines = kind2, a2, path2, lines2
                    continue
            print(f"  {order}: страница заказа не открылась — откладываю")
            h.start_app()
            return None
        headers = [r for r in h.merge_rows(lines) if h.is_order_header(r)]
        if headers:
            opened = h.order_number(headers[0])
            if not h.order_matches(order, opened):
                print(f"  {order}: открыт другой заказ {opened} — откладываю")
                h.start_app()
                return None
        # Текущий шаг отмечен активным маркером/выделением; действие может быть
        # зелёной кнопкой внизу страницы, а не тапом по строке шага слева.
        target = pending_step(a, lines)
        if target is not None:
            near = " ".join(l["text"] for l in lines if abs(l["y"] - target["y"]) < 260)
            m = re.search(r"(\d+)\s*минут", h.norm(near))
            if not m:
                m = re.search(r"(\d+)\s*секунд", h.norm(near))
            if m:
                seconds = int(m.group(1)) * 60 if "минут" in m.group(0) else int(m.group(1))
            step_text = h.norm(target.get("text") or "")
            button = action_button_for_step(a, lines, target)
            scrolls = 0
            while button is None and scrolls < 3:
                # У готового шага кнопка «Открыть» может быть ниже видимой
                # области — прокручиваем страницу заказа, а не откладываем.
                h.swipe(540, 1900, 540, 900, 300)
                time.sleep(0.8)
                kind, a, path, lines = h.read_screen(f"t{step}_s{scrolls}.png")
                # НЕ прерываем по kind: Details страница часто классифицируется
                # как active/list из-за розовой шапки. Главное — найти зелёную
                # кнопку по пикселям, а не доверять классификатору экрана.
                # Сначала ищем кнопку напрямую: после прокрутки зелёная
                # кнопка «Открыть» уже видна, а подпись шага (target) может
                # не распознаться. Раньше `if target is None: break` exiting
                # цикл до нахождения кнопки — заказ откладывался, хотя
                # кнопка была на экране (было у 719933/1409790).
                button = h.find_green_bar(a, 500, 2260, 220)
                new_target = pending_step(a, lines)
                if new_target is not None:
                    target = new_target
                    near = " ".join(l["text"] for l in lines if abs(l["y"] - target["y"]) < 260)
                    m = re.search(r"(\d+)\s*минут", h.norm(near))
                    if not m:
                        m = re.search(r"(\d+)\s*секунд", h.norm(near))
                    if m:
                        seconds = int(m.group(1)) * 60 if "минут" in m.group(0) else int(m.group(1))
                    step_text = h.norm(target.get("text") or "")
                if button is None:
                    button = action_button_for_step(a, lines, target)
                scrolls += 1
            if button is None:
                print(f"  {order}: шаг «{step_text}» найден, но кнопка действия не видна — откладываю")
                h.start_app()
                return None
            print(f"  {order}: «{step_text}» -> кнопка {button}, держим {seconds} с")
            break
        # Кнопка бывает и в середине карточки (y≈840), и у самого низа (y≈2157).
        bar = h.find_green_bar(a, 600, 2260, 400)
        if bar:
            # find_green_bar возвращает (ширина, x, y), а не (x, y).
            near = " ".join(l["text"] for l in lines if abs(l["y"] - bar[2]) < 260)
            m = re.search(r"(\d+)\s*минут", h.norm(near))
            if m:
                seconds = int(m.group(1)) * 60
            else:
                m = re.search(r"(\d+)\s*секунд", h.norm(near))
                seconds = int(m.group(1)) if m else 75
            button = (bar[1], bar[2])
            break
        h.swipe(540, 1900, 540, 900, 300)
        time.sleep(0.6)
    if button is None:
        print(f"  {order}: кнопка действия не найдена")
        return None
    # Проверка срока ТОЛЬКО для текущего шага. Если шаг активен/выделен и кнопка
    # действия есть — выполнять его нужно сразу, даже если ниже на странице указан
    # дедлайн следующего шага. Отложить можно лишь по сроку самого текущего шага.
    margin = deadline_margin_hours()
    # Подпись зелёная (или тёмно-активная на Samsung) — шаг уже наступил,
    # даже если «не позднее» в блоке шага ещё в будущем. Откладывать по
    # future-due такой шаг нельзя: именно так терялись «Открыть на N день».
    ready_step = target is not None and step_state(a, target) == "pending"
    # Кнопкаactions найдена (зелёная/«Открыть») — значит шаг готов, даже если
    # target не распознан. Срок будущего дня не должен блокировать готовый шаг.
    if button is not None:
        ready_step = True
    if (margin > 0 or only_due) and not ready_step:
        due = step_due(lines, target) if target is not None else order_due(lines)
        if due is not None:
            left = (due - datetime.now()).total_seconds() / 3600.0
            if margin > 0 and left > margin:
                print(f"  {order}: до срока шага ещё {left:.0f} ч "
                      f"({due:%d.%m %H:%M}) — рано, откладываю")
                h.start_app()
                return None
            if only_due and left <= 0:
                # По правилу пользователя просроченный заказ с зелёной подписью
                # выполняем немедленно (особенно «Найти/Установить/Открыть»),
                # а не пропускаем. Срок прошёл — тем более пора делать.
                print(f"  {order}: срок шага прошёл ({due:%d.%m %H:%M}) — выполняю сразу")
        # Если у активного шага нет собственного «не позднее», не откладываем его
        # из-за отсутствия срока (готовый шаг с кнопкой нужно выполнить).
    print(f"  {order}: «{card['green']}» -> кнопка {button}, держим {seconds} с")
    near_text = h.norm(" ".join(l["text"] for l in lines if abs(l["y"] - button[1]) < 140))
    rustore = "rustore" in near_text or "русто" in near_text
    # Какой магазин требует задание: по тексту карточки и подписи кнопки.
    store = detect_store(near_text + " " + str(card.get("text") or ""))
    if store != "google":
        print(f"  {order}: задание требует {STORE_LABELS[store]}")
    if dry_run:
        return None
    # find_green_bar возвращает (ширина, x, y) — три элемента. Ранее кнопка
    # приходила из action_button_for_step как (x, y); теперь может прийти
    # напрямую из find_green_bar, поэтому берём только (x, y).
    if len(button) >= 3:
        h.tap(button[1], button[2])
    else:
        h.tap(*button)

    if installing:
        # После «Загрузить из Google Play» может открыться инструкция с
        # поисковым запросом — тогда ставим приложение через поиск Play.
        probe_end = time.monotonic() + 6
        while time.monotonic() < probe_end:
            kind2, a2, path2, _lines2 = h.read_screen("im.png")
            if kind2 != "modal":
                time.sleep(0.5)
                continue
            modal2 = h.modal_lines(a2, path2)
            text2 = " | ".join(line["text"] for line in modal2)
            if "поисковый запрос" not in h.norm(text2):
                break
            bar2 = h.find_green_bar(a2, 900, 1800, 300)
            if bar2:
                h.tap(bar2[1], bar2[2])
                time.sleep(4)
            found = install_by_search(order, text2, a2, modal2)
            h.start_app()
            time.sleep(2)
            if not found:
                return None
            ensure_menu()
            if not _retry or open_active()[0] is None:
                return found, None
            fresh = locate(order)
            if fresh is None:
                return found, None
            return do_task(fresh, dry_run=False, _retry=False, only_due=only_due) or (found, None)
        # Своя база — первой: номер заказа, иконка из кэша Swift Backup,
        # затем хэш иконки из known_apps. Google Play — только после неё.
        pkg = h.LAST_UNINSTALLED_PKG or new_db_package(order) or known_package(order)
        if pkg:
            print(f"  {order}: пакет из памяти заданий — {pkg}")
        if not pkg:
            pkg = match_icon_cache(card.get("a"), card.get("icon_top"))
            if pkg:
                print(f"  {order}: приложение по иконке из кэша бэкапов — {pkg}")
        if not pkg:
            pkg = new_db_icon(card.get("a"), card.get("icon_top"))
            if pkg:
                print(f"  {order}: приложение по иконке из новой базы — {pkg}")
        if not pkg:
            pkg = known_by_icon(card.get("icon"))
            if pkg:
                print(f"  {order}: пакет по иконке из базы — {pkg}")
        if not pkg:
            # В своей базе не нашлось — ждём market://intent из магазина.
            end = time.monotonic() + (3 if rustore else 20)
            while pkg is None and time.monotonic() < end:
                pkg = package_from_intent()
                if pkg:
                    break
                time.sleep(1)
        if not pkg:
            print(f"  {order}: пакет не определился — иконка общая или без иконки, нужно опознать приложение")
            h.start_app()
            return None
        print(f"  {order}: приложение {pkg}")
        learn_new_db(order, pkg, card)
        if installed(pkg):
            print(f"    {pkg}: уже установлено")
        elif has_backup(pkg) and install_from_backup(pkg):
            print(f"    {pkg}: установлено из бэкапа")
        elif install_from_market(pkg, store):
            pass
        else:
            print(f"    {pkg}: установить не удалось")
            h.start_app()
            return None
        h.start_app()
        time.sleep(2.0)
        ensure_menu()
        if not _retry or open_active()[0] is None:
            return pkg, None
        fresh = locate(order)
        if fresh is None:
            print(f"  {order}: после установки карточка не найдена")
            return pkg, None
        return do_task(fresh, dry_run=False, _retry=False, only_due=only_due) or (pkg, None)

    pkg = None
    component = None
    end = time.monotonic() + 12
    while time.monotonic() < end:
        m = re.search(r"u0 ([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)", h.focus())
        if m and m.group(1) not in h.APP_PACKAGES and m.group(1) not in NOT_TARGET:
            pkg = m.group(1)
            component = f"{pkg}/{m.group(2)}"
            break
        time.sleep(0.3)
    if pkg is None:
        # В новой сборке AdvertApp тап по шагу приложение не запускает —
        # запускаем его сами: пакет ищем в базе заказов, затем по иконке.
        pkg = new_db_package(order) or known_package(order)
        if pkg:
            print(f"  {order}: пакет из базы заданий — {pkg}")
        if not pkg:
            pkg = match_icon_cache(card.get("a"), card.get("icon_top"))
            if pkg:
                print(f"  {order}: приложение по иконке из кэша бэкапов — {pkg}")
        if not pkg:
            pkg = new_db_icon(card.get("a"), card.get("icon_top"))
            if pkg:
                print(f"  {order}: приложение по иконке из новой базы — {pkg}")
        if not pkg:
            pkg = known_by_icon(card.get("icon"))
            if pkg:
                print(f"  {order}: пакет по иконке из базы — {pkg}")
        if pkg:
            h.adb("shell", "am start -n " + h.main_activity(pkg))
            time.sleep(4.0)
            m = re.search(r"u0 ([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)", h.focus())
            if m and m.group(1) not in h.APP_PACKAGES and m.group(1) not in NOT_TARGET:
                component = f"{m.group(1)}/{m.group(2)}"
    if pkg is None:
        print(f"  {order}: приложение не запустилось")
        h.start_app()
        return None
    print(f"  {order}: запущен {pkg}, ждём {seconds} с")
    # seconds=75 по умолчанию → max(hold_min(), 80) = 80 с: внутри окна 65–80,
    # но не меньше минимума из настроек. Явный срок из текста сохраняется.
    hold_package(pkg, max(h.hold_min(), seconds + 5), component)
    h.start_app()
    time.sleep(2.0)
    ensure_menu()
    return pkg, component


def search_token(pkg):
    """Латинский кусок пакета для поиска в Swift Backup (без точек).

    Последний сегмент обычно самый уникальный: Lamb, Colombo, reward.
    Если он общий (mobile/app), берём самый длинный осмысленный сегмент.
    """
    parts = pkg.split(".")
    last = parts[-1]
    if last.lower() not in GENERIC and len(last) >= 3:
        return last
    meaningful = [p for p in parts if p.lower() not in GENERIC and len(p) >= 3]
    meaningful.sort(key=len, reverse=True)
    return meaningful[0] if meaningful else last


def raw_tap(x, y):
    h.adb("shell", f"input tap {int(x)} {int(y)}")


def raw_swipe(x1, y1, x2, y2, ms=300):
    h.adb("shell", f"input swipe {x1} {y1} {x2} {y2} {ms}")


def line_center(line):
    # find_word() может вернуть как dict (строку), так и готовую пару (x, y).
    # Раньше tuple падал здесь с AttributeError: 'tuple' has no attribute 'get'.
    if isinstance(line, (tuple, list)) and len(line) >= 2:
        return int(line[0]), int(line[1])
    words = line.get("words") or []
    if words:
        x = sum(w["x"] + w["w"] / 2 for w in words) / len(words)
        y = sum(w["y"] + w["h"] / 2 for w in words) / len(words)
        return int(x), int(y)
    return int(line["x"] + 100), int(line["y"] + 20)


def find_word_pos(lines, *words):
    """Центр слова на экране (сначала по словам, потом по строке).

    Нужен там, где у разных телефонов разные координаты пунктов меню:
    на Samsung тап по жёсткой координате попадал в «Журналы вызовов»
    вместо «Приложения».
    """
    wanted = {h.norm(word) for word in words}
    for line in lines:
        for word in line.get("words", []):
            if h.norm(word["t"]) in wanted:
                return word["x"] + word["w"] // 2, word["y"] + word["h"] // 2
    for line in lines:
        low = h.norm(line["text"])
        if any(word in low for word in wanted):
            return line_center(line)
    return None


def find_search_icon(a):
    """Значок лупы в верхней панели Swift Backup (он всегда на x≈787).

    Иконка не подписана текстом, поэтому ищем её по светлым пикселям
    в узкой вертикальной полосе. У Samsung панель выше, чем у Realme,
    поэтому y считаем по самим пикселям: берём самый длинный плотный
    кусок строк, а не крайние точки (иначе в полосу попадает текст
    первой карточки и тап уходит ниже иконки).
    """
    if a is None:
        return None
    x0, x1 = 755, 820
    band = a[80:280, x0:x1]
    if band.size == 0:
        return None
    mask = band.max(axis=2) > 150
    rows = np.flatnonzero(mask.sum(axis=1) >= 3)
    if not len(rows):
        return None
    groups = np.split(rows, np.flatnonzero(np.diff(rows) > 3) + 1)
    best = max(groups, key=len)
    if len(best) < 5:
        return None
    return (x0 + x1) // 2, 80 + int((best[0] + best[-1]) // 2)


def latest_backup(pkg):
    """Каталог с настоящим бэкапом.

    Пустая папка (Swift Backup успел создать её, но копирование не прошло)
    бэкапом не считается: иначе бот удалит приложение, не сохранив его.
    """
    out = h.adb("shell", f"ls -1 {SWIFT_ROOT}/*/backups/apps/local/{pkg} 2>/dev/null").stdout.decode("utf-8", "ignore")
    dirs = [line.strip() for line in out.splitlines() if line.strip()]
    for directory in sorted(dirs, reverse=True):
        listing = h.adb("shell", f"ls -l {directory}/{pkg}.xml 2>/dev/null").stdout.decode("utf-8", "ignore")
        for line in listing.splitlines():
            parts = line.split()
            # -rw-rw---- 1 owner group РАЗМЕР дата время имя
            if len(parts) >= 5 and parts[4].isdigit() and int(parts[4]) > 0:
                return directory
    return None


def backup_stamp(pkg):
    """Время последнего изменения XML бэкапа (Swift Backup пишет его при каждом копировании)."""
    out = h.adb(
        "shell",
        f"stat -c %Y {SWIFT_ROOT}/*/backups/apps/local/{pkg}/*/{pkg}.xml 2>/dev/null",
    ).stdout.decode("utf-8", "ignore")
    stamps = [int(x) for x in out.split() if x.isdigit()]
    return max(stamps) if stamps else 0


def has_backup(pkg):
    return latest_backup(pkg) is not None


def swift_open_apps():
    """Открывает раздел «Приложения» в Swift Backup.

    Пункт находим по слову на экране: у Samsung и Realme он стоит на
    разной высоте, и жёсткая координата попадала в «Журналы вызовов».
    """
    for _ in range(5):
        kind, a, path, lines = h.read_screen("sw_home.png", check_focus=False)
        pos = find_word_pos(lines, "приложения", "приложений", "приложение")
        if not pos:
            print("    Swift Backup: пункт «Приложения» не найден")
            return False
        raw_tap(*pos)
        time.sleep(3)
        kind, a, path, lines = h.read_screen("sw_list.png", check_focus=False)
        if any("локальн" in h.norm(l["text"]) for l in lines):
            return True
    print("    Swift Backup: список приложений не открылся")
    return False


def swift_search(query, attempts=3):
    """Включает поиск приложений в Swift Backup и вводит запрос.

    Возвращает строки экрана с результатами (пустые, если поиск не сработал).
    """
    lines = []
    for attempt in range(attempts):
        kind, a, path, lines = h.read_screen("sw_bar.png", check_focus=False)
        icon = find_search_icon(a) or (787, 187)
        raw_tap(*icon)
        time.sleep(1.2)
        # В поле может остаться прошлый запрос — чистим его целиком.
        h.adb("shell", "input keyevent 123 " + " ".join(["67"] * 40))
        h.adb("shell", f"input text {query}")
        time.sleep(3)
        kind, a, path, lines = h.read_screen("sw_res.png", check_focus=False)
        text = " ".join(l["text"] for l in lines).lower()
        if query.lower() in text:
            return lines
        print(f"    Swift Backup: поиск «{query}» не сработал (попытка {attempt + 1})")
    # Поиск не сработал — возвращаем пусто: иначе бот принял бы за результат
    # обычный список приложений и нажал не туда.
    return []


def swift_backup(pkg, query=None):
    """Создаёт локальный бэкап через UI Swift Backup. Возвращает успех."""
    try:
        return _swift_backup(pkg, query)
    finally:
        # После бэкапа всегда возвращаемся в AdvertApp: иначе бот остаётся
        # в Swift Backup и следующий тап не попадает в «Активные».
        if "swiftbackup" in h.focus().lower():
            h.start_app()
            time.sleep(2)


def _swift_backup(pkg, query=None):
    """Создаёт локальный бэкап через UI Swift Backup. Возвращает успех."""
    if has_backup(pkg):
        print(f"    Swift Backup: у {pkg} уже есть бэкап — пропускаю")
        return True
    before = backup_stamp(pkg)
    # Имя из карточки задания не всегда совпадает с названием в Swift Backup,
    # поэтому пробуем несколько запросов подряд.
    candidates = []
    for item in (query, search_token(pkg), pkg):
        if item and item not in candidates:
            candidates.append(item)
    print(f"    Swift Backup: бэкап {pkg} (запросы: {', '.join(candidates)})")
    h.adb("shell", f"am force-stop {SWIFT_PKG}")
    h.adb("shell", f"am start -n {SWIFT_HOME}")
    time.sleep(4)
    if not swift_open_apps():
        return False
    lines = []
    used = candidates[0] if candidates else ""
    for item in candidates:
        found = swift_search(item)
        if found:
            lines, used = found, item
            break
    if not lines:
        print(f"    Swift Backup: {pkg} не найден ни по одному запросу")
        return False
    text = " ".join(l["text"] for l in lines)
    if pkg.lower() not in text.lower().replace(" ", "") and used.lower() not in text.lower():
        print(f"    Swift Backup: {pkg} не найден по запросу «{used}»")
        return False
    # Похожих пакетов бывает два (kartu.Colombo и kartu.Lamb): жмём строку
    # именно с нужным пакетом, иначе откроется чужая карточка.
    target = None
    for l in lines:
        if pkg.lower() in l["text"].lower().replace(" ", ""):
            target = l
            break
    if target is None:
        for l in lines:
            if used.lower() in l["text"].lower():
                target = l
                break
    if target is None:
        print(f"    Swift Backup: строка результата для {pkg} не найдена")
        return False
    raw_tap(*line_center(target))
    time.sleep(3)
    # Кнопка «Резервное копирование» на карточке приложения. В новой версии
    # Swift Backup этого пункта на карточке нет: приложение отмечается
    # галочкой, а копирование запускает кнопка «Готово» внизу экрана.
    button = None
    for _ in range(4):
        kind, a, path, lines = h.read_screen("swd.png", check_focus=False)
        button = None
        for l in lines:
            if "резервное копирование" in h.norm(l["text"]) and l["y"] > 700:
                button = line_center(l)
                break
        if button:
            raw_tap(*button)
            break
        raw_swipe(540, 1900, 540, 900, 300)
        time.sleep(0.6)
    if button is None:
        kind, a, path, lines = h.read_screen("swd2.png", check_focus=False)
        pos = None
        for l in lines:
            if "готов" in h.norm(l["text"]) and l["y"] > 1900:
                pos = line_center(l)
                break
        if not pos:
            print("    Swift Backup: не нашёл ни «Резервное копирование», ни «Готово»")
            return False
        print("    Swift Backup: копирование запускаю кнопкой «Готово»")
        raw_tap(*pos)
    time.sleep(2)
    # Нижняя панель: APKs + «Устройство» уже выбраны
    raw_tap(648, 2184)
    time.sleep(2)
    end = time.monotonic() + 180
    while time.monotonic() < end:
        # Swift Backup перезаписывает существующую папку на месте:
        # признак свежего бэкапа — новый mtime XML, а не новая папка.
        if backup_stamp(pkg) != before:
            break
        time.sleep(2)
    else:
        print(f"    Swift Backup: бэкап {pkg} не обновился")
        return False
    if "TaskActivity" in h.focus():
        kind, a, path, lines = h.read_screen("sw_done.png", check_focus=False)
        pos = find_word_pos(lines, "готово", "готов", "закрыть")
        raw_tap(*(pos or (540, 2184)))  # ГОТОВО
        time.sleep(2)
    h.start_app()
    time.sleep(2)
    return has_backup(pkg)


def load_map():
    if not os.path.exists(MAP_PATH):
        return {}
    with open(MAP_PATH, encoding="utf-8") as file:
        return json.load(file)


def save_map(data):
    with open(MAP_PATH, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def load_known():
    """База знакомых заданий: заказы и отпечатки иконок.

    Формат: {"orders": {order: {...}}, "icons": [{hash, package, query}]}.
    Старый плоский формат (order → info) читается как "orders".
    """
    if not os.path.exists(KNOWN_PATH):
        return {"orders": {}, "icons": []}
    with open(KNOWN_PATH, encoding="utf-8") as file:
        data = json.load(file)
    if "orders" not in data:
        data = {"orders": data, "icons": []}
    data.setdefault("icons", [])
    data.setdefault("ambiguous", [])
    return data


def save_known(data):
    with open(KNOWN_PATH, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def known_package(order):
    """Пакет, который этим заказом ставили раньше (для повторов задания)."""
    info = load_known()["orders"].get(str(order)) or {}
    return info.get("package")


# ---------------------------------------------------------------------------
# новая база panel_apps.json: несколько ID и иконок на приложение

_NEW_ICON_INDEX = None
_NEW_ICON_MTIME = 0.0


def _new_db():
    try:
        from core.db import load_db
        return load_db()
    except Exception as error:
        print(f"  новая база недоступна: {error!r}")
        return None


def new_db_package(order):
    """Пакет из редактора базы: ID задания может повторяться у приложения."""
    db = _new_db()
    if not db:
        return None
    try:
        from core.db import find_app
        app = find_app(db, order_id=str(order))
        return app.get("package") if app else None
    except Exception:
        return None


def new_db_icon(a, icon_top):
    """Иконка из panel_apps.json и icon_cache (несколько вариантов на пакет)."""
    global _NEW_ICON_INDEX, _NEW_ICON_MTIME
    if a is None or icon_top is None:
        return None
    try:
        from core.config import DB_PATH
        from core.matcher import IconIndex
        mtime = os.path.getmtime(DB_PATH) if os.path.exists(DB_PATH) else 0.0
        if _NEW_ICON_INDEX is None or mtime != _NEW_ICON_MTIME:
            db = _new_db()
            if not db:
                return None
            _NEW_ICON_INDEX = IconIndex(db)
            _NEW_ICON_MTIME = mtime
        image = Image.fromarray(a.astype("uint8")).crop(
            (40, icon_top, 240, icon_top + 200))
        result = _NEW_ICON_INDEX.match_icon(image)
        if result.get("ok") and result.get("entry"):
            return result["entry"].package
    except Exception as error:
        print(f"  матчинг по новой базе не удался: {error!r}")
    return None


def learn_new_db(order, package, card):
    """Дописывает ID задания и иконку в panel_apps.json."""
    db = _new_db()
    if not db:
        return
    try:
        from core.matcher import learn
        image = None
        a, icon_top = card.get("a"), card.get("icon_top")
        if a is not None and icon_top is not None:
            image = Image.fromarray(a.astype("uint8")).crop(
                (40, icon_top, 240, icon_top + 200))
        learn(db, order_id=str(order), package=package,
              title=card.get("title", ""), image=image, source="bot")
    except Exception as error:
        print(f"  не удалось обновить новую базу: {error!r}")


def icon_hash(a, icon_top):
    """Отпечаток иконки задания (квадрат ~200×200 слева от шагов)."""
    if a is None or icon_top is None:
        return None
    image = Image.fromarray(a.astype("uint8")).crop((40, icon_top, 240, icon_top + 200))
    gray = image.convert("L")
    # Пустая/однотонная плашка — у части заданий иконка не грузится (баг сервера).
    if np.asarray(gray).std() < 6:
        return None
    image = gray.resize((17, 16), Image.LANCZOS)
    data = image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata()
    px = list(data)
    bits = 0
    for row in range(16):
        for col in range(16):
            bits = (bits << 1) | (px[row * 17 + col] > px[row * 17 + col + 1])
    return f"{bits:064x}"


def hamming(left, right):
    return bin(int(left, 16) ^ int(right, 16)).count("1")


def known_by_icon(digest, limit=4):
    """Ищет знакомое приложение по точной иконке (повтор задания).

    Общие иконки (джойстик у VIP-заданий, пустые плашки) в базе помечены
    как ambiguous и для поиска не используются — иначе можно поставить
    не то приложение.
    """
    if not digest:
        return None
    known = load_known()
    for other in known["ambiguous"]:
        try:
            if hamming(digest, other) <= 20:
                return None
        except ValueError:
            continue
    best = None
    for entry in known["icons"]:
        try:
            distance = hamming(digest, entry["hash"])
        except (KeyError, ValueError):
            continue
        if distance <= limit and (best is None or distance < best[0]):
            best = (distance, entry.get("package"))
    return best[1] if best else None


def remember_order(order, info, digest=None):
    """Запоминает «заказ → приложение» и отпечаток иконки навсегда.

    Записи не удаляются после выполнения задания: оно часто повторяется,
    и в следующий раз приложение ставится сразу из бэкапа. Общие иконки
    (джойстик, пустая плашка) помечаются ambiguous и не используются.
    """
    info = info or {}
    pkg = info.get("package")
    if not pkg:
        return
    known = load_known()
    known["orders"][str(order)] = {
        "package": pkg,
        "component": info.get("component"),
        "query": info.get("query"),
        "at": info.get("at") or datetime.now().isoformat(timespec="seconds"),
    }
    digest = digest or info.get("icon")
    if digest:
        clash = False
        for entry in list(known["icons"]):
            if entry.get("package") == pkg:
                known["icons"].remove(entry)
                continue
            try:
                if hamming(digest, entry["hash"]) <= 20:
                    # Иконка общая для разных приложений — запоминать нельзя.
                    known["ambiguous"].extend([digest, entry["hash"]])
                    known["icons"].remove(entry)
                    clash = True
            except ValueError:
                continue
        if clash:
            print(f"  {order}: иконка общая (джойстик/пустая) — не использую для поиска")
        else:
            known["icons"].append({"hash": digest, "package": pkg,
                                   "query": info.get("query")})
            print(f"  {order}: иконка {pkg} запомнена ({len(known['icons'])} в базе)")
    save_known(known)


def load_seen():
    try:
        with open(SEEN_PATH, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return {}


def save_seen(data):
    with open(SEEN_PATH, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def cleanup_delay_minutes():
    """Пауза перед удалением завершённого заказа (настройка панели)."""
    settings = _load_panel_settings() or {}
    cfg = settings.get("active", {}) if isinstance(settings, dict) else {}
    try:
        return max(0, int(cfg.get("cleanup_delay_minutes", 0) or 0))
    except (TypeError, ValueError):
        return 0


def cleanup_confirmations():
    """Сколько раз перезапускать AdvertApp, прежде чем считать заказ исчезнувшим."""
    settings = _load_panel_settings() or {}
    cfg = settings.get("active", {}) if isinstance(settings, dict) else {}
    try:
        return max(1, int(cfg.get("cleanup_confirmations", 2) or 2))
    except (TypeError, ValueError):
        return 2


def confirm_missing(orders, rounds=2, delay=8):
    """Подтверждает завершение заказа по ДВУМ условиям.

    1. Заказа нет в «Активных» после повторного перезапуска AdvertApp.
    2. Заказ есть в «Истории» AdvertApp — независимо от цвета шагов.

    Раньше проверялось только условие (1), из-за чего бот уходил в Swift
    Backup за ещё нужными приложениями. Старый guard неполного скана
    «Активных» сохранён: на неполных данных не удаляем ничего.
    """
    missing = set(orders)
    for round_index in range(max(1, rounds)):
        if not missing:
            break
        if round_index:
            time.sleep(delay)
        print(f"  проверка {round_index + 1}/{rounds}: перезапускаю AdvertApp")
        restart_to_menu()
        cards = scan_active()
        if cards is None or getattr(cards, "incomplete", False):
            print("  список «Активных» прочитан не полностью — ничего не удаляю")
            return set()
        present = {c["order"] for c in cards}
        missing -= present
        print(f"  проверка {round_index + 1}/{rounds}: не найдено "
              f"{len(missing)} из {len(orders)}")
    if not missing:
        return set()
    # Второе условие: заказ должен появиться в «Истории». Скан делаем один раз.
    # История может быть прочитана частично (не удалось открыть глубокую
    # страницу) — это безопасно: удаляем только номера, реально замеченные
    # в истории. None/пусто = ни одного заказа увидеть не удалось.
    history = scan_history(wanted=missing)
    if not history:
        print("  «История» не прочитана — ничего не удаляю")
        return set()
    finished = {order for order in missing if order in history}
    absent = missing - finished
    if absent:
        print(f"  в «Истории» не найдено {len(absent)} заказов — не удаляю их")
    return finished


def cleanup(mapping, active_orders, dry_run=False, active_incomplete=False):
    """Удаляет приложение завершённого заказа, если есть бэкап."""
    if active_incomplete:
        print("  список «Активных» неполный — cleanup пропущен")
        return
    seen = load_seen()
    now = datetime.now()
    for order in active_orders:
        seen[order] = now.isoformat(timespec="seconds")
    save_seen(seen)
    if not active_orders:
        # Пустой список — скорее сбой чтения, чем завершение всех заказов.
        print("  «Активные» пусты — удаление пропущено")
        return
    used = {info["package"] for order, info in mapping.items()
            if order in active_orders and info.get("package")}
    candidates = [order for order in mapping if order not in active_orders]
    if not candidates:
        return
    # Лимит в 3 заказа на вызов: если брать первые попавшиеся из mapping,
    # одни и те же старые «застрявшие» заказы (бэкап не создан) занимают все
    # слоты, и свежие завершения никогда не доходят до удаления. Поэтому
    # сначала берём те, что пропали из «Активных» совсем недавно.
    def last_seen(order):
        raw = seen.get(order)
        if not raw:
            return datetime.min
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return datetime.min
    candidates.sort(key=last_seen, reverse=True)
    if CLEANUP_MAX_ORDERS > 0 and len(candidates) > CLEANUP_MAX_ORDERS:
        print(f"  завершённых заказов {len(candidates)} — обрабатываю первые "
              f"{CLEANUP_MAX_ORDERS}, остальные в следующий раз")
        candidates = candidates[:CLEANUP_MAX_ORDERS]
    # Дополнительная пауза: заказ мог выпасть из списка на минуту.
    delay = cleanup_delay_minutes()
    if delay:
        waiting = []
        for order in candidates:
            last = seen.get(order)
            last_dt = None
            if last:
                try:
                    last_dt = datetime.fromisoformat(last)
                except ValueError:
                    last_dt = None
            if last_dt and now - last_dt < timedelta(minutes=delay):
                left = delay - int((now - last_dt).total_seconds() // 60)
                print(f"  {order}: пропал из «Активных» недавно — удаление отложено ({left} мин)")
                continue
            waiting.append(order)
        candidates = waiting
    if not candidates:
        return
    # Завершён только если исчез из «Активных» И появился в «Истории».
    finished = confirm_missing(candidates, rounds=cleanup_confirmations())
    for order, info in list(mapping.items()):
        if order not in finished:
            continue
        pkg = info.get("package")
        if not pkg or pkg in used:
            continue
        if not has_backup(pkg):
            print(f"  {order}: {pkg} без бэкапа — создаю через Swift Backup")
            if not swift_backup(pkg, info.get("query")):
                print(f"  {order}: бэкап не создан — не удаляю")
                continue
        print(f"  {order}: заказ завершён, удаляю {pkg}")
        # Связь «заказ → приложение» сохраняем навсегда: задание часто повторяется,
        # и в следующий раз приложение поставится сразу из бэкапа.
        remember_order(order, info)
        forget_new_taken(order)
        if dry_run:
            continue
        result = h.adb("shell", f"pm uninstall --user 0 {pkg}")
        print("   ", result.stdout.decode("utf-8", "ignore").strip())
        if result.returncode == 0:
            mapping.pop(order, None)
            seen.pop(order, None)
            save_map(mapping)
            save_seen(seen)


def task_priority(card, mapping):
    """Порядок обхода: сначала новые задания, потом многодневные.

    0 — первый шаг задания («Найти/Установить/Загрузить/Скачать»): на него
        дают около 40 минут, поэтому он важнее всего.
    1 — заказ ещё не попадал в order_apps.json, то есть новый.
    2 — многодневные шаги «Открыть на 2/3/8/16/31 день».
    """
    text = h.norm(card.get("green") or "")
    if any(word in text for word in ("найти", "установить", "загрузить", "скачать")):
        return 0
    if card["order"] not in mapping:
        return 1
    return 2


def load_new_taken():
    """Заказы, взятые из «Новых»: hunter2.py отмечает их при заборе."""
    try:
        with open(NEW_TAKEN_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {str(key) for key in data} if isinstance(data, dict) else set()
    except Exception:
        return set()


def forget_new_taken(order):
    """Снимает отметку «взято из «Новых»» — заказ уже обработан."""
    try:
        with open(NEW_TAKEN_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or str(order) not in data:
            return
        data.pop(str(order), None)
        with open(NEW_TAKEN_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def task_stage(card, new_taken):
    """Этап задания.

    0 — заказ взят из «Новых»: у него самый короткий срок, он идёт первым.
    1 — шаг «Установить/Загрузить/Найти»: приложение нужно поставить.
    2 — шаг «Открыть на N дней»: приложение уже стоит, его надо подержать.
    """
    if card["order"] in new_taken:
        return 0
    text = h.norm(card.get("green") or "")
    if any(word in text for word in ("найти", "установить", "загрузить", "скачать")):
        return 1
    return 2


def task_days(card):
    """Число дней в шаге «Открыть на N дней» (0 — шаг не про дни)."""
    text = h.norm(card.get("green") or "")
    match = re.search(r"открыть\s+на\s+(\d+)\s*дн", text)
    return int(match.group(1)) if match else 0


def task_sort_key(card, new_taken):
    """Ключ порядка: этап → число дней (2 → 8 → 16 → 31) → цена по убыванию."""
    stage = task_stage(card, new_taken)
    days = task_days(card)
    if stage == 1:
        days = 0
    return (stage, days, -float(card.get("price", 0.0)))


def main():
    parser = argparse.ArgumentParser(description="Задания по активным заказам AdvertApp")
    parser.add_argument("--dry-run", action="store_true", help="Ничего не нажимать и не удалять")
    parser.add_argument("--only", help="Выполнить только этот номер заказа")
    parser.add_argument("--until", help="Не начинать задания после HH:MM")
    parser.add_argument("--max-tasks", type=int, default=0,
                        help="Сколько заданий выполнить за проход (0 — все)")
    parser.add_argument("--serial", help="Серийный номер устройства (для фермы)")
    parser.add_argument("--slot", type=int, help="Номер слота фермы")
    args = parser.parse_args()
    if args.serial:
        configure_device(args.serial, args.slot)
        h.prepare_device()
    only = {args.only} if args.only else None
    until = None
    if args.until:
        try:
            hh, mm = (int(x) for x in args.until.split(":"))
        except ValueError:
            parser.error("--until ожидает HH:MM")
        until = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
        if until <= datetime.now():
            until += timedelta(days=1)

    cards = scan_active()
    if cards is None:
        print("не удалось открыть «Активные»")
        return 1
    print(f"активных заказов: {len(cards)}")
    for card in cards:
        if card["actionable"] and (only is None or card["order"] in only):
            print(f"  действие: {card['order']} — {card['price']:.2f} ₽ — {card['green']}")
    if args.dry_run:
        # В dry-run не трогаем карточки: координаты после прокрутки списка
        # уже не соответствуют текущему экрану.
        print("dry-run: нажатия не выполняются")
        return 0
    mapping = load_map()
    done = 0
    queue = [c for c in cards if c["actionable"]]
    if only is not None:
        queue = [c for c in queue if c["order"] in only]
    # Порядок обхода: по этапам — сначала заказы из «Новых» и шаги
    # «Установить», затем «Открыть на 2/8/16/31 дней». Внутри этапа —
    # сначала более короткие сроки, затем цена по убыванию.
    settings = _load_panel_settings() or {}
    active_cfg = settings.get("active", {}) if isinstance(settings, dict) else {}
    new_taken = load_new_taken()
    if active_cfg.get("order_by_stage", True):
        queue.sort(key=lambda c: task_sort_key(c, new_taken))
        print(f"порядок по этапам: взято из «Новых» — {len(new_taken)}")
    elif active_cfg.get("sort_by_price", True):
        queue.sort(key=lambda c: (-c.get("price", 0.0), task_priority(c, mapping)))
    else:
        queue.sort(key=lambda c: task_priority(c, mapping))
    for card in queue[:10]:
        print(f"  план: {card['order']} — этап {task_stage(card, new_taken)}, "
              f"{task_days(card)} дн, {card.get('price', 0.0):.2f} ₽ — {card['green']}")
    for card in queue:
        if args.max_tasks and done >= args.max_tasks:
            print(f"  выполнено {done} — лимит прохода")
            break
        if until and datetime.now() >= until - timedelta(seconds=150):
            print("  до окна бота меньше 2.5 мин — новые задания не начинаю")
            break
        # После прокрутки списка координаты из scan_active устарели:
        # возвращаемся в «Активные» и ищем заказ заново.
        if open_active()[0] is None:
            print("не удалось вернуться в «Активные»")
            break
        current = locate(card["order"])
        if current is None:
            print(f"  {card['order']}: не найден при прокрутке")
            continue
        # Отпечаток иконки берём только если карточка видна целиком: у нижней
        # кромки блок шагов обрезан, и позиция иконки считается неверно.
        icon_top = current.get("icon_top")
        current["icon"] = (icon_hash(current.get("a"), icon_top)
                           if icon_top and icon_top + 210 < h.CONTENT_BOTTOM else None)
        result = do_task(current, dry_run=args.dry_run)
        if result:
            pkg, component = result
            mapping[card["order"]] = {
                "package": pkg,
                "component": component,
                "query": search_token(pkg),
                "at": datetime.now().isoformat(timespec="seconds"),
                "icon": current.get("icon"),
            }
            save_map(mapping)
            remember_order(card["order"], mapping[card["order"]])
            done += 1

    cards = scan_active()
    if cards is None:
        print("не удалось прочитать «Активные» — cleanup пропущен")
        save_map(mapping)
        return 0
    active_orders = {c["order"] for c in cards}
    print(f"после обхода активных: {len(active_orders)}")
    cleanup(mapping, active_orders, dry_run=args.dry_run,
            active_incomplete=getattr(cards, "incomplete", False))
    save_map(mapping)
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        sys.exit(main())
    except (RuntimeError, OSError) as exc:
        print(f"остановлено: {exc}")
        sys.exit(1)
