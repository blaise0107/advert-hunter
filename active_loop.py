"""Автопроверка «Активных» 2–3 раза в час вне окон ночного бота.

Запуск в фоне:
    python active_loop.py

Проходы в :12, :32, :52. Пропускает:
  * окно ночного бота :59:30–:08:00;
  * моменты, когда hunter2.log менялся менее 120 с назад — значит бот реально
    работает (включая непрерывный ночной забор 23:59:30–01:59:30);
  * тихие часы 02:00–07:59, чтобы не будить телефон после ночного забора.

За один проход tasks.py выполняет не больше двух заданий и не начинает новое,
если до :58 осталось меньше 2.5 минут.
"""
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

from pathlib import Path

BASE = Path(__file__).resolve().parent
LOGS_DIR = BASE / "logs"
LOGS_DIR.mkdir(exist_ok=True)
LOG_CANDIDATES = [LOGS_DIR / "hunter2.log", BASE / "hunter2.log"]
PASS_MINUTES = (12, 32, 52)
BOT_END_MINUTE = 8


def log(message):
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"{stamp} {message}", flush=True)


def in_bot_window(now):
    seconds = now.minute * 60 + now.second
    return seconds >= 59 * 60 + 30 or seconds < BOT_END_MINUTE * 60


def bot_running():
    now = time.time()
    for path in LOG_CANDIDATES:
        try:
            if now - os.path.getmtime(path) < 120:
                return True
        except OSError:
            pass
    # Персональные логи фермы (runs/*/hunter2.log) — бот может работать там.
    try:
        for run_log in (BASE / "runs").glob("*/hunter2.log"):
            if now - os.path.getmtime(run_log) < 120:
                return True
    except OSError:
        pass
    return False


def current_slot(now):
    """Ближайший уже наступивший проход в этом часе (:12/:32/:52)."""
    for minute in reversed(PASS_MINUTES):
        if now.minute >= minute:
            return now.replace(minute=minute, second=0, microsecond=0)
    return None


def main():
    log("=== active_loop start ===")
    last_slot = None
    while True:
        now = datetime.now()
        if 2 <= now.hour < 8:
            time.sleep(60)
            continue
        slot = current_slot(now)
        if slot is None or slot == last_slot:
            time.sleep(20)
            continue
        if in_bot_window(now) or bot_running():
            # Задним числом не догоняем: проход в :12 после окна бота не нужен.
            last_slot = slot
            time.sleep(20)
            continue
        # До :58 текущего часа: проход в :52 не должен залезать в окно
        # ночного бота, которое начинается в :59:30.
        until = now.replace(minute=58, second=0, microsecond=0)
        log(f"=== pass (until {until:%H:%M}) ===")
        try:
            subprocess.run(
                [sys.executable, "-u", "tasks.py",
                 "--until", until.strftime("%H:%M"), "--max-tasks", "0"],
                cwd=str(BASE), timeout=1500,
            )
        except subprocess.TimeoutExpired:
            log("tasks.py не успел за 25 минут — пропускаю проход")
        last_slot = slot
        time.sleep(65)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("=== active_loop stop ===")
