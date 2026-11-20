#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OCR-сервер RapidOCR (кириллица) для фермы AdvertApp.

Протокол полностью повторяет ocr.ps1 -Server:
    на stdin — по одной JSON-строке {"path": "..."};
    на stdout — по одной JSON-строке со списком строк распознавания.

Формат строки совместим с тем, что ждут hunter2.py и tasks.py:
    {"text": str, "x": int, "y": int,
     "words": [маркер, текст строки], ...}

RapidOCR склеивает визуальную строку в один box, поэтому word[0] —
искусственный маркер: цвет кружка галочки/часов слева от текста. Без него
tasks.step_state() не сможет отличить выполненный шаг от текущего.

Запуск (обязательно интерпретатором из .venv-ocr):
    .venv-ocr\\Scripts\\python.exe -u ocr_rapid_server.py -Server
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

# ---- Ограничение потоков ONNX Runtime ----
# RapidOCR/onnxruntime по умолчанию задействует ВСЕ ядра (здесь 8) на каждый
# процесс, а серверов одновременно два (по одному на воркер) — из-за этого
# процессор стоит на 100%. Точность распознавания от числа потоков НЕ зависит,
# поэтому ограничиваем каждый сервер 2 потоками: 2 сервера × 2 потока = 4
# ядра вместо 8. Переменные MUST быть выставлены ДО импорта onnxruntime.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "ORT_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_var, "2")

# Информационные сообщения RapidOCR/onnxruntime уходят в stderr и не должны
# попадать в поток JSON-ответов (в ферме stderr всё равно закрыт).
logging.getLogger("rapidocr").setLevel(logging.ERROR)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR  # noqa: E402

# Пороги цвета скопированы из tasks.step_marker() — они должны совпадать,
# иначе маркер, классифицированный здесь, не будет понят ботом.
YELLOW = (180, 120, 120)   # r > 180, g > 120, b < 120
GREEN = (140, 120, 120)    # g > 140, r < 120, b < 120
MARKER_DX = 40             # кружок находится в 40 px слева от начала текста
MARKER_W = 30
MARKER_H = 36

_engine = None


def get_engine():
    """Движок создаётся один раз на весь процесс (загрузка моделей дорогая)."""
    global _engine
    if _engine is None:
        _engine = RapidOCR(params={
            "Rec.lang_type": LangRec.CYRILLIC,
            "Rec.model_type": ModelType.MOBILE,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            # Ограничиваем потоки ONNX Runtime: по умолчанию (-1) он берёт
            # все 8 ядер на каждый движок, а движков два — процессор на 100%.
            # Точность распознавания от числа потоков не зависит.
            "EngineConfig.onnxruntime.intra_op_num_threads": 2,
            "EngineConfig.onnxruntime.inter_op_num_threads": 2,
        })
    return _engine


def marker_word(rgb, x, y):
    """Слово-маркер: цвет кружка слева от строки.

    tasks.step_marker() берёт words[0], проверяет его цвет и требует не менее
    5% пикселей нужного цвета. Поэтому рамка маркера выбирается так, чтобы
    накрывать сам кружок (x-40 ... x-10, y ... y+36).
    """
    h, w = rgb.shape[:2]
    mx = max(0, int(x) - MARKER_DX)
    my = max(0, int(y))
    mw = max(0, min(MARKER_W, w - mx))
    mh = max(0, min(MARKER_H, h - my))
    token = "О"  # как Windows OCR читает пустой кружок: неизвестное состояние
    if mw and mh:
        flat = rgb[my:my + mh, mx:mx + mw].reshape(-1, 3).astype(int)
        red, green, blue = flat[:, 0], flat[:, 1], flat[:, 2]
        yellow = (red > YELLOW[0]) & (green > YELLOW[1]) & (blue < YELLOW[2])
        is_green = (green > GREEN[0]) & (red < GREEN[1]) & (blue < GREEN[2])
        need = max(3, int(flat.shape[0] * 0.05))
        if int(yellow.sum()) >= need:
            token = "◷"     # жёлтые часы — текущий шаг
        elif int(is_green.sum()) >= need:
            token = "✓"     # зелёная галочка — шаг выполнен
    return {"t": token, "x": mx, "y": my, "w": mw, "h": mh}


def recognize(path):
    """Вернуть список строк в формате Windows OCR-сервера."""
    # RapidOCR получает именно путь: тогда он сам корректно читает изображение
    # в ожидаемом порядке каналов. Для анализа цвета грузим копию отдельно.
    result = get_engine()(path)
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None)
    if boxes is None or texts is None or len(boxes) == 0:
        return []
    # imdecode вместо imread: imread на Windows не открывает пути с кириллицей.
    try:
        with open(path, "rb") as file:
            bgr = cv2.imdecode(np.frombuffer(file.read(), dtype=np.uint8),
                               cv2.IMREAD_COLOR)
    except OSError:
        bgr = None
    if bgr is None:
        return []
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    lines = []
    for i, text in enumerate(texts):
        text = str(text).strip()
        if not text:
            continue
        box = np.asarray(boxes[i])
        x = int(box[:, 0].min())
        y = int(box[:, 1].min())
        x1 = int(box[:, 0].max())
        y1 = int(box[:, 1].max())
        lines.append({
            "text": text,
            "x": x,
            "y": y,
            "words": [
                marker_word(rgb, x, y),
                {"t": text, "x": x, "y": y,
                 "w": max(1, x1 - x), "h": max(1, y1 - y)},
            ],
        })
    # Как и ocr.ps1: сверху вниз, при равном y — слева направо.
    lines.sort(key=lambda line: (line["y"], line["x"]))
    return lines


def main():
    parser = argparse.ArgumentParser(description="RapidOCR-сервер для AdvertApp")
    parser.add_argument("-Path", dest="path", default=None)
    parser.add_argument("-Server", dest="server", action="store_true")
    args = parser.parse_args()

    if not args.server:
        if not args.path:
            print("Укажите -Path или -Server", file=sys.stderr)
            return 2
        try:
            print(json.dumps(recognize(os.path.abspath(args.path)),
                             ensure_ascii=False))
        except Exception as exc:  # сбой движка не должен ронять процесс
            print(f"Ошибка RapidOCR: {exc}", file=sys.stderr)
            print("[]")
        return 0

    # Режим сервера: один ответ на каждую строку запроса.
    # Если воркер-родитель убит жёстким taskkill /F (atexit не вызывается),
    # его child process может остаться сиротой и продолжать жечь CPU. Каждые
    # несколько запросов сверяем ppid: сменился — значит нас подменили новым
    # сервером, выходим, чтобы не копить мёртвые копии движка.
    parent_pid = os.getppid()
    handled = 0
    for request in sys.stdin:
        request = request.strip()
        if not request:
            continue
        handled += 1
        if handled % 5 == 0 and os.getppid() != parent_pid:
            print("Родитель-воркер сменился — завершаю OCR-сервер",
                  file=sys.stderr)
            break
        try:
            data = json.loads(request)
            answer = recognize(os.path.abspath(data["path"]))
        except Exception as exc:
            print(f"Ошибка RapidOCR: {exc}", file=sys.stderr)
            answer = []
        print(json.dumps(answer, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
