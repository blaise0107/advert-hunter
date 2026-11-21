"""Ферма AdvertApp: задания на всех подключённых телефонах.

Один процесс-супервизор:
  * перечисляет онлайн-устройства через `adb devices -l`;
  * приводит каждому экран к 1080×2412 / 408 (core/display.py);
  * запускает по одному процессу resume_after_works.py на устройство;
  * держит не больше max_parallel работников одновременно;
  * после круга ставит устройство в очередь и берёт следующее;
  * пишет farm_status.json для панели.

Запуск:
    python farm.py
    python farm.py --max-parallel 2 --round-minutes 15
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOGS_DIR = BASE / "logs"
LOGS_DIR.mkdir(exist_ok=True)
_PY = os.environ.get("ADVERT_PYTHON")
PYTHON = Path(_PY) if _PY else Path(r"C:\Users\RZE\AppData\Local\Programs\Python\Python314\python.exe")
if not PYTHON.exists():
    PYTHON = Path(sys.executable)
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
STATUS_PATH = BASE / "farm_status.json"
LOG_PATH = LOGS_DIR / "farm.log"
STOP_PATH = BASE / "hunter.stop"

sys.path.insert(0, str(BASE))
from core import devices  # noqa: E402
from core import display  # noqa: E402
from core.config import load_settings  # noqa: E402

SERIAL = ""
STARTED_AT = datetime.now()
STOP = False


def log(message: str) -> None:
    line = f"{datetime.now():%H:%M:%S} {message}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def slug(serial: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", serial) or "device"


def run_dir(serial: str) -> Path:
    path = BASE / "runs" / slug(serial)
    path.mkdir(parents=True, exist_ok=True)
    return path


def tail_text(path: Path, limit: int = 4096) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - limit))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def last_line(path: Path) -> str:
    text = tail_text(path)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:180] if lines else ""


def phase(serial: str) -> str:
    text = tail_text(run_dir(serial) / "resume.log")
    text += "\n" + tail_text(run_dir(serial) / "hunter2.log")
    for line in reversed(text.splitlines()):
        low = line.lower()
        if not low.strip():
            continue
        if "цикл: «активные»" in low or "tasks.py" in low:
            return "Активные"
        if "цикл: забор «новые»" in low or "hunter2 start" in low:
            return "Новые"
        if "resume start" in low:
            return "старт"
        if "resume stop" in low:
            return "круг завершён"
    return "—"


def _atomic_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except OSError:
        pass


def _kill_tree(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=20,
        )
    except Exception:
        pass


def settings_values() -> dict:
    settings = load_settings() or {}
    farm = settings.get("farm", {}) if isinstance(settings, dict) else {}
    try:
        max_parallel = max(1, min(8, int(farm.get("max_parallel", 2))))
    except (TypeError, ValueError):
        max_parallel = 2
    try:
        round_minutes = max(4, min(180, int(farm.get("round_minutes", 15))))
    except (TypeError, ValueError):
        round_minutes = 15
    try:
        techwait = max(0, int(farm.get("techwait_seconds", 120)))
    except (TypeError, ValueError):
        techwait = 120
    try:
        restart_delay = max(5, int(farm.get("restart_delay_seconds", 30)))
    except (TypeError, ValueError):
        restart_delay = 30
    disabled = farm.get("disabled") or []
    if not isinstance(disabled, list):
        disabled = []
    return {
        "max_parallel": max_parallel,
        "round_minutes": round_minutes,
        "techwait": techwait,
        "restart_delay": restart_delay,
        "disabled": {str(item) for item in disabled},
        "deadline": str(settings.get("deadline", "08:30")) if isinstance(settings, dict) else "08:30",
    }


class Farm:
    def __init__(self, args):
        values = settings_values()
        self.max_parallel = args.max_parallel or values["max_parallel"]
        self.round_minutes = args.round_minutes or values["round_minutes"]
        self.techwait = values["techwait"]
        self.restart_delay = values["restart_delay"]
        self.disabled = values["disabled"]
        self.active_minutes = max(3, int(round(self.round_minutes * 0.6)))
        self.new_minutes = max(2, self.round_minutes - self.active_minutes)
        self.deadline = self._deadline(args.until or values["deadline"])
        self.started = datetime.now()
        self.active: dict[str, dict] = {}
        self.queue: list[str] = []
        self.known: set[str] = set()
        self.last_started: dict[str, float] = {}
        self.next_attempt: dict[str, float] = {}
        self.online: dict[str, object] = {}
        # Защита от ложного «отключилось»: adb devices иногда разово
        # возвращает пустой список. Останавливаем воркера только после
        # двух подряд провалов discovery, а не по одному опросу.
        self._discover_misses = 0
        # Per-serial счётчик пропаж: одно устройство может "чихнуть" в adb,
        # пока другое видно. Не останавливаем воркера после одной пропажи.
        self._missing_serial_counts = {}

    @staticmethod
    def _deadline(text: str) -> datetime:
        try:
            hh, mm = (int(part) for part in text.split(":"))
        except ValueError:
            hh, mm = 8, 30
        now = datetime.now()
        deadline = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if deadline <= now:
            deadline += timedelta(days=1)
        return deadline

    def discover(self) -> list:
        try:
            items = devices.list_devices()
        except Exception as error:
            log(f"adb devices: {error!r}")
            self._discover_misses += 1
            if self._discover_misses >= 2:
                # Два подряд провала — похоже, adb реально недоступен.
                return []
            # Один разовый сбой: возвращаем последний известный список,
            # чтобы не убивать воркеров на простом чихе adb.
            return [item for item in self.online.values()
                    if item.online and item.serial not in self.disabled]
        seen = {item.serial for item in items if item.online}
        # Per-serial защита от одиночного "чиха" adb: если известное устройство
        # пропало из adb впервые — держим его в списке ещё один цикл. Только
        # две подряд пропажи считаются реальным отключением (иначе воркер
        # останавливался после первого же partial-loss, пока другое устройство
        # оставалось видимым — баг приводил к простою Samsung по 20+ минут).
        kept = []
        for serial, item in list(self.online.items()):
            if serial in seen:
                self._missing_serial_counts.pop(serial, None)
                continue
            misses = self._missing_serial_counts.get(serial, 0) + 1
            self._missing_serial_counts[serial] = misses
            if misses < 2 and serial not in self.disabled:
                kept.append(item)
        if items or self._discover_misses == 0:
            self._discover_misses = 0
        merged = list(items) + [item for item in kept
                                if item.serial not in seen]
        self.online = {item.serial: item for item in merged if item.online}
        return [item for item in merged if item.online and item.serial not in self.disabled]

    def start_worker(self, serial: str, slot: int) -> None:
        rd = run_dir(serial)
        log_path = rd / "resume.log"
        command = [
            str(PYTHON), "-u", "strategy_engine.py",
            "--serial", serial, "--slot", str(slot),
        ]
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            fh = open(log_path, "a", encoding="utf-8", errors="replace")
            fh.write(f"\n=== farm start {datetime.now():%d.%m %H:%M:%S} slot={slot} ===\n")
            fh.flush()
            proc = subprocess.Popen(
                command, cwd=str(BASE), env=env,
                stdout=fh, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
            )
        except Exception as error:
            log(f"{serial}: не удалось запустить работника: {error!r}")
            try:
                fh.close()
            except Exception:
                pass
            return
        self.active[serial] = {
            "proc": proc, "fh": fh, "slot": slot,
            "started": time.time(), "started_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.last_started[serial] = time.time()
        log(f"старт {serial} (pid {proc.pid}, слот {slot})")

    def stop_worker(self, serial: str, restore: bool = False) -> None:
        info = self.active.pop(serial, None)
        if info is None:
            return
        proc = info["proc"]
        if proc.poll() is None:
            _kill_tree(proc.pid)
        try:
            info["fh"].close()
        except Exception:
            pass
        if restore:
            try:
                display.restore(serial, str(run_dir(serial)))
                log(f"{serial}: экран возвращён к оригиналу")
            except Exception as error:
                log(f"{serial}: restore display: {error!r}")
        log(f"стоп {serial} (pid {proc.pid})")

    def stop_all(self) -> None:
        for serial in list(self.active):
            self.stop_worker(serial, restore=True)

    def write_status(self, running: bool = True) -> None:
        items = []
        for serial, item in self.online.items():
            if serial in self.active:
                state = "работает"
            elif serial in self.queue:
                state = "ждёт"
            else:
                state = "онлайн"
            info = self.active.get(serial) or {}
            items.append({
                "serial": serial,
                "model": getattr(item, "name", ""),
                "state": state,
                "slot": info.get("slot"),
                "pid": info.get("proc").pid if info.get("proc") else None,
                "started": info.get("started_at"),
                "phase": phase(serial) if info else "—",
                "last": last_line(run_dir(serial) / "hunter2.log")
                        or last_line(run_dir(serial) / "resume.log"),
            })
        # Офлайн-устройства, о которых знаем, тоже показываем.
        for serial in sorted(self.known - set(self.online)):
            items.append({
                "serial": serial, "model": "", "state": "офлайн",
                "slot": None, "pid": None, "started": None,
                "phase": "—", "last": last_line(run_dir(serial) / "resume.log"),
            })
        data = {
            "updated": datetime.now().isoformat(timespec="seconds"),
            "started": self.started.isoformat(timespec="seconds"),
            "farm_pid": os.getpid(),
            "running": running,
            "max_parallel": self.max_parallel,
            "round_minutes": self.round_minutes,
            "active_minutes": self.active_minutes,
            "new_minutes": self.new_minutes,
            "deadline": self.deadline.isoformat(timespec="seconds"),
            "active": len(self.active),
            "queue": list(self.queue),
            "devices": items,
        }
        _atomic_json(STATUS_PATH, data)

    def run(self) -> int:
        log(f"=== farm start: до {self.deadline:%d.%m %H:%M}, "
            f"параллельно {self.max_parallel}, круг {self.round_minutes} мин "
            f"({self.active_minutes}+{self.new_minutes}) ===")
        if STOP_PATH.exists():
            try:
                STOP_PATH.unlink()
            except OSError:
                pass
        slot = 0
        try:
            while datetime.now() < self.deadline:
                online_items = self.discover()
                online_serials = {item.serial for item in online_items}
                # Новые устройства — в очередь.
                for item in online_items:
                    if item.serial not in self.known:
                        self.known.add(item.serial)
                        self.queue.append(item.serial)
                        log(f"найдено устройство {item.serial} ({item.name})")
                # Пропавшие устройства убираем.
                for serial in list(self.active):
                    if serial not in online_serials:
                        log(f"{serial}: отключилось — останавливаю работника")
                        self.stop_worker(serial, restore=False)
                self.queue = [s for s in self.queue if s in online_serials]
                # Запускаем, пока есть слоты.
                while len(self.active) < self.max_parallel:
                    now = time.time()
                    candidates = [
                        s for s in self.queue
                        if s not in self.active and now >= self.next_attempt.get(s, 0)
                    ]
                    if not candidates:
                        break
                    serial = min(candidates, key=lambda s: self.last_started.get(s, 0))
                    self.queue.remove(serial)
                    self.start_worker(serial, slot)
                    slot += 1
                # Проверяем завершившихся.
                now = time.time()
                for serial, info in list(self.active.items()):
                    code = info["proc"].poll()
                    if code is None:
                        continue
                    self.active.pop(serial, None)
                    try:
                        info["fh"].close()
                    except Exception:
                        pass
                    delay = self.restart_delay
                    if code == 2:
                        delay = max(delay, 300)
                    elif code != 0:
                        delay = max(delay, 120)
                    log(f"круг {serial} завершён (код {code}); следующий через {delay} с")
                    if serial in online_serials:
                        if serial not in self.queue:
                            self.queue.append(serial)
                        self.next_attempt[serial] = now + delay
                self.write_status(running=True)
                if STOP_PATH.exists():
                    log("найден hunter.stop — останавливаю ферму")
                    break
                time.sleep(2)
        except KeyboardInterrupt:
            log("Ctrl+C — останавливаю ферму")
        finally:
            self.stop_all()
            # Для устройств в очереди тоже возвращаем экран.
            for serial in list(self.known):
                if serial in self.online:
                    try:
                        display.restore(serial, str(run_dir(serial)))
                    except Exception:
                        pass
            self.write_status(running=False)
            log("=== farm stop ===")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ферма AdvertApp на все телефоны")
    parser.add_argument("--max-parallel", type=int, help="Сколько телефонов вести одновременно")
    parser.add_argument("--round-minutes", type=int, help="Длительность круга на телефон")
    parser.add_argument("--until", help="Время остановки HH:MM (для теста)")
    args = parser.parse_args()
    if args.max_parallel is not None and args.max_parallel < 1:
        parser.error("--max-parallel должен быть >= 1")
    if args.round_minutes is not None and args.round_minutes < 4:
        parser.error("--round-minutes должен быть >= 4")
    return Farm(args).run()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        log(f"farm fatal: {error!r}")
        raise
