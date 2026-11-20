"""Ночная серия: короткие прогоны каждый час и длинный забор с 23:59:30.

Запуск (в фоне, до 02:00):
    python night.py

Логика:
  * 19:00 … 23:00 — по 8 минут каждый час (днём задания тоже бывают, но мало);
  * 23:59:30 … 01:59:30 — непрерывный забор, пока RU-задания не пропадут
    на 20 минут (--idle-stop 20). Ограничения «00:06» больше нет.
"""
import os
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hunter2 as h


def run_slot(**kwargs):
    """Один прогон бота: сбой не должен убивать весь ночной план."""
    try:
        h.run(**kwargs)
    except Exception:
        print("=== slot failed ===", flush=True)
        traceback.print_exc()
        time.sleep(5)

if os.path.exists(h.STOP_PATH):
    os.remove(h.STOP_PATH)

now = datetime.now()

# 1) Часовые прогоны до полуночи.
for hour in range(now.hour + 1, 24):
    mark = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    start = max(datetime.now(), mark - timedelta(seconds=30))
    end = mark + timedelta(minutes=8)
    if end <= datetime.now():
        continue
    print(f"=== hourly {start:%H:%M:%S}-{end:%H:%M:%S}", flush=True)
    run_slot(start_datetime=start, deadline_datetime=end, refresh_seconds=2.0)

# 2) Ночной шквал: забираем, пока задания не кончатся.
start = datetime.now().replace(hour=23, minute=59, second=30, microsecond=0)
if start < datetime.now():
    start = datetime.now()
end = start + timedelta(hours=2)
print(f"=== night {start:%H:%M:%S}-{end:%H:%M:%S}", flush=True)
run_slot(start_datetime=start, deadline_datetime=end, refresh_seconds=1.0, idle_stop=20)
