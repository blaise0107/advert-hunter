#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Конструктор стратегий AdvertApp.

Один процесс = одно устройство. План берётся из panel_settings.json →
"strategy" (редактируется таблицей в панели «Стратегии»).

Гарантии движка:
  * модалки обрабатываются перед каждым действием и во всех ожиданиях;
  * предложение из модалки имеет приоритет 0 и уходит в очередь работ;
  * заказ из «Новых» не считается сделанным, пока не выполнен;
  * если в очереди есть pending — следующий забор «Новых» не начинается;
  * каждый шаг пишется в logs/strategy.log.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

try:
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import hunter2 as h
import tasks as t
from core.config import load_settings

SERIAL = ""
SLOT = None
QUEUE_PATH = None
STRATEGY_LOG = BASE / "logs" / "strategy.log"
STOP = False
# Заказы, которые b.c. «рано» (до срока) или временно недоступны.
# Не берём их повторно 15 минут, иначе проход застревает на первом же.
SKIPPED_PATH = ""  # runs/<serial>/skipped.json — переживает перезапуск процесса
SKIPPED_TTL = 900
# periodically done orders (multi-day steps) — success cooldown
DONE_PATH = ""
DONE_TTL = 40 * 60
CLEANUP_EVERY = 15  # минут между бэкапами/удалением (метка в runs/<serial>/last_cleanup.txt)
ATTEMPT_LIMIT = 20


