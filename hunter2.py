"""AdvertApp RU-order hunter v2 (OCR).

Логика:
  screencap -> Windows OCR (ru) -> заголовки карточек -> отсев иностранных и Украина
  и Samsung-only -> страница задания -> кнопка нужного магазина.
  RuStore и Huawei разрешены; обновление — только перезаходом в «Новые».

Запуск:
    python hunter2.py                    # старт сейчас, 10 минут
    python hunter2.py 17:59:30 18:06:00  # старт/конец по времени
"""
import argparse
import atexit
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta

from PIL import Image
import numpy as np

# Консоль Windows в cp1251 не умеет стрелки в логах — принудительно UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ADB = os.environ.get("ADB_EXE", r"C:\Users\RZE\AppData\Local\Android\Sdk\platform-tools\adb.exe")
# Устройство по умолчанию: из окружения HUNTER_SERIAL, иначе автодетект,
# если подключено ровно одно. Ферма всё равно вызывает configure_device(serial)
# для каждого воркера, но одиночный запуск теперь не привязан к «fab1701».
def _default_serial():
    env = os.environ.get("HUNTER_SERIAL", "").strip()
    if env:
        return env
    try:
        out = subprocess.run([ADB, "devices"], capture_output=True,
                             timeout=8).stdout.decode("utf-8", "ignore")
        serials = [line.split()[0] for line in out.splitlines()
                   if line.strip().endswith("\tdevice")]
        if len(serials) == 1:
            return serials[0]
    except Exception:
        pass
    return ""


SERIAL = _default_serial()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
try:
    from core.config import load_settings as _load_panel_settings
except Exception:  # бот должен работать и без нового пакета core
    def _load_panel_settings():
        return {}
try:
    from core import blacklist as _order_blacklist
except Exception:  # без модуля ЧС бот работает как раньше
    _order_blacklist = None
