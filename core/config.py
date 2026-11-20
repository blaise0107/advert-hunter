# -*- coding: utf-8 -*-
"""Настройки панели и пути проекта.

Все настройки хранятся в panel_settings.json рядом с базой приложений.
Файл перечитывается ботом перед каждым проходом, поэтому изменения
из панели подхватываются без перезапуска цикла.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "panel_apps.json")
SETTINGS_PATH = os.path.join(BASE, "panel_settings.json")
ICON_CACHE = os.path.join(BASE, "icon_cache")
BACKUP_DIR = os.path.join(BASE, "backups")

DEFAULT_SETTINGS = {
    "version": 1,
    "deadline": "08:30",
    "target_balance": 210.0,
    "min_withdraw": 150.0,
    "commission": 50.0,
    "theme": "dark",
    "accent": "#2ecc71",
    # Какой пакет AdvertApp использовать: "" = определить автоматически,
    # иначе com.advert.mobileearnings.app / com.teamdev.reward / ru.teamdev.reward.
    "app_package": "",
    # Минимальное удержание приложения по заданиям «Открыть»/«Открыть на N день».
    "hold_min_seconds": 70,
    "cycle": {
        "new_minutes": 12,
        "active_minutes": 25,
        "sleep_seconds": 30,
        "active_first": True,
    },
    "new": {
        # 0 = обходить все страницы списка «Новые», N = только первые N.
        # RU-задания живут в начале списка, поэтому по умолчанию хватает
        # первой страницы: обход и повторный поиск карточки идут быстрее.
        "max_pages": 1,
        "allow_second_page": True,
        # Листать список «Новые» до низа, а не только верхний экран.
        "scroll_to_bottom": True,
        # Контрольные проверки: сколько раз перепроверять весь список,
        # если доступных RU-заданий не осталось, и пауза между проверками.
        "recheck_count": 2,
        "recheck_delay": 10,
        # Сколько секунд ждать открытия пункта меню/задания: сервер
        # AdvertApp отвечает медленно, экран меняется не сразу.
        "open_wait_seconds": 75,
        "sort_by_price": True,
        "idle_stop_minutes": 8,
        # Временный ЧС заданий по ID: сколько минут не пытаться брать задание.
        "blacklist_ttl_minutes": 360,
        # Сколько предложений бот берёт из модалки за один прогон (0 — без лимита).
        "max_offers_per_run": 3,
    },
    "active": {
        "max_pages": 0,
        "empty_stop": 2,
        "hard_cap": 25,
        "sort_by_price": True,
        # Порядок обхода «Активных» по этапам: сначала заказы из «Новых» и
        # шаги «Установить», затем «Открыть на N дней» (2 → 8 → 16 → 31).
        "order_by_stage": True,
        # Сколько раз перезапускать AdvertApp и перепроверять список,
        # прежде чем считать заказ исчезнувшим и удалять приложение.
        "cleanup_confirmations": 2,
        # Дополнительная пауза перед удалением, минут (0 — не ждать).
        "cleanup_delay_minutes": 0,
        # На сколько часов до срока заказа («не позднее …») выполнять его.
        # 24 — выполняем заказ в последние сутки; 0 — всегда сразу.
        "deadline_margin_hours": 24,
        # Сколько секунд ждать открытия «Активных» и страницы заказа:
        # сервер AdvertApp отвечает медленно, экран меняется не сразу.
        "open_wait_seconds": 75,
        "retry_count": 2,
    },
    "match": {
        "icon_max_distance": 1.0,
        "icon_margin": 0.75,
        "hash_max_hamming": 4,
        "shared_hamming": 20,
        "id_over_icon": False,
        "min_confidence": 0.55,
    },
    "markets": ["google", "rustore", "huawei", "xiaomi"],
    "forbidden": ["samsung", "vpn"],
    # Обслуживание: кадры и логи не должны расти бесконечно.
    "maintenance": {
        "frames_max_age_minutes": 90,
        "frames_max_files": 600,
        "frames_max_mb": 250,
        "log_max_mb": 2,
        "log_keep": 10,
    },
    # Живой экран: поток только для выбранного телефона.
    "screen": {
        "fps": 1,
        "auto_stop": True,
    },
    # Ферма: задания на всех подключённых телефонах.
    "farm": {
        "max_parallel": 2,
        "round_minutes": 15,
        "techwait_seconds": 120,
        "restart_delay_seconds": 30,
        "disabled": [],
    },
    "notify": {
        "sound": False,
        "telegram": False,
    },
    # Конструктор стратегий: редактируется таблицей в панели «Стратегии».
    # active = "auto" — план выбирается по времени суток (from/to).
    "strategy": {
        "active": "auto",
        # Сколько секунд держать приложение после взятия задания,
        # прежде чем идти в «Новые» (по условиям 30 с … 2 мин).
        "hold_after_take_seconds": 80,
        # Отдых после успешного выполнения заказа: многодневные шаги не
        # нужно повторять каждые 20 минут (0 = без кулдауна).
        "done_cooldown_minutes": 40,
        # Постфактум-подтверждение unknown через перескан «Активных» по номеру
        # (предложение Qwen 4 MAX). По умолчанию ВЫКЛЮЧЕНО — включать только
        # после ночного наблюдения: в час пик лишний перескан может украсть
        # окно раздачи (оно длится ~4 с).
        "confirm_unknown": {"enabled": False},
        "plans": {
            "rush": {
                "name": "Ночной шквал (00:00–08:29)",
                "enabled": True,
                "from": "00:00:00", "to": "08:29:59",
                "steps": [
                    {"id": "r1", "enabled": True, "kind": "MODAL_PUMP", "tab": "",
                     "from": "00:00:00", "to": "23:59:59", "note": "Модалки всегда (приоритет 0)",
                     "params": {"repeat": 2}},
                    {"id": "r2", "enabled": True, "kind": "NAV_TAB", "tab": "NEW",
                     "from": "00:00:00", "to": "08:29:59", "note": "Открыть НОВЫЕ", "params": {}},
                    {"id": "r3", "enabled": True, "kind": "SCAN_NEW", "tab": "NEW",
                     "from": "00:00:00", "to": "08:29:59",
                     "note": "Собрать RU; стоп после 2 заходов без RU",
                     "params": {"empty_limit": 2, "max_pages": 1, "scroll_to_bottom": True}},
                    {"id": "r4", "enabled": True, "kind": "TAKE_NEW", "tab": "NEW",
                     "from": "00:00:00", "to": "08:29:59", "note": "Взять все RU в очередь",
                     "params": {"max_tasks": 0}},
                    {"id": "r5", "enabled": True, "kind": "EXECUTE_ACTIVE", "tab": "ACTIVE",
                     "from": "00:00:00", "to": "08:29:59", "note": "Сразу выполнить взятое",
                     "params": {"max_tasks": 0, "only_due": True}},
                    {"id": "r6", "enabled": True, "kind": "MODAL_PUMP", "tab": "",
                     "from": "00:00:00", "to": "23:59:59", "note": "Проверить модалки ещё раз",
                     "params": {}},
                ],
            },
            "day": {
                "name": "Дневной (08:30–23:59)",
                "enabled": True,
                "from": "08:30:00", "to": "23:59:59",
                "steps": [
                    {"id": "d0", "enabled": True, "kind": "MODAL_PUMP", "tab": "",
                     "from": "00:00:00", "to": "23:59:59", "note": "Модалки всегда (приоритет 0)",
                     "params": {}},
                    {"id": "d1", "enabled": True, "kind": "EXECUTE_ACTIVE", "tab": "ACTIVE",
                     "from": "08:30:00", "to": "23:59:59", "note": "Сначала выполнить взятое",
                     "params": {"max_tasks": 0, "only_due": True}},
                    {"id": "d2", "enabled": True, "kind": "NAV_TAB", "tab": "NEW",
                     "from": "08:30:00", "to": "23:59:59", "note": "Открыть НОВЫЕ", "params": {}},
                    {"id": "d3", "enabled": True, "kind": "SCAN_NEW", "tab": "NEW",
                     "from": "08:30:00", "to": "23:59:59",
                     "note": "Собрать RU; стоп после 3 заходов без RU",
                     "params": {"empty_limit": 3, "max_pages": 1, "scroll_to_bottom": True}},
                    {"id": "d4", "enabled": True, "kind": "TAKE_NEW", "tab": "NEW",
                     "from": "08:30:00", "to": "23:59:59", "note": "Взять RU и сразу выполнить",
                     "params": {"max_tasks": 0}},
                    {"id": "d5", "enabled": True, "kind": "WAIT", "tab": "",
                     "from": "08:30:00", "to": "23:59:59", "note": "Пауза 60 с (модалки опрашиваются)",
                     "params": {"seconds": 60}},
                ],
            },
            "urgent": {
                "name": "Срочное: только модалки и сроки",
                "enabled": True,
                "from": "00:00:00", "to": "23:59:59",
                "steps": [
                    {"id": "u1", "enabled": True, "kind": "MODAL_PUMP", "tab": "",
                     "from": "00:00:00", "to": "23:59:59", "note": "Модалки", "params": {}},
                    {"id": "u2", "enabled": True, "kind": "EXECUTE_ACTIVE", "tab": "ACTIVE",
                     "from": "00:00:00", "to": "23:59:59", "note": "Только заказы с близким сроком",
                     "params": {"max_tasks": 3, "only_due": True}},
                ],
            },
        },
    },
}


def _deep_merge(base, extra):
    """Рекурсивно дополняет base значениями extra, не теряя новые ключи."""
    result = copy.deepcopy(base)
    if not isinstance(extra, dict):
        return result
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_settings(path: str | None = None) -> dict:
    path = path or SETTINGS_PATH
    if not os.path.exists(path):
        return copy.deepcopy(DEFAULT_SETTINGS)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("корень настроек должен быть объектом")
        return _deep_merge(DEFAULT_SETTINGS, data)
    except Exception:
        return copy.deepcopy(DEFAULT_SETTINGS)


def save_settings(settings: dict, path: str | None = None) -> str:
    path = path or SETTINGS_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = _deep_merge(DEFAULT_SETTINGS, settings)
    fd, tmp = tempfile.mkstemp(prefix=".panel_settings_", suffix=".json",
                               dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return path


def backup_file(path: str) -> str | None:
    """Копирует файл в backups/ с меткой времени. Возвращает путь копии."""
    if not os.path.exists(path):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    name = os.path.basename(path)
    stem, ext = os.path.splitext(name)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"{stem}_{stamp}{ext}")
    with open(path, "rb") as src, open(dst, "wb") as out:
        out.write(src.read())
    return dst