def skipped_load():
    try:
        with open(SKIPPED_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def skipped_save(data):
    try:
        with open(SKIPPED_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        pass


def done_load():
    try:
        with open(DONE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def done_save(data):
    try:
        with open(DONE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        pass


_PASS_COUNT = 0


def ensure_app():
    """Возврат в AdvertApp: cleanup/магазины оставляют фокус в чужих окнах."""
    if h.app_in_focus():
        return True
    log(f"фокус не AdvertApp ({h.focus()[:80]}) — возвращаюсь в приложение")
    try:
        h.start_app()
        time.sleep(2)
    except Exception as exc:
        log(f"не удалось вернуться в AdvertApp: {exc!r}")
        return False
    return h.app_in_focus()


CLEANUP_ACTIVE_ATTEMPTS = 3


def _scan_active_for_cleanup(attempts=CLEANUP_ACTIVE_ATTEMPTS):
    """Читает «Активные» с нескольких попыток.

    `scan_active()` внутри уже вызывает `open_active()` (у того свои 3
    попытки тапа по плитке), но после прохода экран может застрять в
    «Новых»/WAIT/модалке. Поэтому перед каждой новой попыткой явно
    возвращаемся в меню через `ensure_menu()` — следующий scan_active открывает
    «Активные» с чистого листа.

    Возвращает (cards, причина_неудачи). При успехе причина пустая.
    """
    reason = ""
    for attempt in range(1, max(1, attempts) + 1):
        try:
            if not ensure_app():
                reason = "AdvertApp не в фокусе"
                continue
            if attempt > 1:
                # Сброс экрана: после неудачи список мог остаться прокрученным
                # или уйти в другую вкладку.
                t.ensure_menu()
            cards = t.scan_active()
            if cards is None:
                reason = "не удалось открыть «Активные»"
                log(f"cleanup: {reason} (попытка {attempt}/{attempts})")
                continue
            # ВАЖНО: не подменять пустой результат на [] — вместе с ним
            # теряется флаг incomplete (ActiveCards) и guard перестаёт работать.
            if getattr(cards, "incomplete", False):
                reason = "список «Активных» неполный"
                log(f"cleanup: {reason} (попытка {attempt}/{attempts})")
                continue
            return cards, ""
        except Exception as exc:
            reason = repr(exc)
            log(f"cleanup: ошибка чтения «Активных» "
                f"(попытка {attempt}/{attempts}): {exc!r}")
        if attempt < attempts:
            time.sleep(2)
    return None, reason


def do_cleanup():
    """Бэкап+удаление завершённых заказов — раз в N минут.

    Счётчик проходов не работает: farm.py перезапускает процесс, и счётчик
    каждый раз начинается с 1. Метка времени в runs/<serial>/ переживает
    перезапуск. Вызывается в НАЧАЛЕ прохода (см. run_once), когда экран ещё
    в меню/«Активных», а не в «Новых» после SCAN_NEW/WAIT.
    """
    path = os.path.join(h.RUN_DIR, "last_cleanup.txt")
    try:
        with open(path, encoding="utf-8") as f:
            last = float(f.read().strip())
    except Exception:
        last = 0.0
    now = time.time()
    if now - last < CLEANUP_EVERY * 60:
        log(f"cleanup пропущен ({int((now - last) / 60)} мин с прошлого, раз в {CLEANUP_EVERY} мин)")
        return
    cards, reason = _scan_active_for_cleanup()
    if cards is None:
        # Все попытки неполные/неудачные — удалять нельзя (безопасность).
        log(f"cleanup: удаление пропущено: {reason}")
        return
    incomplete = getattr(cards, "incomplete", False)
    mapping = t.load_map()
    if not ensure_app():
        return
    try:
        t.cleanup(mapping, {c["order"] for c in cards}, active_incomplete=incomplete)
        t.save_map(mapping)
        log("cleanup выполнен")
    except Exception as exc:
        log(f"cleanup: ошибка: {exc!r}")
    finally:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(str(time.time()))
        except OSError:
            pass
        ensure_app()

# ---------------------------------------------------------------------------
def log(message):
    prefix = f"[{SERIAL}] " if SERIAL else ""
    line = f"{datetime.now():%H:%M:%S} {prefix}{message}"
    print(line, flush=True)
    try:
        STRATEGY_LOG.parent.mkdir(exist_ok=True)
        with open(STRATEGY_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def configure(serial, slot=None):
    global SERIAL, SLOT, QUEUE_PATH
    SERIAL = serial or ""
    SLOT = slot
    h.configure_device(SERIAL, SLOT)
    t.configure_device(SERIAL, SLOT)
    QUEUE_PATH = Path(h.RUN_DIR) / "work_queue.json"
    global SKIPPED_PATH, DONE_PATH
    SKIPPED_PATH = os.path.join(h.RUN_DIR, "skipped.json")
    DONE_PATH = os.path.join(h.RUN_DIR, "done_recent.json")


# ---------------------------------------------------------------------------
# Очередь работ
# ---------------------------------------------------------------------------
def queue_load():
    try:
        with open(QUEUE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def queue_save(queue):
    try:
        with open(QUEUE_PATH, "w", encoding="utf-8") as f:
            json.dump(queue, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        log(f"не удалось сохранить очередь: {exc!r}")


def queue_add(order, source, priority=5):
    order = str(order or "").strip()
    if not order or order.lower() in ("unprompted", "none"):
        return
    queue = queue_load()
    if order in queue and queue[order].get("status") in ("done", "lost"):
        return
    queue[order] = {
        "status": "pending", "source": source, "priority": int(priority),
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    queue_save(queue)
    log(f"в очередь: {order} (источник {source}, приоритет {priority})")


def queue_set(order, status):
    queue = queue_load()
    if str(order) in queue:
        queue[str(order)]["status"] = status
        queue_save(queue)


def prune_stale_queue(max_age_hours=2):
    """Просроченные pending-заказы помечаем lost, чтобы их можно было взять
    заново. Иначе заказ, однажды не выполненный, висит в очереди часами,
    take_new видит «уже в очереди» и никогда не пытается снова."""
    queue = queue_load()
    changed = False
    now = datetime.now()
    for order, info in queue.items():
        if info.get("status") != "pending":
            continue
        at = info.get("at")
        try:
            when = datetime.fromisoformat(at)
        except (TypeError, ValueError):
            continue
        if (now - when).total_seconds() > max_age_hours * 3600:
            info["status"] = "lost"
            changed = True
            log(f"{order}: просрочен в очереди ({max_age_hours} ч) — помечаю lost")
    if changed:
        queue_save(queue)


def queue_pending():
    prune_stale_queue()
    queue = queue_load()
    return [k for k, v in queue.items() if v.get("status") == "pending"]


def queue_summary():
    queue = queue_load()
    counts = {}
    for value in queue.values():
        status = value.get("status", "?")
        counts[status] = counts.get(status, 0) + 1
    return ", ".join(f"{k}={v}" for k, v in counts.items()) or "пусто"


# ---------------------------------------------------------------------------
# Модальные окна — высокий приоритет
# ---------------------------------------------------------------------------
def modal_pump():
    """Обрабатывает модалку, если она на экране. Возвращает статус или ''."""
    try:
        kind, a, path, lines = h.read_screen("str_modal.png", check_focus=False)
    except Exception as exc:
        log(f"modal_pump: кадр не прочитан: {exc!r}")
        return ""
    if kind != "modal" or a is None:
        return ""
    # Главное меню может ложно классифицироваться как modal (is_dim).
    # Не кормим его handle_dialog — иначе бесконечный цикл BACK.
    if h.menu_score(lines) >= 3:
        log("modal_pump: главное меню, не модалка — пропускаю")
        return ""
    text = " | ".join(line.get("text", "") for line in (h.modal_lines(a, path) or lines))
    try:
        status = h.handle_dialog("unprompted", a, path, "navigation", navigation=True)
        log(f"модалка: {status} — {text[:120]}")
        if status == "offer":
            match = re.search(r"(?<!\d)(\d{5,8})(?!\d)", text.replace("NQ", ""))
            if match:
                queue_add(match.group(1), "modal", priority=0)
            else:
                log("модалка: номер заказа не распознан — смотрите results.jsonl")
        return status
    except Exception as exc:
        log(f"modal_pump: ошибка обработки модалки: {exc!r}")
        try:
            ensure_app()
        except Exception:
            pass
        return ""


# ---------------------------------------------------------------------------
# Шаги стратегии
# ---------------------------------------------------------------------------
def nav_tab(tab):
    tab = (tab or "").upper()
    try:
        ensure_app()
        if tab == "NEW":
            log("переход: НОВЫЕ")
            result = h.open_new(force_reenter=True, timeout=h.open_wait_seconds())
            return result[0] is not None
        if tab == "ACTIVE":
            log("переход: АКТИВНЫЕ")
            t.ensure_menu()
            a, _path, _lines = t.open_active()
            return a is not None
        log(f"неизвестная вкладка: {tab!r}")
    except RuntimeError as exc:
        # Фокус мог уйти (системный диалог, модалка магазина) между
        # проверкой и тапом — возвращаем приложение и пробуем ещё раз.
        log(f"переход в {tab}: гонка фокуса ({exc}) — возвращаюсь в приложение")
        ensure_app()
        time.sleep(1)
    return False


def scan_new(empty_limit=3, max_pages=None, scroll=True, take_callback=None):
    """Собирает RU-задания; стоп после N заходов без RU."""
    empty_limit = max(1, int(empty_limit or 3))
    settings = load_settings() or {}
    new_cfg = settings.get("new", {})
    if max_pages is None:
        max_pages = new_cfg.get("max_pages", 1)
    found = {}
    empty = 0
    for attempt in range(1, empty_limit + 1):
        modal_pump()
        cards, _cur, total = h.collect_new_cards(
            max_pages=int(max_pages or 0),
            scroll_to_bottom=bool(scroll and new_cfg.get("scroll_to_bottom", True)),
            empty_stop=int(new_cfg.get("empty_stop", 2)),
            deadline=datetime.now() + timedelta(minutes=10),
        )
        ru = [c for c in cards if c.get("ru")]
        for c in ru:
            found[str(c["order"])] = c
        log(f"заход {attempt}/{empty_limit}: карточек {len(cards)}, RU {len(ru)}, "
            f"всего уникальных {len(found)}")
        if ru:
            empty = 0
            #azurh любой RU — берём его СРАЗУ, пока экран свежий, и продолжаем
            # искать дальше: пока в «Новых» появляются задания, забираем все.
            if take_callback is not None:
                # После взятия AdvertApp перестраивает список: остальные
                # карточки этого же захода превращаются в долгий поиск
                # несуществующего заказа. Берём только верхнюю (свежую),
                # остальные дождутся следующего скана.
                best = min(ru, key=lambda c: c.get("top", 1 << 30))
                take_callback([best])
                found.clear()
            time.sleep(max(1, int(new_cfg.get("recheck_delay", 10))))
            nav_tab("NEW")
            continue
        empty += 1
        if empty >= empty_limit:
            break
        time.sleep(max(1, int(new_cfg.get("recheck_delay", 10))))
        nav_tab("NEW")
    result = list(found.values())
    if load_settings().get("new", {}).get("sort_by_price", True):
        result.sort(key=lambda c: (-float(c.get("price", 0) or 0), c.get("top", 0)))
    return result


def take_new(cards, max_tasks=0):
    """Берёт RU-задания, складывает в очередь работ."""
    taken = 0
    for card in (cards or []):
        if STOP:
            break
        modal_pump()
        if max_tasks and taken >= max_tasks:
            log(f"лимит забора {max_tasks} — дальше не беру")
            break
        order = str(card["order"])
        prune_stale_queue()
        _q = queue_load()
        if order in _q and _q[order].get("status") == "pending":
            log(f"{order}: уже в очереди (pending) — пропускаю")
            continue
        if order in _q and _q[order].get("status") == "done":
            log(f"{order}: недавно выполнен — пропускаю забор")
            continue
        # status lost/просроченный — разрешаем взять заново (queue_add перезапишет)
        skipped_now = skipped_load()
        if time.time() - skipped_now.get(order, 0) < SKIPPED_TTL:
            log(f"{order}: недавно был недоступен — пропускаю забор")
            continue
        fresh, path = None, None
        # Сначала пробуем карточку с тем экрана, где её только что увидели:
        # координаты свежие, повторный поиск по номеру часто не находит
        # заказ, потому что список «Новых» успевает перестроиться.
        if card.get("path") and os.path.isfile(card["path"]):
            # Координаты из collect_new_cards уже устарели: список успел
            # перестроиться. Перепроверяем номер на СЕЙЧАШНЕМ экране.
            kind_now, a_now, path_now, lines_now = h.read_screen("tk.png")
            match = None
            if kind_now == "list":
                match = next((c for c in h.analyze_cards(a_now, lines_now)
                              if h.same_order(order, c["order"])), None)
            if match is not None:
                fresh, path = match, path_now
                log(f"{order}:(card=свежий экран top={match.get('top')})")
            else:
                log(f"{order}: Карточка на текущем экране не найдена — ищу заново")
                fresh, path = h.locate_new_card(
                    order, max_pages=int(load_settings().get("new", {}).get("max_pages", 1) or 0),
                    scroll_to_bottom=True, empty_stop=2,
                    deadline=datetime.now() + timedelta(seconds=45),
                    hint_page=card.get("page"), hint_top=card.get("top"),
                )
        else:
            fresh, path = h.locate_new_card(
                order, max_pages=int(load_settings().get("new", {}).get("max_pages", 1) or 0),
                scroll_to_bottom=True, empty_stop=2,
                deadline=datetime.now() + timedelta(seconds=45),
                hint_page=card.get("page"), hint_top=card.get("top"),
            )
        if fresh is None:
            log(f"{order}: карточка не найдена при повторном поиске — пропускаю на 15 мин")
            skipped_now = skipped_load()
            skipped_now[order] = time.time()
            skipped_save(skipped_now)
            continue
        status = h.attempt_order(fresh, path, datetime.now() + timedelta(minutes=3))
        log(f"{order}: попытка взять → {status}")
        if status == "unavailable":
            # Карточка в «Новых» уже выдана/закрыта: без паузы бот будет
            # пытаться её каждый проход и не дойдёт до свежих заданий.
            skipped_now = skipped_load()
            skipped_now[order] = time.time()
            skipped_save(skipped_now)
        if status == "not_visible":
            # Карточка исчезла — НЕ считаем заказ взятым. AdvertApp обязан
            # показать модалку с инструкцией/предложением: ищем её сам.
            log(f"{order}: карточка исчезла — ищу модалку AdvertApp")
            kind, a, path, lines = h.read_screen("nv.png", check_focus=False)
            if kind == "modal":
                mtext = " | ".join(line["text"] for line in h.modal_lines(a, path))
                mkind = h.modal_kind(mtext)
                log(f"{order}: модалка {mkind}: {mtext[:140]}")
                status = h.handle_dialog(order, a, path, "open")
                if mkind == "instructions" and status in (None, "unknown"):
                    status = "instructions"
            else:
                log(f"{order}: модалки нет — заказ НЕ взят, пропускаю на 15 мин")
                skipped_now = skipped_load()
                skipped_now[order] = time.time()
                skipped_save(skipped_now)
                status = "unknown"
        # install_done — это только установка: заказ уже в «Активных», его
        # должен обработать EXECUTE_ACTIVE. instructions_failed тоже остаётся
        # в очереди (с TTL), чтобы его перепроверили в следующем проходе.
        if status in ("offer", "accepted", "instructions", "market_opened",
                      "install_done", "instructions_failed"):
            queue_add(order, "new", priority=1)
            taken += 1
            if status == "instructions_failed":
                skipped_now = skipped_load()
                skipped_now[order] = time.time(); skipped_save(skipped_now)
                continue
            # По условиям AdvertApp нужно провести во взятом задании от 30 с
            # до 2 мин, иначе задание не засчитывается. Держим приложение
            # перед возвратом в «Новые».
            hold_after = int(load_settings().get("strategy", {}).get(
                "hold_after_take_seconds", 80) or 0)
            if hold_after > 0:
                log(f"{order}: удерживаю приложение {hold_after} с после взятия")
                h.hold_foreground(h.LAST_HELD_PKG or h.active_app(), hold_after)
        if status == "offer":
            break
        nav_tab("NEW")
    return taken


def execute_active(max_tasks=0, only_due=False):
    """Выполняет «Активные» пачкой, пока не закончатся зелёные подписи.

    После каждого успешного шага список пересканируется заново: у day-gated
    заданий следующий зелёный шаг («Открыть на N+1 день») появляется сразу,
    и его нужно взять в том же проходе, а не ждать следующего цикла.
    """
    if not ensure_app():
        return 0
    attempt_limit = int(max_tasks) if max_tasks else ATTEMPT_LIMIT
    mapping = t.load_map()
    done = 0
    attempts = 0
    # Заказ -> уже обработанные подписи шагов в этом вызове. Один и тот же
    # заказ может дать несколько зелёных шагов подряд — это не зацикливание,
    # пока меняется подпись шага.
    handled = {}
    while not STOP and attempts < attempt_limit:
        try:
            cards = t.scan_active()
        except RuntimeError as exc:
            log(f"сканирование «Активных» прервано: {exc}; возвращаюсь в приложение")
            ensure_app()
            break
        if cards is None:
            log("не удалось открыть «Активные»")
            break
        actionable = []
        for card in cards:
            if not card.get("actionable"):
                continue
            order = str(card["order"])
            signature = h.norm(str(card.get("green") or ""))
            if signature in handled.get(order, ()):
                continue
            actionable.append(card)
        # Отложенные по сроку/ошибке заказы не трогаем 15 минут.
        skipped = skipped_load()
        done_recent = done_load()
        now_wall = time.time()
        actionable = [c for c in actionable
                      if now_wall - skipped.get(str(c["order"]), 0) >= SKIPPED_TTL]
        done_ttl = int(load_settings().get("strategy", {}).get("done_cooldown_minutes", 40) or 0) * 60
        actionable = [c for c in actionable
                      if not done_ttl or now_wall - done_recent.get(str(c["order"]), 0) >= done_ttl]
        log(f"активных {len(cards)}, зелёных шагов {len(actionable)} "
            f"(отложено {sum(1 for k, v in skipped.items() if now_wall - v < SKIPPED_TTL)})")
        if not actionable:
            break
        pending = set(queue_pending())
        # Приоритет по правилу пользователя: все зелёные подписи выполняем
        # немедленно, а «Найти/Установить/Открыть приложение» — первыми
        # (на них всего 40 минут). task_sort_key уже ставит эти шаги этапом 1,
        # но добавляем явный вес, чтобы они гарантированно шли раньше day-gated.
        def _priority(c):
            text = h.norm(str(c.get("green") or ""))
            urgent = any(w in text for w in ("найти", "установить", "загрузить",
                                              "скачать", "открыть приложение"))
            return (0 if urgent else 1,
                    0 if c["order"] in pending else 1,
                    t.task_sort_key(c, t.load_new_taken()))
        actionable.sort(key=_priority)
        progress = False
        for card in actionable:
            if STOP or attempts >= attempt_limit:
                break
            modal_pump()
            attempts += 1
            order = str(card["order"])
            handled.setdefault(order, set()).add(h.norm(str(card.get("green") or "")))
            if only_due:
                log(f"{order}: режим only_due — срок проверяется в do_task")
            skipped = skipped_load()
            # По правилу пользователя зелёная подпись = выполнять немедленно,
            # без «отложить на 15 мин». Игнорируем skipped, если у заказа есть
            # активный (actionable) зелёный шаг — особенно «Найти/Установить/
            # Открыть приложение», где срок всего 40 минут.
            if order in skipped and time.time() - skipped[order] < SKIPPED_TTL \
                    and not card.get("actionable"):
                continue
            try:
                if t.open_active()[0] is None:
                    log("не удалось вернуться в «Активные»")
                    break
                current = t.locate(order)
            except RuntimeError as exc:
                log(f"{order}: потерян фокус ({exc}) — возвращаюсь в приложение")
                ensure_app()
                skipped[order] = time.time(); skipped_save(skipped)
                continue
            if current is None:
                log(f"{order}: не найден при прокрутке")
                continue
            icon_top = current.get("icon_top")
            current["icon"] = (t.icon_hash(current.get("a"), icon_top)
                               if icon_top and icon_top + 210 < h.CONTENT_BOTTOM else None)
            try:
                result = t.do_task(current, only_due=only_due)
            except RuntimeError as exc:
                log(f"{order}: действие прервано ({exc}) — возвращаюсь в приложение")
                ensure_app()
                skipped[order] = time.time(); skipped_save(skipped)
                continue
            if result:
                pkg, component = result
                mapping[order] = {
                    "package": pkg, "component": component,
                    "query": t.search_token(pkg),
                    "at": datetime.now().isoformat(timespec="seconds"),
                    "icon": current.get("icon"),
                }
                t.save_map(mapping)
                t.remember_order(order, mapping[order])
                # Удержание сделано, но по алгоритму AdvertApp это ещё не
                # выполнение: помечаем done только по реальному подтверждению.
                if t.order_confirmed(order):
                    queue_set(order, "done")
                    done_recent = done_load()
                    done_recent[order] = time.time(); done_save(done_recent)
                    done += 1
                    log(f"{order}: выполнено ({pkg}) — AdvertApp подтвердил")
                else:
                    if order in queue_load():
                        queue_set(order, "pending")
                    else:
                        queue_add(order, "active", priority=2)
                    log(f"{order}: шаг сделан ({pkg}), заказ остаётся в «Активных» — ждём следующий зелёный шаг")
                skipped = skipped_load(); skipped.pop(order, None); skipped_save(skipped)
                progress = True
                break  # пересканируем: возможен следующий зелёный шаг подряд
            skipped[order] = time.time(); skipped_save(skipped)
            log(f"{order}: не выполнено — отложено на {SKIPPED_TTL // 60} мин")
        if not progress:
            break
    log(f"выполнено за проход: {done}; очередь: {queue_summary()}")
    return done


def wait_seconds(seconds):
    seconds = max(0, int(seconds or 0))
    end = time.monotonic() + seconds
    while time.monotonic() < end and not STOP:
        modal_pump()
        time.sleep(min(3, max(0, end - time.monotonic())))


# ---------------------------------------------------------------------------
# План и время
# ---------------------------------------------------------------------------
def in_window(step, now):
    start = str(step.get("from") or "00:00:00")
    end = str(step.get("to") or "23:59:59")
    cur = now.strftime("%H:%M:%S")
    if start <= end:
        return start <= cur <= end
    return cur >= start or cur <= end


def pick_plan(settings):
    strategy = (settings or {}).get("strategy") or {}
    plans = strategy.get("plans") or {}
    active = strategy.get("active") or "auto"
    if active != "auto" and active in plans:
        return active, plans[active]
    now = datetime.now().strftime("%H:%M:%S")
    for name, plan in plans.items():
        if not plan.get("enabled", True):
            continue
        if in_window({"from": plan.get("from", "00:00:00"),
                      "to": plan.get("to", "23:59:59")}, datetime.now()):
            return name, plan
    if plans:
        name = next(iter(plans))
        return name, plans[name]
    return "", {}


def run_step(step):
    kind = str(step.get("kind") or "").upper()
    params = step.get("params") or {}
    if kind == "NAV_TAB":
        return nav_tab(step.get("tab") or params.get("tab", "NEW"))
    if kind == "SCAN_NEW":
        return scan_new(params.get("empty_limit", 3), params.get("max_pages"),
                        params.get("scroll_to_bottom", True))
    if kind == "TAKE_NEW":
        return take_new(params.get("cards") or [], params.get("max_tasks", 0))
    if kind == "EXECUTE_ACTIVE":
        return execute_active(params.get("max_tasks", 0), params.get("only_due", False))
    if kind == "MODAL_PUMP":
        return modal_pump()
    if kind == "WAIT":
        wait_seconds(params.get("seconds", 30))
        return True
    if kind in ("STOP", "END"):
        global STOP
        STOP = True
        return True
    log(f"неизвестный шаг: {kind!r}")
    return False


def run_once(dry_run=False):
    settings = load_settings() or {}
    name, plan = pick_plan(settings)
    steps = [s for s in (plan.get("steps") or []) if s.get("enabled", True)]
    # Приоритет каждого часа: сначала собрать ВСЕ RU в «Новых», пока не
    # останется, и только потом выполнять «Активные». Поэтому пред-выполнение
    # очереди из прошлых проходов убрано — оно уводило бота в «Активные» и
    # мешало хватать свежие заказы в начале часа.
    log(f"=== стратегия {name}: {plan.get('name', '')} — шагов {len(steps)} ===")
    if dry_run:
        for i, step in enumerate(steps, 1):
            log(f"ПРЕДПРОСМОТР {i}/{len(steps)}: {step.get('kind')} "
                f"{step.get('tab', '')} {step.get('from')}–{step.get('to')} "
                f"{step.get('params', {})}")
        return 0
    pending = queue_pending()
    if pending:
        log(f"в очереди {len(pending)} невыполненных — выполню после сбора «Новых»")
    # Cleanup ПЕРЕД шагами: после рестарта процесса/фермы экран ещё в меню
    # или «Активных». Раньше вызов стоял в конце — к тому моменту экран был
    # в «Новых»/WAIT, и scan_active возвращал None/неполный список, из‑за чего
    # guard корректно блокировал удаление, но завершённые заказы не удалялись.
    ensure_app()
    modal_pump()
    do_cleanup()
    ensure_app()
    for i, step in enumerate(steps, 1):
        if STOP:
            break
        ensure_app()
        modal_pump()
        if not in_window(step, datetime.now()):
            log(f"ШАГ {i}/{len(steps)}: вне окна {step.get('from')}–{step.get('to')} — пропуск")
            continue
        log(f"ШАГ {i}/{len(steps)}: {step.get('kind')} "
            f"{step.get('tab', '')} ({step.get('from')}–{step.get('to')})")
        try:
            if step.get("kind") == "SCAN_NEW":
                take_steps = [s for s in steps if s.get("kind") == "TAKE_NEW"]

                def _take_batch(batch):
                    for take in take_steps:
                        try:
                            take_new(batch, (take.get("params") or {}).get("max_tasks", 0))
                        except RuntimeError as exc:
                            # Фокус ушёл (системный экран/VPN/Play) между
                            # проверкой и тапом в attempt_order — не роняем
                            # весь шаг SCAN_NEW, возвращаемся в AdvertApp.
                            log(f"забор RU прерван потерей фокуса: {exc}; возвращаюсь")
                            ensure_app()
                            break

                cards = scan_new(
                    (step.get("params") or {}).get("empty_limit", 3),
                    (step.get("params") or {}).get("max_pages"),
                    take_callback=_take_batch if take_steps else None,
                )
                if cards:
                    _take_batch(cards)
                continue
            run_step(step)
        except Exception as exc:
            log(f"ШАГ {i}: ошибка {exc!r}")
            log(traceback.format_exc())
            modal_pump()
    ensure_app()
    log(f"=== конец прохода; очередь: {queue_summary()} ===")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Конструктор стратегий AdvertApp")
    parser.add_argument("--serial", help="серийный номер устройства")
    parser.add_argument("--slot", type=int, help="номер слота фермы")
    parser.add_argument("--plan", help="имя плана (иначе auto по времени)")
    parser.add_argument("--dry-run", action="store_true", help="только предпросмотр плана")
    parser.add_argument("--duration", type=float, default=0,
                        help="повторять проходы N секунд (0 — один проход)")
    args = parser.parse_args()
    configure(args.serial, args.slot)
    if args.plan:
        settings = load_settings()
        settings.setdefault("strategy", {})["active"] = args.plan
    end = time.monotonic() + (args.duration or 0)
    while not STOP:
        run_once(dry_run=args.dry_run)
        if not args.duration or time.monotonic() >= end:
            break
        wait_seconds(20)
    return 0


if __name__ == "__main__":
    sys.exit(main())
