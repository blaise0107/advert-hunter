"""Ждёт окончания техработ AdvertApp и запускает «Активные» + «Новые».

Один процесс = один телефон. Ферма (farm.py) запускает несколько таких
процессов с разными --serial; все временные файлы и логи уходят в
runs/<serial>/, поэтому устройства не мешают друг другу.

Запуск в фоне:
    python resume_after_works.py
    python resume_after_works.py --serial 238a1a84440b7ece --once
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

# Консоль Windows в cp1251 не умеет стрелки в логах — принудительно UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from pathlib import Path

BASE = Path(__file__).resolve().parent
LOGS_DIR = BASE / "logs"
LOGS_DIR.mkdir(exist_ok=True)
sys.path.insert(0, str(BASE))
import hunter2 as h
try:
    from core.config import load_settings as _load_panel_settings
except Exception:  # цикл должен работать и без нового пакета core
    def _load_panel_settings():
        return {}

SERIAL = ""
SLOT = None


def log(message):
    prefix = f"[{SERIAL}] " if SERIAL else ""
    print(f"{datetime.now():%H:%M:%S} {prefix}{message}", flush=True)


def ocr_screen(name):
    remote = f"/sdcard/{name}.png"
    local = os.path.join(h.TMP, f"{name}.png")
    h.adb("shell", f"screencap -p {remote}")
    h.adb("pull", remote, local)
    try:
        return h.ocr(local) or []
    except Exception as error:
        log(f"ocr error: {error!r}")
        return []


def maintenance():
    text = h.norm(" ".join(line.get("text", "") for line in ocr_screen("tech")))
    return ("технические работы" in text or "будет доступен через" in text
            or "приносим извинения" in text)


def portrait():
    """Фиксирует портретную ориентацию: бот рассчитан на 1080×2412."""
    h.adb("shell", "settings put system accelerometer_rotation 0")
    h.adb("shell", "settings put system user_rotation 0")
    time.sleep(0.5)


def ready():
    h.adb("shell", "input keyevent KEYCODE_WAKEUP")
    h.adb("shell", "wm dismiss-keyguard")
    portrait()
    h.start_app()
    time.sleep(3)
    return not maintenance()


def _settings_int(settings, section, key, default):
    try:
        value = (settings.get(section) or {}).get(key, default)
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _settings_float(settings, section, key, default):
    try:
        value = (settings.get(section) or {}).get(key, default)
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


def run_new(new_minutes, deadline):
    left = int((deadline - datetime.now()).total_seconds() // 60)
    log(f"цикл: забор «Новых», осталось {left} мин")
    portrait()
    try:
        h.run(deadline_datetime=min(datetime.now() + timedelta(minutes=new_minutes), deadline),
              idle_stop=6)
    except Exception as error:
        log(f"h.run error: {error!r}")


def run_active(active_minutes, deadline):
    left = int((deadline - datetime.now()).total_seconds() // 60)
    log(f"цикл: «Активные», осталось {left} мин")
    portrait()
    until = min(datetime.now() + timedelta(minutes=active_minutes), deadline)
    command = [sys.executable, "-u", "tasks.py",
               "--until", f"{until:%H:%M}", "--max-tasks", "0"]
    if SERIAL:
        command += ["--serial", SERIAL, "--slot", str(SLOT or 0)]
    subprocess.run(command, cwd=str(BASE))


def main():
    global SERIAL, SLOT
    parser = argparse.ArgumentParser(description="Цикл AdvertApp для одного телефона")
    parser.add_argument("--serial", help="Серийный номер устройства")
    parser.add_argument("--slot", type=int, help="Номер слота фермы")
    parser.add_argument("--once", action="store_true",
                        help="Один круг «Активные» + «Новые» и выход (режим фермы)")
    parser.add_argument("--active-minutes", type=int, help="Длительность «Активных» в этом круге")
    parser.add_argument("--new-minutes", type=int, help="Длительность «Новых» в этом круге")
    parser.add_argument("--techwait", type=int, default=0,
                        help="Сколько секунд ждать окончания техработ (0 — до дедлайна)")
    parser.add_argument("--deadline", help="Время остановки HH:MM (по умолчанию из настроек)")
    args = parser.parse_args()

    SERIAL = (args.serial or "").strip()
    SLOT = args.slot
    if SERIAL:
        h.configure_device(SERIAL, SLOT)
        h.prepare_device()
    else:
        SERIAL = h.SERIAL
    # Явный запуск отменяет прежний запрос остановки.
    try:
        os.remove(os.path.join(str(BASE), "hunter.stop"))
    except OSError:
        pass

    settings = _load_panel_settings() or {}
    cycle = settings.get("cycle", {}) if isinstance(settings, dict) else {}
    new_minutes = args.new_minutes or _settings_int(settings, "cycle", "new_minutes", 12)
    active_minutes = args.active_minutes or _settings_int(settings, "cycle", "active_minutes", 25)
    sleep_seconds = _settings_int(settings, "cycle", "sleep_seconds", 30)
    active_first = bool(cycle.get("active_first", True))

    start = datetime.now()
    if args.deadline:
        try:
            hh, mm = (int(part) for part in args.deadline.split(":"))
        except ValueError:
            parser.error("--deadline ожидает HH:MM")
        deadline = start.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if deadline <= start:
            deadline += timedelta(days=1)
    elif args.once:
        deadline = start + timedelta(minutes=active_minutes + new_minutes + 3)
    else:
        deadline_text = str(settings.get("deadline", "08:30")) if isinstance(settings, dict) else "08:30"
        try:
            hh, mm = (int(part) for part in deadline_text.split(":"))
        except ValueError:
            hh, mm = 8, 30
        deadline = start.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if deadline <= start:
            deadline += timedelta(days=1)

    log(f"=== resume start (до {deadline:%d.%m %H:%M}) ===")
    tech_end = start + timedelta(seconds=args.techwait) if args.techwait else deadline
    while datetime.now() < tech_end:
        if ready():
            log("техработы закончились — начинаю")
            break
        log("техработы ещё идут — жду 60 с")
        time.sleep(60)
    else:
        log("техработы не закончились")
        return 2 if args.once else 1

    log(f"порядок цикла: {'сначала «Активные»' if active_first else 'сначала «Новые»'}; "
        f"«Новые» {new_minutes} мин, «Активные» {active_minutes} мин, пауза {sleep_seconds} с")

    def run_round():
        if active_first:
            run_active(active_minutes, deadline)
            if datetime.now() < deadline and not h.stop_requested():
                run_new(new_minutes, deadline)
        else:
            run_new(new_minutes, deadline)
            if datetime.now() < deadline and not h.stop_requested():
                run_active(active_minutes, deadline)

    if args.once:
        run_round()
        if os.path.isfile(h.OFFER_PATH) and datetime.now() < deadline:
            log("из модалки взято новое задание — сразу «Активные»")
            try:
                os.remove(h.OFFER_PATH)
            except OSError:
                pass
            run_active(active_minutes, deadline)
        log("=== resume stop (один круг) ===")
        return 0

    while datetime.now() < deadline and not h.stop_requested():
        run_round()
        # Из модалки могло быть взято новое задание: не ждём паузу,
        # сразу идём в «Активные» выполнять его.
        if os.path.isfile(h.OFFER_PATH):
            log("из модалки взято новое задание — сразу «Активные»")
            try:
                os.remove(h.OFFER_PATH)
            except OSError:
                pass
            continue
        if sleep_seconds:
            time.sleep(sleep_seconds)
    log("=== resume stop ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