OCR = os.path.join(BASE_DIR, "ocr.ps1")
# Движок OCR выбирается ключом panel_settings.json → ocr.engine = "rapid".
# По умолчанию остаётся Windows OCR (ocr.ps1), то есть поведение фермы не меняется.
OCR_RAPID_PY = os.path.join(BASE_DIR, ".venv-ocr", "Scripts", "python.exe")
OCR_RAPID_SCRIPT = os.path.join(BASE_DIR, "ocr_rapid_server.py")
PWSH = os.environ.get("PWSH_EXE", r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
LOG_PATH = os.path.join(LOGS_DIR, "hunter2.log")
RESULTS_PATH = os.path.join(BASE_DIR, "results.jsonl")
# Маркер «из модалки взято новое задание»: resume сразу запускает «Активные»,
# чтобы выполнить его, не дожидаясь конца фазы «Новые».
OFFER_PATH = os.path.join(BASE_DIR, "offer.pending")
# Заказы, взятые из «Новых»: tasks.py ставит их первыми в «Активных».
NEW_TAKEN_PATH = os.path.join(BASE_DIR, "new_taken.json")
# Пакет, который последним открывали по напоминанию: tasks.py использует его,
# чтобы засчитать выполнение заказа, а не запускать его заново.
LAST_HELD_PKG = None
# Пакет, удалённый по инструкции «удалите приложение»: его нужно поставить снова.
LAST_UNINSTALLED_PKG = None
TMP = os.path.join(BASE_DIR, "frames")
os.makedirs(TMP, exist_ok=True)
# Каталог конкретного устройства (runs/<serial>). До configure_device()
# совпадает с корнем проекта — так старый одиночный запуск работает как раньше.
RUN_DIR = BASE_DIR
DEVICE_SLOT = None
GLOBAL_STOP_PATH = os.path.join(BASE_DIR, "hunter.stop")

_frame_counter = 0


def frame_path(name):
    """Уникальное имя кадра: файлы не перезаписываются, пока их читает OCR."""
    global _frame_counter
    _frame_counter += 1
    # Долгие проходы «Активных» идут в отдельном процессе tasks.py и тоже
    # снимают кадры. Чистим папку по ходу, иначе она растёт сотнями файлов.
    if _frame_counter % 200 == 0:
        try:
            cfg = (_load_panel_settings() or {}).get("maintenance") or {}
            prune_frames(
                int(cfg.get("frames_max_age_minutes", 90)),
                int(cfg.get("frames_max_files", 600)),
                int(cfg.get("frames_max_mb", 250)),
            )
        except Exception:
            pass
    stem, ext = os.path.splitext(name)
    return os.path.join(TMP, f"{stem}_{_frame_counter:06d}{ext or '.png'}")


def prune_frames(max_age_minutes=90, max_files=600, max_mb=250):
    """Чистка кадров: по возрасту, количеству и суммарному размеру.

    Возвращает число удалённых файлов. Кадры нужны только на время OCR,
    поэтому старые снимки можно удалять без потери данных.
    """
    cutoff = time.time() - max_age_minutes * 60
    files = []
    removed = 0
    try:
        names = os.listdir(TMP)
    except OSError:
        return 0
    for name in names:
        path = os.path.join(TMP, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path):
            continue
        if stat.st_mtime < cutoff:
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
            continue
        files.append((stat.st_mtime, stat.st_size, path))
    files.sort()
    total = sum(size for _mtime, size, _path in files)
    limit_bytes = max(1, int(max_mb)) * 1024 * 1024
    while files and (len(files) > max_files or total > limit_bytes):
        _mtime, size, path = files.pop(0)
        try:
            os.remove(path)
            removed += 1
            total -= size
        except OSError:
            pass
    if removed:
        log(f"prune frames: удалено {removed}, осталось {len(files)}")
    return removed

APP = "com.advert.mobileearnings.app"
# Пакеты AdvertApp: старая сборка и новая (TeamDev). На телефоне бот работает
# с той, которая установлена и включена: на Samsung старая отключена,
# а на Realme доступны обе.
APP_PACKAGES = (
    "com.advert.mobileearnings.app",
    "com.teamdev.reward",
    "ru.teamdev.reward",
)
APP_ACTIVITIES = {
    "com.advert.mobileearnings.app": "com.advert.mobileearnings.app/.MainActivity",
    "com.teamdev.reward": "com.teamdev.reward/last.order.app.NativeMainActivity",
    "ru.teamdev.reward": "ru.teamdev.reward/last.order.app.NativeMainActivity",
}
_ACTIVE_APP = None
# Сколько предложений бот может принять за один прогон. Пока задания нечем
# выполнять, лимит не даёт накопить очередь «Открыть на 2 дня».
_OFFERS_TAKEN = 0


def offer_limit():
    """Лимит предложений, берёмых из модалки за один прогон (0 — без лимита)."""
    try:
        new_cfg = (_load_panel_settings() or {}).get("new") or {}
        return max(0, int(new_cfg.get("max_offers_per_run", 3)))
    except Exception:
        return 3
SCREEN_W, SCREEN_H = 1080, 2412
CONTENT_BOTTOM = 2230
REFRESH_SECONDS = 0.5
MARKETS = {
    "com.android.vending": "Google Play",
    "ru.vk.store": "RuStore",
    "com.huawei.appmarket": "Huawei AppGallery",
    "com.xiaomi.mipicks": "Xiaomi GetApps",
}

MENU_NEW_TILE = (223, 690)
BACK_BTN = (75, 2265)
MODAL_OK = (540, 1240)
MODAL_OUTSIDE = (100, 700)
GPLAY_BTN = (540, 775)
SKIP_REC = (540, 1313)   # красная «Пропустить» в рекомендации

GREEN_MIN = 150
CARD_X0, CARD_X1 = 820, 1040


def log(msg):
    line = f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def adb(*args, timeout=25):
    return subprocess.run([ADB, "-s", SERIAL, *args], capture_output=True, timeout=timeout)


def app_packages_enabled():
    """Пакеты AdvertApp, установленные и включённые на этом телефоне."""
    def packages(command):
        out = adb("shell", command).stdout.decode("utf-8", "ignore")
        found = set()
        for line in out.splitlines():
            if line.startswith("package:"):
                found.add(line.split(":", 1)[1].strip())
        return found
    installed = packages("pm list packages")
    disabled = packages("pm list packages -d")
    return [pkg for pkg in APP_PACKAGES if pkg in installed and pkg not in disabled]


def main_activity(pkg):
    """Главная Activity пакета: по ней запускаем AdvertApp."""
    out = adb("shell", f"cmd package resolve-activity --brief {pkg}").stdout.decode("utf-8", "ignore")
    for line in out.splitlines():
        line = line.strip()
        if "/" in line and not line.lower().startswith("no activity"):
            return line
    return APP_ACTIVITIES.get(pkg, f"{pkg}/.MainActivity")


def _app_cache_path():
    return os.path.join(RUN_DIR, "app_package.txt")


def active_app():
    """Пакет AdvertApp, с которым работаем на этом телефоне.

    Порядок выбора: явная настройка панели → пакет, запомненный для
    устройства → тот, что сейчас на экране → первый включённый.
    """
    global _ACTIVE_APP
    if _ACTIVE_APP:
        return _ACTIVE_APP
    try:
        configured = str((_load_panel_settings() or {}).get("app_package", "") or "").strip()
    except Exception:
        configured = ""
    enabled = app_packages_enabled()
    if configured in enabled:
        _ACTIVE_APP = configured
    else:
        cached = ""
        try:
            with open(_app_cache_path(), encoding="utf-8") as f:
                cached = f.read().strip()
        except OSError:
            cached = ""
        if cached in enabled:
            _ACTIVE_APP = cached
        else:
            current = focus()
            _ACTIVE_APP = (next((pkg for pkg in enabled if pkg in current), None)
                           or (enabled[0] if enabled else APP))
    try:
        with open(_app_cache_path(), "w", encoding="utf-8") as f:
            f.write(_ACTIVE_APP + "\n")
    except OSError:
        pass
    log(f"пакет AdvertApp: {_ACTIVE_APP}")
    return _ACTIVE_APP


def app_in_focus(current=None):
    """На экране сейчас AdvertApp (любой из поддерживаемых пакетов)."""
    current = current if current is not None else focus()
    return any(pkg in current for pkg in APP_PACKAGES)


def start_app():
    """Запускает AdvertApp и возвращает команду запуска для лога."""
    pkg = active_app()
    activity = main_activity(pkg)
    # Системный диалог «Application Error: <pkg>» от упавшего стороннего
    # приложения удерживает фокус: am start не снимает его, и воркер
    # застревает на 5+ минут с «фокус не AdvertApp». Закрываем диалог
    # аппаратной BACK и останавливаем виновника, чтобы он не поднялся снова.
    try:
        current = focus()
        if "Application Error" in current:
            match = re.search(r"Application Error:\s*([^\s}]+)", current)
            bad_pkg = match.group(1) if match else None
            adb("shell", "input keyevent 4")
            if bad_pkg and bad_pkg not in APP_PACKAGES:
                adb("shell", f"am force-stop {bad_pkg}")
            log(f"закрыт системный диалог Application Error: {bad_pkg}")
            time.sleep(0.8)
    except Exception:
        pass
    adb("shell", "am start -n " + activity)
    return activity


def tap(x, y):
    if not app_in_focus():
        raise RuntimeError("Остановлено: AdvertApp не на переднем плане; посторонний экран не нажимаю")
    adb("shell", f"input tap {int(x)} {int(y)}")
    log(f"tap {int(x)},{int(y)}")


def tap_any(x, y):
    """Тап без проверки фокуса.

    Нужен в магазинах (Google Play, RuStore) и других сторонних окнах:
    там в фокусе не AdvertApp, но нажать надо именно там.
    """
    adb("shell", f"input tap {int(x)} {int(y)}")
    log(f"tap(any) {int(x)},{int(y)}")


def key_back():
    current = focus()
    if not app_in_focus(current) and not any(package in current for package in MARKETS):
        raise RuntimeError("BACK отменён: на экране постороннее приложение")
    adb("shell", "input keyevent 4")
    log("BACK")


def keyboard_shown():
    """Открыта системная клавиатура (ВАЖНО на Samsung: тап по BACK_BTN
    попадает по клавишам IME и просто печатает мусор в поле ввода)."""
    try:
        out = adb("shell", "dumpsys input_method | grep -i mInputShown", timeout=8)
    except (OSError, subprocess.SubprocessError):
        return False
    text = out.stdout.decode("utf-8", "ignore")
    return any("minputshown=true" in line.lower().replace(" ", "") for line in text.splitlines())


def dismiss_keyboard():
    """Скрыть клавиатуру аппаратным BACK, а не тапом по фиксированным
    координатам навигационной панели."""
    if not keyboard_shown():
        return False
    try:
        adb("shell", "input keyevent 4")
        log("клавиатура скрыта (KEYCODE_BACK)")
        time.sleep(0.4)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def swipe(x1, y1, x2, y2, ms=300):
    if not app_in_focus():
        raise RuntimeError("Прокрутка отменена: AdvertApp не на переднем плане")
    adb("shell", f"input swipe {x1} {y1} {x2} {y2} {ms}")


def focus():
    out = adb("shell", "dumpsys window | grep mCurrentFocus").stdout.decode("utf-8", "ignore")
    return out.strip()


def shot(path):
    """Снимок экрана. Пишем во временный файл с уникальным именем: повторное
    открытие одного и того же файла на Windows иногда даёт EINVAL, а кадр
    терять нельзя — без него не работает детектор модалок."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for attempt in range(3):
        tmp = f"{path}.{os.getpid()}_{attempt}.tmp"
        try:
            result = adb("exec-out", "screencap", "-p", timeout=15)
            if result.returncode or len(result.stdout) < 5000:
                log("Не удалось снять экран; старый кадр не используется")
                time.sleep(0.2)
                continue
            with open(tmp, "wb") as f:
                f.write(result.stdout)
            with Image.open(tmp) as image:
                if image.size != (SCREEN_W, SCREEN_H):
                    raise RuntimeError(f"Экран {image.size}, ожидался 1080×2412. Геометрия не меняется")
                frame = np.array(image.convert("RGB")).astype(np.int16)
            try:
                os.replace(tmp, path)
            except OSError:
                # Кадр уже прочитан в память — имя файла не критично.
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            return frame
        except (subprocess.TimeoutExpired, OSError) as exc:
            log(f"Ошибка снимка экрана ({type(exc).__name__}, попытка {attempt + 1}): {exc}")
            try:
                os.remove(tmp)
            except OSError:
                pass
            time.sleep(0.2)
    return None


def save_png(array, path):
    """Сохранение кадра без падения на EINVAL при перезаписи."""
    for attempt in range(3):
        try:
            Image.fromarray(array.astype(np.uint8)).save(path)
            return True
        except OSError as exc:
            log(f"Ошибка записи {os.path.basename(path)} ({type(exc).__name__}): {exc}")
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            time.sleep(0.1)
    return False


def _ocr_engine():
    """Какой OCR-сервер использовать: 'rapid' (PaddleOCR, кириллица) или 'windows'.

    Настройка читается при каждом запуске сервера, поэтому после правки
    panel_settings.json достаточно перезапустить процесс фермы. Если интерпретатор
    или скрипт RapidOCR отсутствуют — безопасно остаёмся на Windows OCR.
    """
    try:
        cfg = (_load_panel_settings() or {}).get("ocr") or {}
        engine = str(cfg.get("engine", "")).strip().lower()
    except Exception:
        engine = ""
    if engine in ("rapid", "rapidocr", "rapid_ocr"):
        if os.path.exists(OCR_RAPID_PY) and os.path.exists(OCR_RAPID_SCRIPT):
            return "rapid"
        log("ocr.engine=rapid, но .venv-ocr/ocr_rapid_server.py не найден — использую Windows OCR")
    return "windows"


def _ocr_command():
    """Команда запуска OCR-сервера и таймаут ответа для выбранного движка."""
    if _ocr_engine() == "rapid":
        return [OCR_RAPID_PY, "-u", OCR_RAPID_SCRIPT, "-Server"], 20
    return ([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", OCR, "-Server"], 10)


class OCRServer:
    """Постоянный OCR-сервер (RapidOCR или Windows); ответ ограничен таймаутом."""

    def __init__(self):
        self.process = None
        self.answers = None
        self.engine = ""
        self.timeout = 10

    def start(self):
        self.close()
        command, self.timeout = _ocr_command()
        self.engine = "rapid" if command[0] == OCR_RAPID_PY else "windows"
        log(f"OCR-сервер: {self.engine} ({os.path.basename(command[0])})")
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1,
        )
        answers = queue.Queue()

        def read_answers():
            try:
                for line in process.stdout:
                    answers.put(line)
            finally:
                answers.put(None)

        threading.Thread(target=read_answers, name=f"{self.engine}-ocr", daemon=True).start()
        self.process, self.answers = process, answers

    def recognize(self, path):
        if self.process is None or self.process.poll() is not None:
            self.start()
        self.process.stdin.write(json.dumps({"path": os.path.abspath(path)}, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        try:
            answer = self.answers.get(timeout=self.timeout)
        except queue.Empty as exc:
            self.close()
            raise TimeoutError(f"OCR ({self.engine}) не ответил за {self.timeout} с") from exc
        if answer is None:
            self.close()
            raise RuntimeError(f"OCR ({self.engine}) завершился до ответа")
        data = json.loads(answer)
        if isinstance(data, dict) and "error" in data:
            raise RuntimeError(data["error"])
        return data if isinstance(data, list) else ([data] if data else [])

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        try:
            process.stdin.close()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            process.wait(timeout=2)
        finally:
            process.stdout.close()


_ocr_server = OCRServer()
atexit.register(_ocr_server.close)


def ocr(path):
    try:
        return _ocr_server.recognize(path)
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        log(f"Ошибка OCR: {exc}")
        return []


def green_mask(a):
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    return (g > GREEN_MIN) & (r < 130) & (b < 130) & ((g - r) > 70) & ((g - b) > 70)


def find_rings(a):
    m = green_mask(a)
    ys, xs = np.where(m)
    sel = (xs > CARD_X0) & (xs <= CARD_X1) & (ys > 300)
    ys, xs = ys[sel], xs[sel]
    if not len(ys):
        return []
    yy = np.unique(ys)
    groups = []
    s = prev = yy[0]
    for y in yy[1:]:
        if y - prev > 12:
            groups.append((s, prev))
            s = y
        prev = y
    groups.append((s, prev))
    rings = []
    for y0, y1 in groups:
        mm = (ys >= y0) & (ys <= y1)
        if mm.sum() < 300:
            continue
        x0, x1 = int(xs[mm].min()), int(xs[mm].max())
        if not (150 <= x1 - x0 <= 220):
            continue
        rings.append({"cy": (y0 + y1) // 2, "top": (y0 + y1) // 2 - 135,
                      "bottom": y1 + 135})
    return rings


def has_gplay_button(a):
    m = green_mask(a)
    for y in (710, 740, 780, 820):
        row = m[y]
        if row.sum() > 800:
            xs = np.where(row)[0]
            if xs.min() < 120 and xs.max() > 950:
                return True
    return False


def has_red_button(a):
    if a is None:
        return False
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    red = (r > 170) & (g < 100) & (b < 100)
    # широкая красная полоса в центре (кнопка «Пропустить»)
    for y in range(1250, 1400, 20):
        if red[y, 300:780].sum() > 300:
            return True
    return False


def is_dim(a):
    pts = [(20, 600), (20, 1500), (1060, 900), (1060, 1800)]
    edges_dim = sum(1 for x, y in pts if a[y, x].max() < 200) >= 3
    center = a[600:1750:8, 150:930:8]
    white_panel = (center.min(axis=2) > 240).mean() > 0.2
    return edges_dim and white_panel


def active_header(a):
    """Розовый заголовок «Активных» виден даже под обрезанным текстом."""
    if a is None:
        return False
    band = a[145:210:5, 180:900:10]
    r, g, b = band[:, :, 0], band[:, :, 1], band[:, :, 2]
    return ((r > 170) & (r - g > 50) & (abs(g - b) < 25)).mean() > 0.7


def screen_kind(a):
    if a is None:
        return "none"
    if is_dim(a):
        return "modal"
    if active_header(a):
        return "active"
    if has_gplay_button(a):
        return "detail"
    if find_rings(a):
        return "list"
    return "other"


MENU_WORDS = ("новые", "активные", "деньги", "история", "новости",
              "рефералы", "профиль", "поддержка", "рейтинг")


def find_word(lines, needle):
    """Центр первого слова/строки, содержащей needle (без регистра)."""
    needle = needle.lower()
    for l in lines:
        for w in l.get("words", []):
            if needle in w["t"].lower():
                return (w["x"] + w["w"] // 2, w["y"] + w["h"] // 2)
        if needle in l["text"].lower():
            return (l["x"] + 60, l["y"] + 20)
    return None


def menu_score(lines):
    text = " ".join(l["text"].lower() for l in lines)
    return sum(1 for w in MENU_WORDS if w in text)


def wait_kind(kind, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        p = frame_path("w.png")
        a = shot(p)
        if screen_kind(a) == kind:
            return a
        time.sleep(0.3)
    return None


def find_green_bar(a, y0=900, y1=1750, min_width=400):
    """Самая широкая зелёная горизонтальная полоса (кнопка в модалке)."""
    if a is None:
        return None
    m = green_mask(a)
    best = None
    for y in range(y0, min(y1, a.shape[0]), 10):
        row = m[y]
        if row.sum() < min_width:
            continue
        xs = np.where(row)[0]
        x0, x1 = int(xs.min()), int(xs.max())
        if x1 - x0 < min_width:
            continue
        if x0 > 260 or x1 < 820:  # кнопка должна быть примерно по центру
            continue
        width = x1 - x0
        if best is None or width > best[0]:
            best = (width, (x0 + x1) // 2, y)
    return best


def close_modal(a, lines=None):
    """Только закрытие: не нажимает «Начать» или произвольную зелёную полосу."""
    if a is None:
        return False
    if not app_in_focus():
        log("close_modal: фокус не в AdvertApp — возвращаюсь в приложение")
        try:
            start_app()
        except Exception:
            pass
        time.sleep(0.5)
        return False
    if has_red_button(a):
        try:
            tap(*SKIP_REC)
        except RuntimeError:
            log("close_modal: тап «Пропустить» не прошёл (фокус ушёл) — возвращаю в приложение")
            try:
                start_app()
            except Exception:
                pass
            return False
        time.sleep(0.4)
        return True
    if lines is None:
        path = frame_path("modal_close.png")
        save_png(a, path)
        lines = modal_lines(a, path)
    for label in ("пропустить", "отмена", "закрыть", "понятно", "ок", "ok"):
        for line in lines:
            for word in line.get("words", []):
                token = norm(word["t"]).strip(" .,!:")
                token = token.replace("о", "o").replace("к", "k") if len(token) == 2 else token
                wanted = label.replace("о", "o").replace("к", "k") if len(label) == 2 else label
                if token == wanted:
                    # Фокус мог уйти между проверкой app_in_focus() и этим
                    # тапом (модалка «смена аккаунта» перехватывает экран).
                    # tap() тогда бросает RuntimeError и роняет воркер —
                    # ловим его и закрываем окно перезапуском приложения.
                    try:
                        tap(word["x"] + word["w"] // 2, word["y"] + word["h"] // 2)
                    except RuntimeError:
                        log("close_modal: тап не прошёл (фокус ушёл) — перезапускаю AdvertApp")
                        try:
                            restart_app(datetime.now() + timedelta(seconds=90))
                        except Exception:
                            try:
                                start_app()
                            except Exception:
                                pass
                        return False
                    time.sleep(0.2)
                    return True
    # Неизвестное окно AdvertApp (реклама/«смена аккаунта») часто не имеет
    # текстовой кнопки: BACK его не закрывает, а тап по ✕/вне окна только
    # сдвигает экран и залипает ещё сильнее (по наблюдению пользователя).
    # Надёжное лечение — принудительный перезапуск AdvertApp.
    log("Неизвестное окно не закрывается — перезапускаю AdvertApp")
    try:
        restart_app(datetime.now() + timedelta(seconds=90))
    except Exception as exc:
        log(f"close_modal: перезапуск не удался ({exc!r})")
        try:
            start_app()
        except Exception:
            pass
    return False


def read_screen(name="c.png", check_focus=True, require_menu=False):
    """Снимок + OCR. По умолчанию НЕ проверяет фокус — это нужно только для
    действий, чтобы не нажимать, когда телефон на экране пользователя.
    require_menu: требует, чтобы в OCR-тексте было меню — используется на
    старте, когда приложение может быть на splash.
    Каждый кадр пишется в новый файл: OCR-сервер читает его асинхронно, и
    перезапись того же имени на Windows даёт EINVAL."""
    path = frame_path(name)
    a = shot(path)
    if a is None:
        return "none", None, path, []
    kind = screen_kind(a)
    lines = ocr(path)
    # Главное меню AdvertApp иногда даёт ложное is_dim() → kind == "modal".
    # Проверяем меню ПЕРВЫМ, независимо от kind, иначе оно уходит в
    # handle_dialog и бесконечно циклит BACK (09:09 на 238a).
    if menu_score(lines) >= 3 and not is_active_list(lines, a):
        return "menu", a, path, lines
    if kind != "modal":
        if is_active_list(lines, a):
            kind = "active"
        elif looks_like_order_detail(lines):
            # Розовый заголовок страницы одного заказа совпадает по цвету с
            # заголовком списка «Активные»: screen_kind() ошибочно даёт
            # "active", из-за чего страница заказа считалась не открывшейся.
            kind = "detail"
        elif menu_score(lines) >= 3:
            kind = "menu"
        elif "страница заказа" in norm(" ".join(line["text"] for line in lines)):
            kind = "detail"
        elif kind in ("other", "detail"):
            heads = [row for row in merge_rows(lines) if is_order_header(row)]
            if kind == "detail":
                # Страница заказа часто содержит заголовок и в шапке, и в
                # карточке — это не список. Не понижаем detail в list.
                pass
            elif heads:
                rings = find_rings(a) if a is not None else []
                if len(heads) >= 2 and rings:
                    kind = "list"
                elif len(heads) == 1 and (has_gplay_button(a) or any(
                        any(w in norm(row.get("text", "")) for w in
                            ("найти приложение", "открыть приложение", "открыть на", "загрузить", "скачать"))
                        for row in merge_rows(lines))):
                    kind = "detail"
                elif len(heads) >= 2 and not rings:
                    # Несколько заголовков без колец/признаков списка на "other"
                    # чаще признак шумного OCR на странице заказа — не форсим list.
                    if has_gplay_button(a) or any(
                            any(w in norm(row.get("text", "")) for w in
                                ("найти приложение", "открыть приложение", "открыть на", "загрузить", "скачать"))
                            for row in merge_rows(lines)):
                        kind = "detail"
    return kind, a, path, lines


def reboot_device():
    """Перезагружает телефон и ждёт загрузки.

    Нужна, когда приложение перестало реагировать на тапы: события доходят
    до окна, но Flutter их не обрабатывает. Перезапуск процесса не помогает,
    помогает только перезагрузка.
    """
    global _REBOOTS
    _REBOOTS += 1
    log("перезагружаю телефон: ввод в приложении завис")
    adb("reboot", timeout=30)
    adb("wait-for-device", timeout=300)
    for _ in range(60):
        out = adb("shell", "getprop sys.boot_completed", timeout=20).stdout.decode("utf-8", "ignore")
        if "1" in out:
            log("телефон загружен")
            time.sleep(8)
            prepare_device()
            start_app()
            return True
        time.sleep(3)
    log("телефон не сообщил о загрузке")
    return False


_REBOOTS = 0
_REBOOT_LIMIT = 1


def reboot_used():
    """Перезагружать телефон можно не чаще раза за прогон.

    Иначе при зависшем вводе ферма будет перезагружать аппарат по кругу.
    """
    if _REBOOTS >= _REBOOT_LIMIT:
        log("перезагрузка уже выполнялась в этом прогоне — не повторяю")
        return True
    return False


def screen_signature(kind, lines):
    """Отпечаток экрана: чтобы понять, что картинка вообще не меняется."""
    texts = [str(line.get("text", ""))[:24] for line in lines[:4]]
    return (kind, tuple(texts))


def open_wait_seconds():
    """Сколько ждать открытия пункта меню/задания.

    Сервер AdvertApp часто отвечает по минуте: экран минуту остаётся
    прежним, а потом открывается нужный пункт. Раньше бот ждал 15 с и
    успевал решить, что «не нажалось», — и перезапускал приложение.
    """
    try:
        value = int((_load_panel_settings() or {}).get("new", {}).get("open_wait_seconds", 75))
    except (TypeError, ValueError):
        value = 75
    return max(15, min(300, value))


def open_new(max_tries=40, force_reenter=False, timeout=None):
    """Перезаход засчитывается только после реально увиденного главного меню.

    BACK из детальной страницы иногда возвращает сразу в список (страница была
    поверх списка). Раньше это давало до 14 с простоя: список без меню не
    принимался и бот ничего не нажимал. Теперь разрешён второй BACK, а если
    меню так и не появилось — список принимается как есть.

    После тапа ждём open_wait_seconds(): сервер AdvertApp отвечает медленно,
    и экран может не меняться почти минуту. Перезапускать приложение и
    повторять тап раньше этого времени нельзя — иначе сбиваем загрузку.
    """
    timeout = open_wait_seconds() if timeout is None else timeout
    wait = open_wait_seconds()
    end = time.monotonic() + timeout
    saw_menu = not force_reenter
    backs = 0
    starts = 0
    enter_pending = False
    menu_waits = 0
    last_sig = None
    stuck = 0
    last_tap_at = 0.0
    for _ in range(max_tries):
        if time.monotonic() >= end or stop_requested():
            break
        # Samsung держит IME поверх списка: тап по BACK_BTN в этот момент
        # печатает буквы в поле ввода, и перезаход в «Новые» невозможен.
        if dismiss_keyboard():
            continue
        kind, a, path, lines = read_screen("o.png")
        sig = screen_signature(kind, lines)
        if sig == last_sig:
            stuck += 1
            waited = time.monotonic() - last_tap_at
            if stuck == 4 and waited > wait:
                # Перезапуск приложения при неотвечающем сервере ничего не
                # даёт: он только сбрасывает загрузку и усугубляет простой.
                # Ждём, пока приложение само покажет окно с предложением.
                log("экран не меняется больше минуты — продолжаю ждать")
                time.sleep(10)
                end += 10
                stuck = 0
                last_sig = None
                continue
            elif stuck == 8 and waited > wait:
                # Перезагрузка телефона тут не помогает: события доходят до
                # окна, но у сборки AdvertApp нет View-иерархии, которая
                # обрабатывает касания (SurfaceView рисуется вне View).
                # Поэтому не мучаем аппарат, а ждём: приложение само
                # показывает окно с предложением — его кнопки нажимаются.
                log("ввод в приложении не работает: жду окно с заданием")
                stuck = 0
                last_sig = None
                time.sleep(8)
                end += 8
        else:
            stuck = 0
        last_sig = sig
        if kind == "list" and saw_menu:
            return a, path
        if kind == "menu":
            saw_menu = True
            backs = 0
            if not enter_pending:
                pos = find_word(lines, "новые")
                if pos:
                    tap(*pos)
                    enter_pending = True
                    menu_waits = 0
                    last_tap_at = time.monotonic()
            else:
                # Тап по «Новые» мог быть проглочен всплывающим окном или
                # сервер ещё думает. Повторяем только через долгое ожидание.
                menu_waits += 1
                if menu_waits >= 3 and time.monotonic() - last_tap_at > wait:
                    log("меню не изменилось после тапа — повторяю")
                    enter_pending = False
                    menu_waits = 0
        elif kind == "modal":
            # Всплывающее окно с предложением перекрывает меню: пока оно
            # висит, тап по плитке уходит в никуда. Разбираем окно и
            # повторяем вход в «Новые».
            try:
                handle_dialog("unprompted", a, path, "navigation", navigation=True)
            except Exception as error:  # noqa: BLE001 - окно не должно ронять забор
                log(f"не удалось разобрать окно: {error!r}")
                close_modal(a, modal_lines(a, path))
            enter_pending = False
            menu_waits = 0
            # Окно перекрывало меню и съедало нажатие: даём ещё время.
            end += 10
        elif kind in ("list", "detail", "active"):
            # Для detail/active нужен третий BACK: первый часто закрывает
            # только клавиатуру, второй уводит на список, и только третий
            # возвращает в меню. Без него получаем «не удалось выйти к меню».
            need = 2 if kind == "list" else 3
            if backs < need:
                try:
                    key_back()
                except RuntimeError:
                    tap(*BACK_BTN)
                backs += 1
                time.sleep(0.35)
            elif kind == "list":
                log("перезаход: меню не появилось, остаёмся на списке")
                return a, path
            else:
                log("перезаход: не удалось выйти к меню")
                return None, None
        elif kind == "other" and backs and starts < 1:
            # BACK вывел из приложения — возвращаем его без force-stop.
            start_app()
            starts += 1
            backs = 0
            enter_pending = False
            time.sleep(1.2)
        # Не повторяем BACK или «Новые», пока сервер обрабатывает прошлый тап.
        time.sleep(0.15)
    log("Не удалось подтвердить вход в «Новые» за отведённое время")
    return None, None


def ensure_list(max_steps=6):
    return open_new(timeout=open_wait_seconds())


def mark_new_taken(order):
    """Помечает заказ как взятый из «Новых».

    tasks.py читает этот файл и ставит такие заказы первыми в «Активных»:
    у них самый короткий срок на выполнение.
    """
    try:
        data = {}
        if os.path.exists(NEW_TAKEN_PATH):
            with open(NEW_TAKEN_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        data[str(order)] = datetime.now().isoformat(timespec="seconds")
        with open(NEW_TAKEN_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log(f"заказ {order} взят из «Новых» — отмечен для «Активных»")
    except Exception as error:  # noqa: BLE001 - отметка не должна ронять забор
        log(f"не удалось отметить заказ {order}: {error!r}")


def header_icons(a, line_y, text_x):
    """Маленькие цветные изображения у заголовка, не большие иконки приложений."""
    if a is None:
        return []
    x0, x1 = max(0, int(text_x) - 90), min(a.shape[1], int(text_x) + 70)
    y0, y1 = max(0, int(line_y) - 10), min(a.shape[0], int(line_y) + 55)
    band = a[y0:y1, x0:x1]
    colored = (band.max(axis=2) - band.min(axis=2) > 55) & (band.max(axis=2) > 100)
    columns = np.flatnonzero(colored.sum(axis=0) >= 3)
    if not len(columns):
        return []
    groups = np.split(columns, np.flatnonzero(np.diff(columns) > 3) + 1)
    icons = []
    for group in groups:
        left, right = int(group[0]), int(group[-1]) + 1
        ys = np.flatnonzero(colored[:, left:right].any(axis=1))
        if not len(ys):
            continue
        top, bottom = int(ys[0]), int(ys[-1]) + 1
        if 22 <= right - left <= 65 and 18 <= bottom - top <= 60:
            icons.append(a[y0 + top:y0 + bottom, x0 + left:x0 + right])
    return icons


def colored_icon_before(a, line_y, text_x):
    return bool(header_icons(a, line_y, text_x))


def icon_signature(icon):
    return np.array(Image.fromarray(icon.astype(np.uint8)).resize((32, 32), Image.Resampling.BILINEAR)).astype(np.int16)


_galaxy_template = None


def galaxy_store_icon(a, line_y, text_x):
    """Совпадение с эталоном подтверждённой иконки Galaxy Store, а не с любым цветом."""
    global _galaxy_template
    path = os.path.join(BASE_DIR, "assets", "galaxy_store.png")
    if _galaxy_template is None and os.path.isfile(path):
        with Image.open(path) as image:
            _galaxy_template = icon_signature(np.array(image.convert("RGB")))
    if _galaxy_template is None:
        return False
    return any(np.abs(icon_signature(icon) - _galaxy_template).mean() < 22
               for icon in header_icons(a, line_y, text_x))


def card_separators(a):
    """Тонкие серые линии между карточками: подтверждают видимость всей карточки."""
    band = a[250:CONTENT_BOTTOM, 15:-15]
    gray = ((band.max(axis=2) - band.min(axis=2) < 6)
            & (band.min(axis=2) > 155) & (band.max(axis=2) < 240))
    rows = np.flatnonzero(gray.mean(axis=1) > 0.85)
    if not len(rows):
        return []
    groups = np.split(rows, np.flatnonzero(np.diff(rows) > 1) + 1)
    return [250 + int(group[-1]) for group in groups if len(group) <= 8]


ORDER_RE = ["заказ", "ваказ", "закаэ", "3аказ", "закaз"]


def norm(s):
    return s.lower().replace("ё", "е")


def is_order_header(line):
    t = norm(line["text"])
    if not any(w in t for w in ORDER_RE):
        return False
    return re.search(r"(?<!\d)\d{5,8}(?!\d)", line["text"]) is not None


def order_number(line):
    m = re.search(r"(?<!\d)\d{5,8}(?!\d)", line["text"])
    return m.group(0) if m else line["text"]


def merge_rows(lines, tol=18):
    """Склеивает OCR-строки, попавшие на одну визуальную строку.

    Windows OCR иногда рвёт «Заказ №1444292» на «Заказ» и «444292» с
    одинаковым y — по отдельности такой заголовок не распознаётся.
    """
    rows = []
    for line in sorted(lines, key=lambda l: (l["y"], l.get("x", 0))):
        text = line["text"].strip()
        if not text:
            continue
        if rows and abs(line["y"] - rows[-1]["y"]) <= tol:
            rows[-1]["text"] += " " + text
        else:
            rows.append({"y": line["y"], "x": line.get("x", 0), "text": text})
    return rows


def same_order(expected, actual):
    """Сравнивает номера с поправкой на потери OCR.

    «№1444292» часто читается как «444292», поэтому короткий номер
    считаем тем же заказом, если он хвост длинного.
    """
    if not expected or not actual:
        return False
    if str(expected) == str(actual):
        return True
    short, long = sorted((str(expected), str(actual)), key=len)
    return len(short) >= 5 and long.endswith(short)


def order_matches(expected, actual, tolerance=2):
    """Сравнение номеров заказов с допуском на ошибку OCR.

    Windows OCR читает номер по-разному от кадра к кадру: один и тот же
    заказ то «1433514», то «1433512», а иногда теряется цифра и остаётся
    «143163». Поэтому, кроме точного совпадения, принимаем номера, у которых
    совпадает начало, а различаются только последние цифры.
    """
    if not expected or not actual:
        return False
    if str(expected) == str(actual):
        return True
    if same_order(expected, actual):
        return True
    exp = re.sub(r"\D", "", str(expected))
    act = re.sub(r"\D", "", str(actual))
    if len(exp) < 5 or len(act) < 5:
        return False
    common = min(len(exp), len(act)) - tolerance
    if common >= 4 and exp[:common] == act[:common]:
        return True
    return False


def analyze_cards(a, lines):
    """Карточки по OCR-заголовкам «Заказ №…» (без опоры на кольца)."""
    headers = sorted([r for r in merge_rows(lines) if is_order_header(r) and 250 <= r["y"] < CONTENT_BOTTOM], key=lambda r: r["y"])
    separators = card_separators(a)
    active = is_active_list(lines, a)
    cards = []
    for i, h in enumerate(headers):
        top = h["y"]
        next_y = headers[i + 1]["y"] if i + 1 < len(headers) else CONTENT_BOTTOM
        boundary = next((y for y in separators if top + 70 < y < next_y), None)
        bottom = boundary if boundary is not None else next_y - 5
        block = [l for l in lines if top - 5 <= l["y"] <= bottom]
        text = norm(" ".join(l["text"] for l in block))
        foreign = forbidden_reason(text) == "foreign"

        x_word = h["x"]
        for w in h.get("words", []):
            if "каз" in norm(w["t"]):
                x_word = w["x"]
                break
        icon = colored_icon_before(a, top, x_word)
        samsung = forbidden_reason(text) == "samsung" or galaxy_store_icon(a, top, x_word)
        partner = "партнерск" in text or "telegram бот" in text

        # Цена задания — число вида 5.00 в правой колонке карточки.
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

        # Полноценная карточка: блок целиком виден. Иначе строка «Сменить
        # учетную запись» может быть отрезана и иностранец пройдёт как RU.
        complete = (i + 1 < len(headers)) or (boundary is not None)
        low = top + 60 >= CONTENT_BOTTOM - 20
        requirements = "приложени" in text or "открыть на" in text

        # карточка обрезана снизу, если под ней нет заголовка и мало места
        cut = not complete
        if active:
            reason = "active"
        elif foreign:
            reason = "foreign"
        elif samsung:
            reason = "samsung"
        elif partner:
            reason = "partner"
        elif cut:
            reason = "cut"
        elif low:
            reason = "low"
        elif not requirements:
            reason = "no-requirements"
        else:
            reason = ""
        cards.append({
            "top": top,
            "order": order_number(h),
            "title": h["text"],
            "price": price,
            "foreign": foreign,
            "icon": icon,
            "samsung": samsung,
            "low": low,
            "ru": ((not foreign) and (not samsung) and (not partner)
                   and complete and not low and requirements and not active),
            "cut": cut,
            "text": text,
            "lines": len(block),
            "partner": partner,
            "requirements": requirements,
            "reason": reason,
        })
    return cards


def looks_like_order_detail(lines):
    """Признаки страницы одного заказа (а не списка «Новые»/«Активные»).

    Слова «найти приложение», «загрузить», «поисковый запрос» есть и на
    карточках списков — по ним нельзя отличать detail (это уже ломало
    вход в «Новые»: open_new() принимал список за страницу и уходил BACK).
    Надёжный признак — крупный заголовок «Заказ №…» в самой верхушке экрана
    и отсутствие заголовков списков.
    """
    text = norm(" ".join(l["text"] for l in lines))
    if "активные заказ" in text or "новые заказ" in text:
        return False
    return any(is_order_header(row) and row.get("y", 9999) < 500
               for row in merge_rows(lines))


def is_active_list(lines, a=None):
    """Экран «Активные заказы»: розовый заголовок, красные кольца, без кнопки-забора."""
    text = norm(" ".join(l["text"] for l in lines))
    if "активные заказ" in text:
        return True
    # RapidOCR не читает светлый заголовок «АКТИВНЫЕ ЗАКАЗЫ» на розовом
    # фоне, поэтому строку-признак мы можем не увидеть. Тогда опираемся на
    # розовую шапку + МНОГО заголовков заказов: это список, а не деталка
    # (у деталки один заголовок). Раньше looks_like_order_detail ложно
    # перебивал список из‑за «Заказ №°...» с мусорным «°» в OCR.
    heads = [r for r in merge_rows(lines) if is_order_header(r)]
    # Сначала отсекаем страницу заказа: на деталке розовая шапка + ДВА
    # заголовка («Заказ №…» в шапке и в карточке), и старый порядок
    # (active_header + heads>=2 → True) принимал её за список. Из‑за этого
    # do_task видел «список» на уже открытой деталке и откладывал заказ.
    if looks_like_order_detail(lines):
        return False
    if active_header(a) and len(heads) >= 2:
        return True
    return active_header(a)


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


def page_state(a):
    """(текущая страница, всего страниц). Текущая — самый тёмный квадрат."""
    buttons = page_buttons(a)
    if not buttons:
        return 1, 1
    dark = min(buttons, key=lambda b: b[2])
    return buttons.index(dark) + 1, len(buttons)


def go_to_page(target, timeout=25):
    """Переключает список «Новых» на нужную страницу.

    Нижние квадраты страниц — индикатор, а не кнопки: тап по ним список
    не переключает. Поэтому сначала длинными свайпами поднимаем список
    к началу, где квадраты настоящие, и тапаем только верхние (y < 1000).
    """
    end = time.monotonic() + timeout
    last_tap = 0.0
    last_scroll = 0.0
    while time.monotonic() < end:
        kind, a, path, lines = read_screen("pg.png")
        if kind in ("active", "list"):
            current, total = page_state(a)
            if current == target:
                return True
            buttons = page_buttons(a)
            top = [b for b in buttons if b[1] < 1000]
            if target <= len(top) and time.monotonic() - last_tap > 2.5:
                tap(top[target - 1][0], top[target - 1][1])
                last_tap = time.monotonic()
            elif len(top) < target and time.monotonic() - last_scroll > 0.8:
                swipe(540, 450, 540, 2000, 600)
                last_scroll = time.monotonic()
        time.sleep(0.5)
    return False


def _new_page_cards(a, lines, target, orders, page_seen, path=None):
    """Разбирает экран «Новых» и дополняет общий словарь заказов.

    Карточка, увиденная сначала обрезанной, а потом целиком, заменяется
    полной версией: только у неё выставлен признак ru. Возвращает число
    заказов, впервые увиденных на этой странице (для остановки свайпа).
    """
    new = 0
    for card in analyze_cards(a, lines):
        card["page"] = target
        if path:
            card["path"] = path
        key = card["order"]
        if key not in page_seen:
            page_seen.add(key)
            new += 1
        old = orders.get(key)
        if old is None:
            orders[key] = card
            continue
        if card["ru"] and not old["ru"]:
            orders[key] = card
        else:
            old["price"] = max(old.get("price", 0.0), card.get("price", 0.0))
    return new


def _scroll_top(swipes=8, pause=0.35):
    """Прокручивает список к началу текущей страницы.

    Приложение помнит позицию прокрутки между заходами в «Новые», поэтому
    повторный поиск карточки может начаться с середины страницы — и нужная
    карточка просто не попадёт в кадр. Листаем вверх до упора.
    """
    for _ in range(swipes):
        swipe(540, 600, 540, 1950, 400)
        time.sleep(pause)


def scan_new_page(deadline, blocked_ids, blocked, seen, skip_logged, sort_by_price,
                  scroll_to_bottom=True, empty_stop=2, now=None):
    """Просматривает текущую страницу «Новых» сверху вниз.

    Возвращает (card, path, cards): подходящее RU-задание, кадр, на котором
    оно найдено, и все увиденные карточки. Задание берётся сразу с этого
    кадра: сервер пересортировывает список «Новые», и повторный поиск
    карточки по номеру её уже не находит.
    """
    now = now if now is not None else time.monotonic()
    _scroll_top()
    seen_orders = set()
    stagnant = 0
    all_cards = []
    for step in range(30):
        if remaining(deadline) <= 0 or stop_requested():
            break
        kind, a, path, lines = read_screen("npg.png")
        if kind != "list":
            break
        cards = analyze_cards(a, lines)
        all_cards.extend(cards)
        for c in cards:
            if not c["ru"] and c["order"] not in skip_logged:
                skip_logged.add(c["order"])
                log(f"skip: {c['order']} top={c['top']} reason={c['reason']} "
                    f"title={c['title']}")
        eligible = [c for c in cards if c["ru"]
                    and c["order"] not in blocked_ids
                    and c["order"] not in blocked
                    and now - seen.get(c["order"], -1000) >= 90]
        if eligible:
            if sort_by_price:
                card = max(eligible, key=lambda c: (c.get("price", 0.0), -c["top"]))
            else:
                card = min(eligible, key=lambda c: c["top"])
            return card, path, all_cards
        fresh = {c["order"] for c in cards} - seen_orders
        seen_orders |= {c["order"] for c in cards}
        stagnant = 0 if fresh else stagnant + 1
        if stagnant >= empty_stop:
            break
        if not scroll_to_bottom:
            break
        swipe(1048, 1900, 1048, 850, 250)
        time.sleep(0.6)
    return None, None, all_cards


def collect_new_cards(max_pages=0, scroll_to_bottom=True, empty_stop=2, deadline=None):
    """Обходит страницы «Новых» и собирает все карточки.

    Возвращает (cards, current_page, total_pages). У каждой карточки есть
    page — страница, на которой её видели. Координаты из скана устаревают
    после прокрутки, поэтому действовать нужно через locate_new_card().

    max_pages = 0 — обходить все страницы; N — только первые N.
    """
    deadline = deadline or (datetime.now() + timedelta(minutes=10))
    kind, a, path, lines = read_screen("ncs.png")
    if kind != "list":
        return [], 1, 1
    current, total = page_state(a)
    limit = total if max_pages <= 0 else min(total, max_pages)
    orders = {}
    for target in range(1, limit + 1):
        if target != current and not go_to_page(target):
            log(f"страница {target} «Новых» не открылась — обход закончен")
            break
        current = target
        # С каждой страницы начинаем сверху: приложение может вернуть нас
        # на середину списка, и верхние карточки иначе не видны.
        _scroll_top()
        page_seen = set()
        stagnant = 0
        for step in range(30):
            if remaining(deadline) <= 0 or stop_requested():
                break
            kind, a, path, lines = read_screen(f"np{target}_{step}.png")
            if kind != "list":
                break
            new = _new_page_cards(a, lines, target, orders, page_seen, path)
            if not scroll_to_bottom:
                break
            stagnant = stagnant + 1 if new == 0 else 0
            if stagnant >= empty_stop:
                break
            swipe(1048, 1900, 1048, 850, 250)
            time.sleep(0.6)
    return list(orders.values()), current, total


def locate_new_card(order, max_pages=0, scroll_to_bottom=True, empty_stop=2,
                    deadline=None, hint_page=None, hint_top=None):
    """Ищет карточку «Новых» по номеру и возвращает её с актуальными координатами.

    Возвращает (card, path). Карточка всегда снята с того экрана, на котором
    её можно сразу нажать. hint_page — страница, на которой карточку видели
    в прошлом скане: с неё начинаем, чтобы не листать список с начала.
    hint_top — координата карточки в прошлом скане: по ней выбираем между
    похожими номерами, если точное совпадение не нашлось.
    """
    deadline = deadline or (datetime.now() + timedelta(minutes=10))
    if open_new(force_reenter=True, timeout=min(open_wait_seconds(), remaining(deadline)))[0] is None:
        return None, None
    kind, a, path, lines = read_screen("nlc.png")
    if kind != "list":
        return None, None
    current, total = page_state(a)
    limit = total if max_pages <= 0 else min(total, max_pages)
    # Сначала страница, на которой карточку видели: так быстрее и меньше
    # риск, что список перестроится между обходами.
    order_pages = ([hint_page] if hint_page and 1 <= hint_page <= limit else [])
    order_pages += [p for p in range(1, limit + 1) if p not in order_pages]
    log(f"поиск {order}: страниц {total}, начинаю с {order_pages}")
    for target in order_pages:
        if target != current and not go_to_page(target):
            log(f"поиск {order}: страница {target} не открылась — следующая")
            continue
        current = target
        _scroll_top()
        seen_here = set()
        stagnant = 0
        for step in range(30):
            if remaining(deadline) <= 0 or stop_requested():
                return None, None
            kind, a, path, lines = read_screen(f"nl{target}_{step}.png")
            if kind != "list":
                return None, None
            cards = analyze_cards(a, lines)
            fresh = {c["order"] for c in cards} - seen_here
            if fresh:
                log(f"поиск {order}: стр.{target} экран {step} — "
                    + ", ".join(sorted(fresh)))
            seen_here |= {c["order"] for c in cards}
            exact = [c for c in cards if c["order"] == order]
            if exact:
                exact[0]["page"] = target
                log(f"поиск {order}: найден на странице {target}")
                return exact[0], path
            # Номер может отличаться последней цифрой из-за OCR — берём
            # ближайшую по позиции похожую карточку.
            fuzzy = [c for c in cards if order_matches(order, c["order"])]
            if fuzzy:
                if hint_top is not None:
                    fuzzy.sort(key=lambda c: abs(c["top"] - hint_top))
                else:
                    fuzzy.sort(key=lambda c: c["top"])
                fuzzy[0]["page"] = target
                log(f"поиск {order}: взят похожий {fuzzy[0]['order']} на странице {target}")
                return fuzzy[0], path
            stagnant = 0 if fresh else stagnant + 1
            if stagnant >= empty_stop:
                break
            swipe(1048, 1900, 1048, 850, 250)
            time.sleep(0.6)
    log(f"поиск {order}: карточка не найдена ни на одной странице")
    return None, None


def forbidden_reason(text):
    text = norm(text)
    if "samsung" in text or "galaxy store" in text:
        return "samsung"
    if (re.search(r"сменить\s+учет", text) or "vpn" in text or "впн" in text
            or re.search(r"(?:изменить|сменить)\s+.*язык", text)):
        return "foreign"
    return None


def modal_lines(a, path):
    """OCR только белого окна: фон списка не подмешивается в ответ сервера."""
    region = a[500:1850, 120:960]
    white = region.min(axis=2) > 240
    ys, xs = np.where(white)
    if len(ys) < 1000:
        return ocr(path)
    x0, x1 = 120 + int(xs.min()), 121 + int(xs.max())
    y0, y1 = 500 + int(ys.min()), 501 + int(ys.max())
    crop_path = frame_path("modal_crop.png")
    if not save_png(a[y0:y1, x0:x1], crop_path):
        return ocr(path)
    lines = ocr(crop_path)
    for line in lines:
        line["x"], line["y"] = line["x"] + x0, line["y"] + y0
        for word in line.get("words", []):
            word["x"], word["y"] = word["x"] + x0, word["y"] + y0
    return lines


def response_status(text):
    """Не считает любое модальное окно подтверждением забора."""
    text = norm(text)
    forbidden = forbidden_reason(text)
    if forbidden:
        return forbidden
    # Сначала отказы: в окне отказа тоже есть номер заказа и цена.
    if "недоступ" in text or "уже взят" in text or "ошибка" in text:
        return "unavailable"
    if "поисковый запрос" in text or "сверните приложение advertapp" in text:
        return "instructions"
    # Модалка/страница с данными заказа - заказ взят: номер + название/цена
    # + требования ("открыть на N день").
    if re.search(r"\d{5,8}", text) and (
            "руб" in text or "заказ" in text
            or any(phrase in text for phrase in (
                "взят", "принят", "добавлен", "добавлен в активные",
                "открыть на", "установить", "скачать"))):
        return "accepted"
    return "unknown"


def record_result(order, status, text="", path=None, stage="submit"):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    archived = None
    if path and os.path.isfile(path):
        archived = os.path.join(TMP, f"result_{stamp}_{order}.png")
        shutil.copyfile(path, archived)
    record = {"at": datetime.now().isoformat(timespec="seconds"), "serial": SERIAL,
              "order": order, "status": status, "stage": stage,
              "text": text, "frame": archived}
    with open(RESULTS_PATH, "a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
    log(f"result: {order} status={status} text={text!r}")


def wait_app_ready(timeout=110.0):
    """Ждёт, пока приложение выйдет из splash в меню или список."""
    end = time.monotonic() + timeout
    while time.monotonic() < end and not stop_requested():
        if not app_in_focus():
            time.sleep(0.3)
            continue
        kind, a, path, lines = read_screen("ready.png")
        text = norm(" ".join(line["text"] for line in lines))
        # Samsung на холодном старте иногда показывает ANR-диалог
        # «Приложение не отвечает». Жмём «Подождать», а не «Закрыть».
        if ("не отвечает" in text or "не реагирует" in text
                or "isn't responding" in text or "is not responding" in text):
            pos = (find_word(lines, "подождать") or find_word(lines, "ждать")
                   or find_word(lines, "wait"))
            if pos:
                log("ANR: нажимаю «Подождать»")
                tap(*pos)
                time.sleep(2)
                continue
        if ("подключения к серверу" in text
                or "failed to connect" in text):
            pos = find_word(lines, "повтор") or find_word(lines, "connect")
            if pos:
                log("нет подключения к серверу — нажимаю «Повтор/Connect»")
                tap(*pos)
                time.sleep(5)
                continue
        if kind in ("menu", "list", "detail", "active"):
            return a, path, kind
        if kind == "modal":
            close_modal(a, modal_lines(a, path))
        time.sleep(0.3)
    return None, None, "timeout"


STOP_PATH = os.path.join(BASE_DIR, "hunter.stop")


def configure_device(serial=None, slot=None):
    """Переключает модуль на конкретное устройство и его каталог runs/<serial>.

    Все временные файлы, логи, результаты, offer.pending и ЧС становятся
    персональными. Это позволяет запускать несколько процессов бота
    одновременно: каждый видит только свой телефон.
    """
    global SERIAL, TMP, LOG_PATH, RESULTS_PATH, OFFER_PATH, STOP_PATH
    global RUN_DIR, DEVICE_SLOT, NEW_TAKEN_PATH, _ACTIVE_APP, _OFFERS_TAKEN
    if serial:
        SERIAL = str(serial).strip()
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", SERIAL) or "device"
    RUN_DIR = os.path.join(BASE_DIR, "runs", slug)
    os.makedirs(RUN_DIR, exist_ok=True)
    TMP = os.path.join(RUN_DIR, "frames")
    os.makedirs(TMP, exist_ok=True)
    LOG_PATH = os.path.join(RUN_DIR, "hunter2.log")
    RESULTS_PATH = os.path.join(RUN_DIR, "results.jsonl")
    OFFER_PATH = os.path.join(RUN_DIR, "offer.pending")
    NEW_TAKEN_PATH = os.path.join(RUN_DIR, "new_taken.json")
    STOP_PATH = os.path.join(RUN_DIR, "hunter.stop")
    DEVICE_SLOT = slot
    _ACTIVE_APP = None
    _OFFERS_TAKEN = 0
    try:
        from core import blacklist
        blacklist.PATH = os.path.join(RUN_DIR, "order_blacklist.json")
    except Exception:
        pass
    return RUN_DIR


def prepare_device():
    """Приводит экран к 1080×2412 / 408, сохраняя оригинал в runs/<serial>."""
    from core import display
    result = display.prepare(SERIAL, RUN_DIR)
    size = result["override_size"] or result["physical_size"]
    density = result["override_density"] or result["physical_density"]
    log(f"display: {SERIAL} -> {size} / {density}")
    return result


def stop_requested():
    return os.path.isfile(STOP_PATH) or os.path.isfile(GLOBAL_STOP_PATH)


def remaining(deadline):
    return max(0.0, (deadline - datetime.now()).total_seconds())


def schedule_times(start_at=None, end_at=None, now=None, duration=600):
    """Абсолютные границы; переход через полночь не завершает прогон немедленно."""
    now = now or datetime.now()
    start = datetime.combine(now.date(), start_at) if start_at else now
    end = datetime.combine(start.date(), end_at) if end_at else start + timedelta(seconds=duration)
    if end_at and end <= start:
        end += timedelta(days=1)
    if start_at and end <= now:
        start, end = start + timedelta(days=1), end + timedelta(days=1)
    return max(start, now), end


def rush_window(start):
    mark = start.replace(minute=0, second=0, microsecond=0)
    if start > mark + timedelta(minutes=2):
        mark += timedelta(hours=1)
    return mark - timedelta(seconds=5), mark + timedelta(minutes=2)


def restart_app(deadline):
    if remaining(deadline) < 2 or stop_requested():
        return False
    # Телефон мог уснуть за время ожидания: будим экран и снимаем шторку,
    # иначе screencap вернёт чёрный кадр, а тапы уйдут в никуда.
    adb("shell", "input keyevent KEYCODE_WAKEUP")
    adb("shell", "wm dismiss-keyguard")
    time.sleep(0.5)
    adb("shell", "am force-stop " + active_app())
    result = adb("shell", "am start -n " + main_activity(active_app()))
    if result.returncode:
        raise RuntimeError("Не удалось запустить AdvertApp")
    log("started app (данные не очищаются)")
    _, _, kind = wait_app_ready(min(70, remaining(deadline)))
    log(f"app ready: {kind}")
    return kind != "timeout"


def download_button(a, lines):
    """Кнопка именно разрешённого магазина, включая RuStore и AppGallery."""
    if a is None:
        return None
    for line in lines:
        text = norm(line["text"])
        if not any(word in text for word in ("загруз", "скача", "установ")):
            continue
        if forbidden_reason(text):
            continue
        if not any(store in text for store in (
                "google play", "rustore", "русто", "appgallery", "huawei", "хуав",
                "xiaomi", "getapps", "get apps", "мистор", "mishop")):
            continue
        words = line.get("words", [])
        height = max((word["h"] for word in words), default=40)
        y = int(line["y"] + height // 2)
        if not 350 < y < 1200:
            continue
        bar = find_green_bar(a, max(350, y - 15), min(1200, y + 15), min_width=500)
        if bar:
            return bar[1], y
    return None


def hold_min():
    """Минимальное удержание приложения из настроек панели (по умолчанию 70 с)."""
    try:
        return max(30, int(_load_panel_settings().get("hold_min_seconds", 70)))
    except Exception:
        return 70


SYSTEM_PKGS = {
    "com.google.android.gms",
    "com.android.vending",
    "com.google.android.gsf",
    "com.google.android.packageinstaller",
    "com.android.permissioncontroller",
    "com.android.settings",
    "com.android.intentresolver",
    "com.android.launcher3",
    "com.realme.launcher",
    "com.coloros.launcher",
    "com.oppo.launcher",
    *APP_PACKAGES,
}


def order_package(order):
    """Пакет заказа из legacy-базы или базы v2 — чтобы не удержать чужое приложение."""
    try:
        data = json.load(open(os.path.join(BASE_DIR, "order_apps.json"), encoding="utf-8"))
        entry = data.get(str(order))
        if isinstance(entry, dict) and entry.get("package"):
            return entry["package"]
        if isinstance(entry, str):
            return entry
    except Exception:
        pass
    try:
        data = json.load(open(os.path.join(BASE_DIR, "panel_apps.json"), encoding="utf-8"))
        for app in data.get("apps", []):
            if str(order) in {str(x) for x in app.get("order_ids", [])}:
                return app.get("package")
    except Exception:
        pass
    return None


def accept_open_dialog(a, lines, text):
    """Принимает модалку «Открыть приложение заказа №… и провести N секунд»."""
    # OCR часто вставляет мусор между «заказа» и номером: «заказа | Г\Р1395363»,
    # поэтому допускаем до 12 любых нецифровых символов.
    # OCR有时 разбивает номер на «заказа | NQ1 447425»: цифры идут groups
    # «1» и «447425» с пробелом. Склеиваем первые группы, пока не наберём
    # нормальный 5–8-значный номер заказа.
    tail_match = re.search(r"заказ\w*\D{0,24}", text)
    order = ""
    if tail_match:
        groups = re.findall(r"\d+", text[tail_match.end():])
        if groups:
            order = groups[0]
            if len(order) < 5 and len(groups) > 1:
                order += groups[1]
    # OCR вставляет разделитель между числом и словом: «30 | секунд!».
    seconds = re.search(r"(\d+)\D{0,8}секунд", text)
    if len(order) < 5 or not seconds:
        return None
    hold = int(seconds.group(1))
    expected = order_package(order)
    # Сначала ищем текстовую кнопку: в новой сборке зелёная полоса — это
    # бейдж с ценой, тап по ней не открывает приложение.
    pos = None
    for line in sorted(lines, key=lambda item: item.get("y", 0), reverse=True):
        token = norm(str(line.get("text", ""))).strip(" !:)")
        if token == "открыть":
            pos = (int(line.get("x", 0)) + 40,
                   int(line.get("y", 0)) + int(line.get("h", 30)) // 2)
            break
    bar = find_green_bar(a, 900, 1800, 300) if not pos else None
    if not bar and not pos:
        log("диалог открытия: ни зелёной кнопки, ни подписи «Открыть» не найдено")
        return None
    suffix = f", ожидаем {expected}" if expected else ""
    log(f"диалог открытия: заказ {order}, держать {hold} с{suffix}")
    if pos:
        log(f"диалог открытия: тап по подписи «Открыть» {pos}")
        tap(*pos)
    else:
        tap(bar[1], bar[2])
    time.sleep(2.0)
    pkg = None
    seen = []
    end = time.monotonic() + 12
    while time.monotonic() < end:
        found = re.search(r"u0 ([A-Za-z0-9_.]+)/", focus())
        if found:
            cur = found.group(1)
            if cur not in seen:
                seen.append(cur)
            if cur not in APP_PACKAGES and cur not in SYSTEM_PKGS:
                if not expected or cur == expected:
                    pkg = cur
                    break
        time.sleep(0.5)
    if not pkg:
        log(f"диалог открытия: нужное приложение не запустилось (видели: {', '.join(seen) or '—'})")
        start_app()
        time.sleep(1.5)
        return "unknown"
    global LAST_HELD_PKG
    LAST_HELD_PKG = pkg
    log(f"диалог открытия: {pkg}, ждём {max(hold_min(), hold + 5)} с")
    end = time.monotonic() + max(hold_min(), hold + 5)
    while time.monotonic() < end:
        time.sleep(2.0)
        if pkg not in focus():
            adb("shell", f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
    adb("shell", "am start -n " + main_activity(active_app()))
    time.sleep(2.0)
    return "accepted"


def green_label(lines, bar):
    """Текст на зелёной кнопке: по нему отличаем «Взять» от «Открыть»."""
    if not bar:
        return ""
    words = []
    for line in lines:
        if abs(line["y"] - bar[2]) <= 140:
            words.extend(word["t"] for word in line.get("words", []))
    return norm(" ".join(words))


_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

# Слова, которые встречаются в названиях десятков приложений и не помогают
# выбрать пакет: по ним нельзя однозначно опознать приложение.
_NAME_STOP = {
    "online", "mobile", "loans", "loan", "credit", "credits", "money",
    "bank", "banks", "finance", "fintech", "app", "apps", "store", "game",
    "games", "official", "tech", "soft", "service", "services", "zaim",
    "zaimy", "zaym", "onlayn", "mfo",
}


def translit(text):
    return "".join(_TRANSLIT.get(char, char) for char in text.lower())


def app_name_tokens(name):
    """Латинские и транслитерированные слова названия, без общих слов."""
    tokens = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", name):
        tokens.append(word.lower())
    for word in re.findall(r"[А-Яа-яЁё][А-Яа-яЁё0-9]{3,}", name):
        tokens.append(translit(word))
    return [token for token in dict.fromkeys(tokens)
            if len(token) >= 4 and token not in _NAME_STOP]


def find_package_by_name(name):
    """Ищет установленный сторонний пакет по названию приложения из модалки."""
    result = adb("shell", "pm list packages -3")
    out = (result.stdout or b"").decode("utf-8", "replace")
    packages = [line.split(":", 1)[1].strip() for line in out.splitlines()
                if line.startswith("package:")]
    tokens = app_name_tokens(name)
    log(f"диалог удаления: название «{name}», токены {tokens}")
    # Транслитерация «Яндекс» даёт yandeks, а в пакете написано yandex.
    # Узкий случай: только «Яндекс — с Алисой» -> com.yandex.searchapp,
    # чтобы не перепутать с Яндекс Картами/Такси и не удалить не то.
    low = norm(name)
    if ("yandeks" in tokens or "яндекс" in low) and \
            ("alisoy" in tokens or "alisa" in tokens or "алис" in low or "alice" in low):
        if "com.yandex.searchapp" in packages:
            return "com.yandex.searchapp"
    best, best_score = None, 0
    for pkg in packages:
        flat = re.sub(r"[^a-z0-9]", "", pkg.lower())
        score = sum(len(token) for token in tokens if token in flat)
        if score > best_score:
            best, best_score = pkg, score
    return best


def accept_uninstall_dialog(a, lines, text):
    """Удаляет приложение, названное в инструкции, и жмёт «Подтвердить»."""
    global LAST_UNINSTALLED_PKG
    match = re.search(r"удалит\w*\s+приложение\s*[«<]\s*(.+?)\s*[»>]", text, re.S)
    if not match:
        log("диалог удаления: название приложения не распознано")
        return None
    name = re.sub(r"\s*\|\s*", " ", match.group(1)).strip(" |")
    pkg = find_package_by_name(name)
    if not pkg:
        log(f"диалог удаления: пакет для «{name}» не найден")
        return "uninstall_failed"
    log(f"диалог удаления: удаляю {pkg}")
    result = adb("shell", f"pm uninstall {pkg}")
    output = ((result.stdout or b"") + (result.stderr or b"")).decode("utf-8", "replace").strip()
    if result.returncode or "Success" not in output:
        log(f"диалог удаления: {pkg} не удалён: {output!r}")
        return "uninstall_failed"
    LAST_UNINSTALLED_PKG = pkg
    log(f"диалог удаления: {pkg} удалён, подтверждаю")
    time.sleep(1.0)
    bar = find_green_bar(a, 900, 1800, 300)
    if not bar:
        log("диалог удаления: кнопка «Подтвердить» не найдена")
        return "uninstalled"
    tap(bar[1], bar[2])
    time.sleep(2.0)
    return "uninstalled"


def retry_hold_dialog(a, lines, text):
    """Недодержка: закрывает предупреждение, снова открывает приложение и держит срок."""
    global LAST_HELD_PKG
    pkg = LAST_HELD_PKG
    close_modal(a, lines)
    if not pkg:
        log("модалка недодержки: пакет неизвестен — повторим задание позже")
        return "not_enough_time"
    seconds = 70
    match = re.search(r"(\d+)\D{0,8}секунд", norm(text))
    if match:
        seconds = max(hold_min(), int(match.group(1)) + 5)
    log(f"модалка недодержки: открываю {pkg} и держу {seconds} с")
    adb("shell", f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
    time.sleep(2.0)
    end = time.monotonic() + seconds
    lost = 0
    while time.monotonic() < end:
        time.sleep(2.0)
        if pkg in focus():
            lost = 0
            continue
        lost += 1
        if lost >= 3:
            log(f"модалка недодержки: {pkg} потерял фокус — запускаю снова")
            adb("shell", f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
            lost = 0
    adb("shell", "am start -n " + main_activity(active_app()))
    time.sleep(2.0)
    return "held"


def modal_kind(text, label=""):
    """reminder — напоминание об уже взятом заказе; offer — предложение нового."""
    text = norm(text)
    label = norm(label)
    if "удалит" in text and "приложение" in text:
        return "uninstall"
    # Недодержка: приложение закрыли раньше срока — нужно открыть снова.
    if "не пробыли" in text or "повторите выполнение" in text:
        return "not_enough_time"
    # Инструкция «Поисковый запрос … Зайдите в Google Play»: нужно нажать
    # зелёную кнопку, установить приложение и вернуться подтверждать заказ.
    if ("поисковый запрос" in text or "зайдите в google play" in text
            or "открыть google play" in text):
        return "instructions"
    # Напоминание проверяем первым: в нём тоже есть «заказ» и зелёная кнопка.
    if ("открыть приложение заказа" in text
            or ("провести в приложении" in text and "секунд" in text)):
        return "reminder"
    if any(phrase in text for phrase in (
            "взять заказ", "возьмите заказ", "новый заказ", "новое задание",
            "взять задание", "предлагаем", "доступен заказ", "принять заказ",
            "хотите взять", "доступно новое")):
        return "offer"
    # «Отлично! Вам доступны следующее задание!» — новая сборка AdvertApp
    # пишет так, когда предлагает заказ. Без этого правила модалка уходила
    # в «other», и кнопка «Пропустить» сбрасывала предложение.
    if "доступн" in text and "задани" in text:
        return "offer"
    if any(word in label for word in ("взять", "принять", "получить", "хочу")):
        return "offer"
    if "начать" in label and re.search(r"\d{5,8}", text) and (
            "руб" in text or "заказ" in text):
        return "offer"
    # Кнопки «Пропустить | Начать» — надёжный признак предложения, даже если
    # OCR обрезал заголовок и не прочитал номер заказа.
    if "пропустить" in text and "начать" in text:
        return "offer"
    # Номер заказа OCR иногда читает кириллицей («ГР14З5222»): нормализуем
    # З→3 и О→0, иначе offer по номеру не распознаётся.
    normalized_digits = (text.replace("З", "3").replace("з", "3")
                         .replace("О", "0").replace("о", "0"))
    if "начать" in text and re.search(r"\d{5,8}", normalized_digits) and (
            "руб" in text or "заказ" in text):
        return "offer"
    return "other"


def accept_offer_dialog(a, lines, text):
    """Соглашается с предложением нового задания и помечает его для «Активных»."""
    global _OFFERS_TAKEN
    if forbidden_reason(text):
        # Иностранный заказ («Сменить учетную запись») брать нельзя.
        log(f"диалог предложения: {forbidden_reason(text)} — пропускаю")
        close_modal(a, lines)
        return None
    limit = offer_limit()
    if limit and _OFFERS_TAKEN >= limit:
        # Больше брать нельзя: если задания нечем выполнять (например, вход
        # в «Активные» не работает), заказы копятся и сгорают. Пропускаем.
        pos = find_word(lines, "пропустить") or find_word(lines, "пропуск")
        if pos:
            log(f"лимит предложений за прогон ({limit}) — пропускаю окно")
            tap(*pos)
            time.sleep(0.6)
            return "skipped"
    bar = find_green_bar(a, 900, 1800, 300)
    # В новой сборке зелёной полосы может не быть вообще — ищем кнопку по
    # тексту ДО проверки полосы, иначе предложение пропускается.
    # Ищем «Начать» по словам всех строк (в т.ч. обрезанных), а не только
    # по готовым подписям — OCR часто разносит кнопку по отдельному слову.
    pos = find_word(lines, "начать") or find_word(lines, "взять") or find_word(lines, "принять")
    if not pos:
        for _line in lines:
            for _word in _line.get("words", []):
                if norm(_word.get("t", "")).strip(" !:.,") in ("начать", "взять", "принять"):
                    pos = (_word["x"] + int(_word.get("w", 0)) // 2,
                           _word["y"] + int(_word.get("h", 0)) // 2)
                    break
            if pos:
                break
    if not bar and not pos:
        # Кнопки модалки иногда не читаются OCR (обрезка по высоте). Тогда
        # «Начать» — правовая кнопка рядом с «Пропустить»: тапаем по ней.
        skip = find_word(lines, "пропустить") or find_word(lines, "пропуск")
        if skip and SERIAL == "238a1a84440b7ece":
            pos = (min(900, skip[0] + 300), skip[1])
            log(f"диалог предложения: зелёной кнопки и подписи нет, тапаем рядом с «Пропустить» {pos}")
    if not bar and not pos:
        # Кнопки модалки有时 не читаются OCR, а в новой сборке «Начать»
        # может отсутствовать — тогда задание берётся тапом по карточке
        # заказа внутри окна (цена/номер справа от заголовка).
        normalized = (text.replace("З", "3").replace("з", "3")
                      .replace("О", "0").replace("о", "0"))
        m = re.search(r"(?<!\d)\d{5,8}(?!\d)", normalized)
        if m:
            header = find_word(lines, "заказ") or find_word(lines, "заказа")
            if header:
                pos = (min(900, header[0] + 260), header[1] + 120)
                log(f"диалог предложения: тап по карточке заказа {pos}")
    if not bar and not pos:
        # В этой сборке предложение иногда приходит без кнопки «Начать» —
        # тогда это уведомление о задании, которое всё равно останется в
        # «Новых». Закрываем окно по фиолетовой «Пропустить» (координата
        # проверена на Samsung 1080×2412), чтобы не застрять в модалке.
        skip_y = int(a.shape[0] * 0.738) if a is not None else 1779
        log(f"диалог предложения: кнопки «Начать» нет — закрываю окно тапом ({540},{skip_y})")
        tap(540, skip_y)
        time.sleep(0.5)
        return "skipped"
    label = green_label(lines, bar)
    # В новой сборке зелёная полоса — это фон, а сама кнопка «Начать» —
    # текст на ~50 px ниже центра полосы. Поэтому сначала ищем слово
    # и тапаем по нему, а полосу используем только как запасной вариант.
    if pos:
        log(f"диалог предложения: «{label}», принимаю по слову {pos}")
        tap(*pos)
    else:
        log(f"диалог предложения: «{label}», принимаю ({bar[1]},{bar[2]})")
        tap(bar[1], bar[2])
    _OFFERS_TAKEN += 1
    log(f"принято предложений за прогон: {_OFFERS_TAKEN}/{offer_limit()}")
    try:
        with open(OFFER_PATH, "w", encoding="utf-8") as file:
            file.write(text)
    except OSError as exc:
        log(f"не удалось записать маркер предложения: {exc}")
    # Сразу после согласия приложение может показать напоминание об этом же
    # заказе — его нужно принять, иначе окно останется висеть.
    end = time.monotonic() + 8
    while time.monotonic() < end:
        path = frame_path("offer_after.png")
        a2 = shot(path)
        if a2 is None:
            time.sleep(0.3)
            continue
        kind = screen_kind(a2)
        if kind == "modal":
            lines2 = modal_lines(a2, path)
            text2 = " | ".join(line["text"] for line in lines2)
            bar2 = find_green_bar(a2, 900, 1800, 300)
            if modal_kind(text2, green_label(lines2, bar2)) == "reminder":
                accept_open_dialog(a2, lines2, text2)
                return "offer"
            close_modal(a2, lines2)
            return "offer"
        if kind in ("detail", "active"):
            return "offer"
        time.sleep(0.3)
    return "offer"


def third_party_packages():
    out = adb("shell", "pm list packages -3").stdout.decode("utf-8", "replace")
    return [line.split(":", 1)[1].strip() for line in out.splitlines()
            if line.startswith("package:")]


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


def install_place(text):
    """Номер места из инструкции «—8 место» (по умолчанию 1)."""
    match = re.search(r"[-—–]?\s*(\d{1,2})\s*мест", norm(text))
    return max(1, int(match.group(1))) if match else 1


def installed_packages_set():
    """Все установленные пакеты (не только сторонние — магазины системные)."""
    out = adb("shell", "pm list packages").stdout.decode("utf-8", "ignore")
    return {line.split(":", 1)[1].strip() for line in out.splitlines()
            if line.startswith("package:")}


def instruction_icon(a, lines, bar=None):
    """Иконка из модалки-инструкции: она под строкой «…с иконкой:»."""
    if a is None:
        return None
    height, width = a.shape[:2]
    target = next((line for line in (lines or [])
                   if "икон" in norm(line.get("text", ""))), None)
    if target is None:
        return None
    y0 = int(target.get("y", 0)) + 30
    # Кнопка «ОК» должна быть ниже иконки: первая зелёная полоса модалки —
    # «Скопировать в буфер», она выше и для обрезки не годится.
    if bar is not None and int(bar[2]) > y0 + 50:
        y1 = int(bar[2]) - 15
    else:
        found = find_green_bar(a, y0 + 30, min(height, y0 + 340), 200)
        y1 = found[2] - 15 if found else y0 + 200
    if y1 - y0 < 50:
        return None
    x0, x1 = max(0, width // 2 - 120), min(width, width // 2 + 120)
    return a[y0:y1, x0:x1]


def crop_app_icon(image):
    """Обрезает однотонные поля вокруг цветной иконки."""
    if image is None or not getattr(image, "size", None):
        return image
    mx = image.max(axis=2).astype(np.int16)
    mn = image.min(axis=2).astype(np.int16)
    mask = ((mx - mn) > 35) & (mx > 70)
    ys, xs = np.where(mask)
    if not len(xs):
        return image
    return image[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def icon_match_score(left, right):
    """Средняя разница нормализованных сигнатур (меньше — иконки ближе)."""
    a = icon_signature(crop_app_icon(left))
    b = icon_signature(crop_app_icon(right))
    if a.std() < 3 or b.std() < 3:
        return 9.0
    a = (a - a.mean()) / (a.std() + 1e-6)
    b = (b - b.mean()) / (b.std() + 1e-6)
    return float(np.abs(a - b).mean())


def pkg_from_icon(icon):
    """Пакет из кэша иконок Swift Backup по иконке из модалки."""
    import tasks
    if icon is None:
        return None
    try:
        vector = tasks.icon_vector(Image.fromarray(crop_app_icon(icon)))
    except Exception:  # noqa: BLE001 - иконка могла быть слишком маленькой
        return None
    if vector is None:
        return None
    scored = sorted((float(((vector - value) ** 2).mean()), pkg)
                    for pkg, value in tasks.icon_index().items())
    if not scored:
        return None
    best, pkg = scored[0]
    second = scored[1][0] if len(scored) > 1 else 99.0
    if best < 1.0 and best < 0.75 * second:
        return pkg
    return None


def store_search_command(store, query):
    """Команда adb, открывающая поиск по запросу внутри нужного магазина."""
    quoted = urllib.parse.quote(query or "")
    if store == "huawei":
        # Resolve-activity на fab1701 подтверждает appmarket://search →
        # com.huawei.appmarket (ThirdApiWithAffinityActivity).
        return ["shell", "am", "start", "-a", "android.intent.action.VIEW",
                "-d", f"appmarket://search?q={quoted}"]
    if store == "xiaomi":
        # На Realme GetApps имеет пакет com.xiaomi.mipicks, а не
        # com.xiaomi.market. Без -p Android показывает chooser вместо поиска.
        packages = installed_packages_set()
        target = ("com.xiaomi.market" if "com.xiaomi.market" in packages
                  else "com.xiaomi.mipicks")
        return ["shell", "am", "start", "-a", "android.intent.action.VIEW",
                "-d", f"market://search?q={quoted}", "-p", target]
    if store == "rustore":
        return ["shell", "am", "start", "-a", "android.intent.action.VIEW",
                "-d", f"https://www.rustore.ru/catalog/search?q={quoted}"]
    return None


def store_result_rows(a, lines=None, icon=None):
    """Строки результатов поиска в Huawei/Xiaomi/RuStore.

    В отличие от Google Play, у этих магазинов нет шевронов в правой полосе:
    ищем цветные иконки приложений в левой колонке. Если иконки распознать
    не удалось, откатываемся на OCR-строки с «Установить»/ценой.
    """
    rows = []
    if a is not None and a.ndim == 3 and a.shape[2] >= 3:
        height, width = a.shape[:2]
        x0, x1 = int(width * 0.03), int(width * 0.28)
        y0, y1 = int(height * 0.14), int(height * 0.90)
        band = a[y0:y1, x0:x1]
        mx = band.max(axis=2).astype(np.int16)
        mn = band.min(axis=2).astype(np.int16)
        colored = ((mx - mn) > 45) & (mx > 95)
        active = colored.sum(axis=1) >= 45
        groups, start = [], None
        for index, flag in enumerate(active):
            if flag and start is None:
                start = index
            elif not flag and start is not None:
                if index - start >= 55:
                    groups.append((start, index))
                start = None
        if start is not None and len(active) - start >= 55:
            groups.append((start, len(active)))
        for ys, ye in groups:
            columns = np.flatnonzero(colored[ys:ye].sum(axis=0) >= max(8, (ye - ys) // 4))
            if not len(columns):
                continue
            c0, c1 = int(columns.min()), int(columns.max()) + 1
            if not (55 <= c1 - c0 <= 230) or not (55 <= ye - ys <= 230):
                continue
            rows.append({
                "y": y0 + (ys + ye) // 2,
                "x": x0 + (c0 + c1) // 2,
                "icon": a[y0 + ys:y0 + ye, x0 + c0:x0 + c1],
            })
    if rows:
        rows.sort(key=lambda row: row["y"])
        return rows
    # Фолбэк по тексту: признак результата — кнопка «Установить» или цена.
    candidates = []
    for line in lines or []:
        text = norm(line.get("text", ""))
        y = int(line.get("y", 0))
        if not (250 <= y <= 1750):
            continue
        if "установить" in text or re.search(r"\d+[.,]\d+\s*(руб|₽)", text):
            candidates.append((y, int(line.get("x", 0))))
    candidates.sort()
    for y, x in candidates:
        if not rows or y - rows[-1]["y"] > 90:
            rows.append({"y": y, "x": x, "icon": None})
    return rows


def store_install_by_query(order, store, query, place=1, icon=None, wait=120):
    """Ищет запрос в нужном магазине и ставит результат по месту/иконке."""
    import tasks
    before = set(third_party_packages())
    args = store_search_command(store, query)
    if args is None:
        log(f"{store}: {order}: неизвестный магазин — пропускаю")
        return None
    log(f"{store}: {order}: ищу «{query}» (место {place})")
    adb(*args)
    time.sleep(5)
    found = None
    end = time.monotonic() + 25
    while time.monotonic() < end and not stop_requested():
        path = frame_path("msearch.png")
        a = shot(path)
        if a is None:
            time.sleep(1)
            continue
        lines = ocr(path)
        rows = store_result_rows(a, lines, icon)
        if rows:
            found = (a, lines, rows)
            break
        time.sleep(1)
    if not found:
        log(f"{store}: {order}: список результатов не распознан")
        return None
    _a, lines, rows = found
    if place > len(rows):
        # Ставить последнее попавшееся ОПАСНО: AdvertApp сверяет иконку и
        # следовательно шаг не подтверждается. Лучше пропустить заказ.
        log(f"{store}: {order}: место {place} больше списка ({len(rows)}) — пропускаю установку")
        return None
    index = place - 1
    if icon is not None:
        scores = [icon_match_score(icon, row["icon"]) if row["icon"] is not None
                  else 9.0 for row in rows]
        best = min(range(len(rows)), key=lambda i: scores[i])
        if scores[best] < 0.55 and best != index:
            log(f"{store}: {order}: иконка указывает на место {best + 1} вместо {place} — выбираю по иконке")
            index = best
    row = rows[index]
    tap_x = min(int(row["x"]) + 150, 860)
    log(f"{store}: {order}: открываю результат №{index + 1} (x={tap_x}, y={row['y']})")
    tap_any(tap_x, int(row["y"]))
    time.sleep(3)
    target = None
    end = time.monotonic() + 30
    while time.monotonic() < end and not stop_requested():
        path = frame_path("mdetail.png")
        a = shot(path)
        if a is None:
            time.sleep(0.5)
            continue
        detail_lines = ocr(path)
        target = find_word(detail_lines, "установить")
        if target is None:
            buttons = play_install_lines(detail_lines)
            if buttons:
                target = (540, int(buttons[0]["y"]))
        if target is not None:
            log(f"{store}: {order}: «Установить» {target}")
            tap_any(*target)
            break
        time.sleep(1)
    if target is None:
        log(f"{store}: {order}: кнопка «Установить» не найдена")
        return None
    expected = pkg_from_icon(icon)
    end = time.monotonic() + wait
    while time.monotonic() < end and not stop_requested():
        new = set(third_party_packages()) - before
        if new:
            pkg = sorted(new)[0]
            log(f"{store}: {order}: установлено — {pkg}")
            return pkg
        if expected and tasks.installed(expected):
            log(f"{store}: {order}: установлено (ожидаемый пакет) — {expected}")
            return expected
        time.sleep(3)
    log(f"{store}: {order}: установка не подтвердилась")
    return None


def install_by_query(order, text, a=None, lines=None, bar=None):
    """Ставит приложение из инструкции в магазине, который требует задание."""
    import tasks
    query = extract_search_query(text)
    if not query:
        log("инструкция: поисковый запрос не распознан")
        return None
    store = tasks.detect_store(text)
    place = install_place(text)
    log(f"инструкция: магазин={tasks.STORE_LABELS.get(store, store)} запрос=«{query}» место={place}")
    if store == "google":
        return play_install_by_query(order, query, place)
    return store_install_by_query(order, store, query, place,
                                  instruction_icon(a, lines, bar))


def play_result_rows(a, x0=0.86, x1=0.96, bright=120, min_len=5):
    """Y-центры шевронов (v) в списке результатов Google Play."""
    if a is None:
        return []
    h, w = a.shape[:2]
    strip = a[:, int(w * x0):int(w * x1)]
    m = strip.max(axis=2) if strip.ndim == 3 else strip
    top, bottom = int(h * 0.15), int(h * 0.86)
    flags = (m[top:bottom] > bright).sum(axis=1) >= 3
    rows, start = [], None
    for y, flag in enumerate(flags):
        if flag and start is None:
            start = y
        elif not flag and start is not None:
            if y - start >= min_len:
                rows.append(top + (start + y - 1) // 2)
            start = None
    if start is not None and len(flags) - start >= min_len:
        rows.append(top + (start + len(flags) - 1) // 2)
    merged = []
    for y in rows:
        if merged and y - merged[-1] <= 6:
            merged[-1] = (merged[-1] + y) // 2
        else:
            merged.append(y)
    return merged


def play_install_lines(lines):
    """Строки с кнопкой «Установить» на странице приложения."""
    out = []
    for line in lines:
        text = norm(line["text"])
        if "установ" in text and "установлено" not in text and "открыть" not in text:
            out.append(line)
    out.sort(key=lambda line: line["y"])
    return out


def play_install_by_query(order, query, place=1, wait=120):
    """Открывает Google Play по запросу, открывает N-й результат и ставит приложение."""
    before = set(third_party_packages())
    url = "market://search?q=" + urllib.parse.quote(query)
    log(f"play: {order}: ищу «{query}» (место {place})")
    adb("shell",
        f'am start -a android.intent.action.VIEW -d "{url}" -p com.android.vending')
    time.sleep(5)
    rows = []
    end = time.monotonic() + 20
    while time.monotonic() < end and not stop_requested():
        path = frame_path("play.png")
        a = shot(path)
        if a is not None:
            rows = play_result_rows(a)
            if rows:
                break
        time.sleep(1)
    if not rows:
        log(f"play: {order}: список результатов не распознан")
        return None
    if place > len(rows):
        # Место из инструкции больше видимого списка: ставить последнее
        # попавшееся приложение ОПАСНО — это почти всегда не та иконка, и
        # AdvertApp потом не подтверждает заказ (instructions_failed), сжигая
        # время шквала. Лучше пропустить и дождаться запроса с меньшим местом.
        log(f"play: {order}: место {place} больше списка ({len(rows)}) — пропускаю установку")
        return None
    y = rows[place - 1]
    log(f"play: {order}: открываю результат №{place} (y={y})")
    tap_any(540, y)
    time.sleep(3)
    tapped = False
    end = time.monotonic() + 25
    while time.monotonic() < end and not stop_requested():
        path = frame_path("play_detail.png")
        a = shot(path)
        if a is None:
            time.sleep(0.5)
            continue
        buttons = play_install_lines(ocr(path))
        if buttons:
            target = buttons[0]
            log(f"play: {order}: «Установить» ({target['x']},{target['y']})")
            tap_any(540, int(target["y"]))
            tapped = True
            break
        time.sleep(1)
    if not tapped:
        log(f"play: {order}: на странице приложения нет кнопки «Установить»")
        return None
    end = time.monotonic() + wait
    while time.monotonic() < end and not stop_requested():
        new = set(third_party_packages()) - before
        if new:
            pkg = sorted(new)[0]
            log(f"play: {order}: установлено — {pkg}")
            return pkg
        time.sleep(3)
    log(f"play: {order}: установка не подтвердилась")
    return None


def hold_foreground(pkg, seconds):
    """Держит установленное приложение на экране, перезапуская при потере."""
    end = time.monotonic() + seconds
    lost = 0
    while time.monotonic() < end and not stop_requested():
        time.sleep(2.0)
        if pkg in focus():
            lost = 0
            continue
        lost += 1
        if lost >= 3:
            log(f"инструкция: {pkg} потерял фокус — запускаю снова")
            adb("shell", f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
            lost = 0


def install_from_instruction(order, a, lines, text):
    """Жмёт кнопку инструкции и ставит приложение в магазине из задания."""
    import tasks
    bar = find_green_bar(a, 900, 1800, 300)
    if not bar:
        log("инструкция: зелёная кнопка не найдена")
        return None
    label = green_label(lines, bar)
    store = tasks.detect_store(text)
    log(f"инструкция: «{label}», открываю {tasks.STORE_LABELS.get(store, store)} ({bar[1]},{bar[2]})")
    tap(bar[1], bar[2])
    time.sleep(4)
    return install_by_query(order, text, a, lines, bar)


def install_step_confirmed(order, timeout=25):
    """AdvertApp действительно принял шаг: появился зелёный шаг «Найти/Открыть»."""
    import tasks
    end = time.monotonic() + timeout
    while time.monotonic() < end and not stop_requested():
        kind, a, path, lines = read_screen("install_confirm.png")
        if a is None:
            time.sleep(1)
            continue
        text = norm(" ".join(line["text"] for line in lines))
        pos = find_word(lines, "подтвердить")
        bar = find_green_bar(a, 600, 2260, 400)
        if kind == "modal" or pos is not None:
            if pos is not None:
                tap_any(*pos)
            elif bar:
                tap(bar[1], bar[2])
            time.sleep(2)
            continue
        if any(word in text for word in ("загрузить из", "поисковый запрос",
                                         "установите приложение с иконкой")):
            log(f"инструкция: заказ {order} — AdvertApp не подтвердил установку")
            return False
        if kind == "menu":
            # После установки am start возвращает на главное меню AdvertApp:
            # зелёный шаг появится в «Активных», а не здесь. Требувать его
            # на меню — ложный instructions_failed при успешной установке.
            log(f"инструкция: заказ {order} — установка сделана, ждём зелёный шаг в «Активных»")
            return True
        for line in lines:
            low = norm(line.get("text", ""))
            if not any(word in low for word in ("найти приложение", "открыть приложение",
                                                "найти ", "открыть ")):
                continue
            if tasks.step_text_active(a, line) or tasks.step_state(a, line) == "pending":
                log(f"инструкция: заказ {order} — зелёный шаг: {line['text']!r}")
                return True
        time.sleep(1)
    log(f"инструкция: заказ {order} — зелёный шаг не появился")
    return False


def confirm_after_install(order, pkg):
    """Возвращается в AdvertApp и проверяет подтверждение шага, а не «любой тап»."""
    adb("shell", "am start -n " + main_activity(active_app()))
    time.sleep(2)
    confirmed = install_step_confirmed(order)
    if pkg and confirmed:
        hold_foreground(pkg, hold_min())
    return confirmed


def handle_dialog(order, a, path, stage, navigation=False):
    log(f"handle_dialog: order={order} stage={stage}")
    lines = modal_lines(a, path)
    text = " | ".join(line["text"] for line in lines)
    bar = find_green_bar(a, 900, 1800, 300)
    kind = modal_kind(text, green_label(lines, bar))
    log(f"модалка: {kind}")
    if kind == "uninstall":
        result = accept_uninstall_dialog(a, lines, text)
        if result:
            record_result(order, result, text, path, stage)
            log(f"handle_dialog done: order={order} status={result} text={text!r}")
            if result == "uninstall_failed":
                # Пакет не найден/не удалён — подтверждать нечего. Если оставить
                # окно открытым, MODAL_PUMP будет разбирать его бесконечно.
                close_modal(a, lines)
            return result
    if kind == "not_enough_time":
        result = retry_hold_dialog(a, lines, text)
        record_result(order, result, text, path, stage)
        log(f"handle_dialog done: order={order} status={result} text={text!r}")
        return result
    if kind == "instructions":
        pkg = install_from_instruction(order, a, lines, text)
        if not pkg:
            record_result(order, "instructions_failed", text, path, stage)
            log(f"handle_dialog done: order={order} status=instructions_failed (установка не удалась)")
            close_modal(a, lines)
            return "instructions_failed"
        if not confirm_after_install(order, pkg):
            record_result(order, "instructions_failed", text, path, stage)
            log(f"handle_dialog done: order={order} status=instructions_failed pkg={pkg} (AdvertApp не подтвердил)")
            close_modal(a, lines)
            return "instructions_failed"
        # Это только установка: заказ остаётся в «Активных», где EXECUTE_ACTIVE
        # должен найти зелёный шаг «Найти/Открыть», открыть приложение и держать
        # его 65–80 с. Сам заказ считается выполненным позже, по подтверждению.
        record_result(order, "install_done", text, path, stage)
        log(f"handle_dialog done: order={order} status=install_done pkg={pkg} (ждём зелёный шаг в «Активных»)")
        return "install_done"
    if kind == "offer":
        accept_offer_dialog(a, lines, text)
        record_result(order, "offer", text, path, stage)
        log(f"handle_dialog done: order={order} status=offer text={text!r}")
        return "offer"
    if kind == "reminder":
        special = accept_open_dialog(a, lines, text)
        if special:
            record_result(order, special, text, path, stage)
            log(f"handle_dialog done: order={order} status={special} text={text!r}")
            return special
    status = response_status(text)
    record_result(order, status, text, path, stage)
    log(f"handle_dialog done: order={order} status={status} text={text!r}")
    if status == "accepted" and not navigation:
        # Если заказ подтверждён — закрываем и возвращаем сигнал перезахода.
        log(f"заказ {order} взят -> перезаход для обновления")
        return "accepted"
    # Навигационная модалка без рабочей кнопки: не считаем её принятой,
    # иначе окно висит и цикл упирается в него.
    if status == "accepted":
        log("модалка навигации: кнопка не сработала — закрываю")
    close_modal(a, lines)
    return "closed" if status == "accepted" else status


def attempt_order(card, path, deadline):
    order = card["order"]
    if path and os.path.isfile(path):
        shutil.copyfile(path, os.path.join(TMP, f"ru_{datetime.now():%H%M%S}_{order}.png"))
    log(f"RU candidate: {order} top={card['top']} title={card['title']}")
    # Перед нажатием проверяем, не перекрыт ли список всплывающим окном:
    # окно с предложением нового заказа глотает нажатие, и карточка
    # не открывается — причина, по которой бот «листал и не брал».
    for _ in range(3):
        kind, a, response_path, lines = read_screen("pre.png", check_focus=False)
        if kind == "modal":
            status = handle_dialog(order, a, response_path, "open")
            if status == "offer":
                mark_new_taken(order)
            return status
        if kind == "list":
            break
        time.sleep(0.3)
    tap(650, card["top"] + 60)
    wait_end = time.monotonic() + min(45, remaining(deadline))
    detail = None
    list_waits = 0
    while time.monotonic() < wait_end and not stop_requested():
        kind, a, response_path, lines = read_screen("d.png")
        if kind == "modal":
            status = handle_dialog(order, a, response_path, "open")
            if status == "offer":
                mark_new_taken(order)
            return status
        if kind == "list":
            # Карточка не открылась: нажатие, скорее всего, перехватило
            # всплывающее окно. Повторяем нажатие по актуальным координатам.
            list_waits += 1
            if list_waits >= 3:
                target = next((c for c in analyze_cards(a, lines)
                               if order_matches(order, c["order"])), None)
                if target is None:
                    # Карточка пропала с экрана (список пересортировался).
                    # Отдаём отдельный статус: вызывающий найдёт заказ заново.
                    log(f"{order}: карточка пропала с экрана — нужен повторный поиск")
                    record_result(order, "not_visible", "Карточка не видна на экране", response_path, "open")
                    return "not_visible"
                log(f"{order}: карточка не открылась — повторяю нажатие")
                tap(650, target["top"] + 60)
                list_waits = 1
            time.sleep(0.15)
            continue
        list_waits = 0
        detail_like = (kind == "detail")
        if not detail_like and kind in ("other", "list", "active"):
            # screen_kind иногда ошибается на странице заказа (например,
            # "Найти приложение"/клавиатура/смещённая кнопка). Если виден
            # заголовок заказа и признаки карточки — это detail.
            rows = merge_rows(lines)
            has_order = any(is_order_header(row) and row.get("y", 0) >= 200 for row in rows)
            if has_order and (download_button(a, lines) is not None
                              or any(any(w in norm(row.get("text", "")) for w in ("найти приложение", "открыть приложение", "открыть на", "загрузить"))
                                     for row in rows)):
                detail_like = True
        if detail_like:
            rows = merge_rows(lines)
            if any(is_order_header(row) for row in rows) or download_button(a, lines) is not None:
                detail = a, response_path, lines
                break
            time.sleep(0.2)
            continue
        if kind != "list":
            heads = [row for row in merge_rows(lines) if is_order_header(row)]
            text = " ".join(line["text"] for line in lines)
            reason = forbidden_reason(text) if len(heads) <= 1 else None
            if reason:
                record_result(order, reason, text, response_path, "open")
                log(f"быстрый отсев: {order} -> {reason}")
                key_back()
                time.sleep(0.2)
                return reason
        time.sleep(0.15)
    if detail is None:
        record_result(order, "unknown", "Страница задания не открылась за 45 с", stage="open")
        return "unknown"
    a, response_path, lines = detail
    rows = merge_rows(lines)
    headers = [row for row in rows if is_order_header(row) and row["y"] >= 250]
    actual = order_number(headers[0]) if headers else None
    text = " ".join(row["text"] for row in rows)
    if not same_order(order, actual):
        reason = forbidden_reason(text)
        if actual is None and not reason and download_button(a, lines) is not None:
            # Номер не распознан, но страница задания открылась — работаем
            # с заказом, по которому кликнули.
            log(f"номер заказа не распознан на странице, продолжаю: {order}")
        elif actual and not reason:
            log(f"mismatch: ожидали {order}, открылся {actual} — обрабатываю фактический")
            order = actual
        else:
            record_result(order, "mismatch", f"На странице другой номер: {actual}", response_path, "open")
            key_back()
            return "mismatch"
    reason = forbidden_reason(text)
    if reason:
        record_result(order, reason, text, response_path, "detail")
        return reason
    button = download_button(a, lines)
    if button is None:
        record_result(order, "unknown", "Не распознана кнопка разрешённого магазина", response_path, "detail")
        return "unknown"
    if remaining(deadline) <= 0 or stop_requested():
        return "unknown"
    tap(*button)
    wait_end = time.monotonic() + min(10, remaining(deadline))
    while time.monotonic() < wait_end and not stop_requested():
        current = focus()
        market = next((name for package, name in MARKETS.items() if package in current), None)
        if market:
            record_result(order, "market_opened", market)
            key_back()
            time.sleep(0.3)
            return "market_opened"
        if not app_in_focus(current):
            raise RuntimeError("После кнопки магазина появился посторонний экран; остановлено")
        g_path = frame_path("g.png")
        a = shot(g_path)
        if a is not None and screen_kind(a) == "modal":
            status = handle_dialog(order, a, g_path, "submit")
            if status == "offer":
                mark_new_taken(order)
            return status
        # Иногда Confirmation приходит как диалог без яркой зелёной полосы —
        # screen_kind видит его как "other". Проверяем OCR-текст на слова
        # подтверждения, чтобы не терять заказ по «не подтверждён».
        if a is not None and screen_kind(a) in ("other", "detail", "list"):
            g_lines = ocr(g_path)
            g_text = norm(" ".join(line["text"] for line in g_lines))
            if (("взять" in g_text or "принять" in g_text or "начать" in g_text)
                    and ("заказ" in g_text or "задание" in g_text)):
                status = handle_dialog(order, a, g_path, "submit")
                if status == "offer":
                    mark_new_taken(order)
                return status
        time.sleep(0.2)
    record_result(order, "unknown", "Ответ на нажатие не подтверждён", stage="submit")
    return "unknown"


def run(start_at=None, end_at=None, *, duration=600, dry_run=False, refresh_seconds=None,
        start_datetime=None, deadline_datetime=None, idle_stop=0.0):
    start, deadline = schedule_times(start_at, end_at, duration=duration)
    start = start_datetime or start
    deadline = deadline_datetime or deadline
    rush_start, rush_end = rush_window(start)
    # Новый прогон отменяет старый per-device «стоп», но не глобальный.
    if STOP_PATH != GLOBAL_STOP_PATH and os.path.exists(STOP_PATH):
        try:
            os.remove(STOP_PATH)
        except OSError:
            pass
    log(f"=== hunter2 {'dry-run' if dry_run else 'start'} [{SERIAL}]: "
        f"{start:%d.%m %H:%M:%S} - {deadline:%d.%m %H:%M:%S} ===")
    prepare_device()
    prune_frames()
    log(f"rush: {rush_start:%H:%M:%S} — {rush_end:%H:%M:%S}; только перезаходы для обновления")
    while datetime.now() < start and not stop_requested():
        time.sleep(min(0.5, (start - datetime.now()).total_seconds()))
    if remaining(deadline) <= 0 or stop_requested():
        return
    # Только проверка геометрии: wm density/size и таймаут экрана не меняются.
    density = adb("shell", "wm density").stdout.decode("utf-8", "replace")
    match = re.search(r"Override density:\s*(\d+)", density)
    if not match or int(match.group(1)) != 408:
        raise RuntimeError("Нужна пользовательская density 408; автоматически менять её не буду")
    if not restart_app(deadline):
        log("Приложение не готово; прогон завершён без нажатий на задания")
        return
    # На старте приложение может висеть на splash: ждём меню без проверки
    # фокуса — это нужно только уже в действиях.
    log("ожидание меню")
    for _ in range(6):
        if stop_requested():
            return
        kind, a, path, lines = read_screen("ready.png", check_focus=False)
        if kind == "menu":
            log("меню видно")
            break
        time.sleep(0.6)
    else:
        log("меню не появилось, пробуем ещё раз")
        if not restart_app(deadline):
            return
    if open_new(timeout=min(open_wait_seconds(), remaining(deadline)))[0] is None:
        return
    settings = _load_panel_settings() or {}
    new_cfg = settings.get("new", {}) if isinstance(settings, dict) else {}
    try:
        recheck_count = max(0, int(new_cfg.get("recheck_count", 2)))
    except (TypeError, ValueError):
        recheck_count = 2
    try:
        recheck_delay = max(0.0, float(new_cfg.get("recheck_delay", 10)))
    except (TypeError, ValueError):
        recheck_delay = 10.0
    sort_by_price = bool(new_cfg.get("sort_by_price", True))
    try:
        idle_limit = float(new_cfg.get("idle_stop_minutes", idle_stop or 0))
    except (TypeError, ValueError):
        idle_limit = float(idle_stop or 0)
    try:
        max_pages = int(new_cfg.get("max_pages", 0) or 0)
    except (TypeError, ValueError):
        max_pages = 0
    scroll_to_bottom = bool(new_cfg.get("scroll_to_bottom", True))
    try:
        empty_stop = max(1, int(new_cfg.get("empty_stop", 2) or 2))
    except (TypeError, ValueError):
        empty_stop = 2
    pages_text = "все страницы" if max_pages <= 0 else f"первые {max_pages} страниц"
    log(f"стратегия «Новые»: {pages_text}, "
        f"листать до низа: {'да' if scroll_to_bottom else 'нет'}, "
        f"перепроверок {recheck_count} по {recheck_delay:.0f} с, "
        f"сортировка по цене: {'да' if sort_by_price else 'нет'}")
    maintenance = settings.get("maintenance", {}) if isinstance(settings, dict) else {}
    try:
        frames_age = max(5, int(maintenance.get("frames_max_age_minutes", 90)))
    except (TypeError, ValueError):
        frames_age = 90
    try:
        frames_files = max(50, int(maintenance.get("frames_max_files", 600)))
    except (TypeError, ValueError):
        frames_files = 600
    try:
        frames_mb = max(20, int(maintenance.get("frames_max_mb", 250)))
    except (TypeError, ValueError):
        frames_mb = 250
    prune_frames(frames_age, frames_files, frames_mb)
    try:
        blacklist_ttl = max(1, int(new_cfg.get("blacklist_ttl_minutes", 360)))
    except (TypeError, ValueError):
        blacklist_ttl = 360
    if _order_blacklist is not None:
        known_blocked = _order_blacklist.active_ids()
        log(f"временный ЧС: {len(known_blocked)} заданий" if known_blocked else "временный ЧС пуст")

    seen, blocked, skip_logged = {}, set(), set()
    last_refresh = time.monotonic()
    last_beat = 0.0
    last_ru = time.monotonic()
    last_prune = time.monotonic()
    recovery_count = 0
    modal_count = 0
    no_ru_scans = 0
    while remaining(deadline) > 0 and not stop_requested():
        if time.monotonic() - last_prune > 600:
            prune_frames(frames_age, frames_files, frames_mb)
            last_prune = time.monotonic()
        if idle_limit and time.monotonic() - last_ru > idle_limit * 60:
            log(f"нет RU-заданий дольше {idle_limit:.0f} мин — заканчиваю забор")
            break
        kind, a, path, lines = read_screen()
        if kind == "modal":
            modal_count += 1
            status = handle_dialog("unprompted", a, path, "navigation", navigation=True)
            if status == "offer":
                log("взято новое задание из модалки — переключаюсь на «Активные»")
                break
            if modal_count >= 4:
                log("Повторяющееся окно: прогон остановлен без дальнейших нажатий")
                break
            continue
        modal_count = 0
        if kind != "list":
            if open_new(timeout=min(open_wait_seconds(), remaining(deadline)))[0] is not None:
                recovery_count = 0
                continue
            recovery_count += 1
            if recovery_count > 2 or not restart_app(deadline):
                break
            open_new(timeout=min(open_wait_seconds(), remaining(deadline)))
            continue
        # Мы снова на списке «Новые» — сбрасываем счётчик, чтобы он считал
        # только подряд идущие сбои, а не все за двухчасовой забор.
        recovery_count = 0
        now = time.monotonic()
        blocked_ids = _order_blacklist.active_ids() if _order_blacklist else set()
        # Ищем задание и берём его сразу на том экране, где оно найдено:
        # сервер пересортировывает список «Новые», и повторный поиск
        # карточки по номеру её уже не находит (бот листал и не брал).
        current, total = page_state(a)
        limit = total if max_pages <= 0 else min(total, max_pages)
        card, card_path, cards = None, None, []
        for target_page in range(current, limit + 1):
            if target_page != current and not go_to_page(target_page):
                log(f"страница {target_page} «Новых» не открылась")
                break
            card, card_path, cards = scan_new_page(
                deadline=deadline, blocked_ids=blocked_ids, blocked=blocked,
                seen=seen, skip_logged=skip_logged, sort_by_price=sort_by_price,
                scroll_to_bottom=scroll_to_bottom, empty_stop=empty_stop, now=now)
            if card is not None:
                break
        if now - last_beat >= 10:
            last_beat = now
            log(f"scan: {len(cards)} карточек, ru={sum(c['ru'] for c in cards)}, "
                f"samsung={sum(c['samsung'] for c in cards)}, "
                f"cut={sum(c['cut'] for c in cards)}")
        ru_all = [c for c in cards if c["ru"]]
        # Задания из временного ЧС не считаем доступными: иначе «Новые» будут
        # крутить scan до «Стоп при простое», хотя брать нечего.
        if card is None:
            no_ru_scans += 1
            what = "доступных RU" if ru_all else "RU"
            log(f"в «Новых» нет {what} "
                f"({no_ru_scans}/{recheck_count + 1})")
            if no_ru_scans > recheck_count:
                log("перепроверки исчерпаны — выхожу из «Новых»")
                break
            time.sleep(recheck_delay)
            begin = time.monotonic()
            if open_new(force_reenter=True, timeout=min(open_wait_seconds(), remaining(deadline)))[0] is not None:
                last_refresh = time.monotonic()
                log(f"recheck: меню → Новые ({last_refresh - begin:.2f} с)")
            else:
                time.sleep(1.0)
            continue

        no_ru_scans = 0
        last_ru = now
        for c in cards:
            if c["order"] in blocked_ids and c["order"] not in skip_logged:
                log(f"ЧС: пропускаю {c['order']} (временный чёрный список)")
                skip_logged.add(c["order"])
        if not dry_run:
            log(f"выбираю: {card['order']} — {card.get('price', 0.0):.2f} ₽")
            seen[card["order"]] = now
            status = attempt_order(card, card_path, deadline)
            if status == "offer":
                log("взято новое задание из модалки — переключаюсь на «Активные»")
                break
            if status in ("samsung", "foreign", "unavailable", "accepted", "market_opened", "instructions"):
                blocked.add(card["order"])
            if status in ("unknown", "mismatch", "unavailable") and _order_blacklist is not None:
                # «Недоступен» — заказ точно снят, держим полный TTL.
                # Таймауты и несовпадение номера часто временные: не больше часа,
                # иначе хорошее задание выпадает из забора на весь день.
                ttl = blacklist_ttl if status == "unavailable" else min(blacklist_ttl, 60)
                try:
                    _order_blacklist.add(card["order"], minutes=ttl, reason=status)
                    log(f"ЧС: {card['order']} добавлен на {ttl} мин ({status})")
                except Exception as error:  # noqa: BLE001 - ЧС не должен ронять забор
                    log(f"ЧС: не удалось сохранить {card['order']}: {error!r}")
            if remaining(deadline) > 0 and not stop_requested():
                if open_new(force_reenter=True, timeout=min(open_wait_seconds(), remaining(deadline)))[0] is not None:
                    last_refresh = time.monotonic()
            continue

        # in_rush раньше не определялся → NameError при запуске без
        # --refresh-seconds. Основание берём из уже вычисленного окна rush,
        # а не из зашитого часа: границы приходят из rush_window(start).
        _now_t = datetime.now().time()
        in_rush = rush_start.time() <= _now_t <= rush_end.time()
        interval = refresh_seconds if refresh_seconds is not None else (REFRESH_SECONDS if in_rush else 25.0)
        if now - last_refresh >= interval:
            begin = time.monotonic()
            if open_new(force_reenter=True, timeout=min(open_wait_seconds(), remaining(deadline)))[0] is not None:
                last_refresh = time.monotonic()
                log(f"reenter: меню → Новые ({last_refresh - begin:.2f} с)")
            continue
        time.sleep(0.2)
    log("=== hunter2 stop ===")


def parse_t(s):
    return datetime.strptime(s, "%H:%M:%S").time()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Автозабор RU-заданий AdvertApp; RuStore/Huawei разрешены, Samsung исключён")
    parser.add_argument("start", nargs="?", type=parse_t, help="Время старта HH:MM:SS")
    parser.add_argument("end", nargs="?", type=parse_t, help="Время остановки HH:MM:SS")
    parser.add_argument("--duration", type=float, default=600, help="Длительность немедленного прогона в секундах")
    parser.add_argument("--dry-run", action="store_true", help="Только навигация и распознавание, без открытия/забора заданий")
    parser.add_argument("--refresh-seconds", type=float, help="Интервал между перезаходами (минимум 2 с), без учёта загрузки")
    parser.add_argument("--next-hour", action="store_true", help="Один прогон: ближайший час −30 с … +6 мин")
    parser.add_argument("--hours", type=int, default=1, help="Число часовых прогонов с --next-hour")
    parser.add_argument("--stop", action="store_true", help="Остановить бот через файл hunter.stop")
    parser.add_argument("--serial", help="Серийный номер устройства (для фермы)")
    parser.add_argument("--slot", type=int, help="Номер слота фермы (для логов)")
    parser.add_argument("--restore-display", action="store_true",
                        help="Вернуть оригинальные wm size/density и выйти")
    parser.add_argument("--idle-stop", type=float, default=0.0,
                        help="Остановиться, если RU-заданий нет столько минут (0 — не останавливаться)")
    args = parser.parse_args()
    if args.serial or args.slot is not None:
        configure_device(args.serial, args.slot)
    if args.restore_display:
        from core import display
        changed = display.restore(SERIAL, RUN_DIR)
        print(f"display restored: {SERIAL} ({'да' if changed else 'нечего менять'})")
        sys.exit(0)
    if args.stop:
        with open(STOP_PATH, "w", encoding="utf-8") as file:
            file.write("stop\n")
        print("Остановка запрошена. Новые нажатия на задания прекращаются.")
        sys.exit(0)
    if args.duration <= 0 or args.hours < 1 or args.hours > 24:
        parser.error("Длительность должна быть положительной; --hours от 1 до 24")
    if args.refresh_seconds is not None and args.refresh_seconds < 0.5:
        parser.error("Интервал обновления должен быть не меньше 0.5 с")
    if (args.next_hour and (args.start or args.end)) or (args.hours != 1 and not args.next_hour):
        parser.error("--hours требует --next-hour; время и --next-hour нельзя задавать одновременно")
    for stop_file in {STOP_PATH, GLOBAL_STOP_PATH}:
        if os.path.exists(stop_file):
            try:
                os.remove(stop_file)  # запуск явно отменяет прежний запрос остановки
            except OSError:
                pass
    try:
        if args.next_hour:
            mark = datetime.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            for index in range(args.hours):
                if stop_requested():
                    break
                current_mark = mark + timedelta(hours=index)
                run(dry_run=args.dry_run, refresh_seconds=args.refresh_seconds,
                    start_datetime=max(datetime.now(), current_mark - timedelta(seconds=30)),
                    deadline_datetime=current_mark + timedelta(minutes=6), idle_stop=args.idle_stop)
        else:
            run(args.start, args.end, duration=args.duration,
                dry_run=args.dry_run, refresh_seconds=args.refresh_seconds, idle_stop=args.idle_stop)
    except KeyboardInterrupt:
        log("Остановлено пользователем (Ctrl+C)")
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        log(f"Остановлено безопасно: {exc}")
        sys.exit(1)
    finally:
        _ocr_server.close()
