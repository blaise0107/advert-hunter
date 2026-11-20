"""Отладочный дамп OCR по кадру модалки (только чтение)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunter2 as h

path = sys.argv[1]
for line in h.ocr(path):
    print(line)
