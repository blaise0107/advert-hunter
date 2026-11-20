"""Эталон из карточки, чей отказ Samsung-only подтверждён сохранённым кадром."""
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunter2 as hunter

source = os.path.join(hunter.TMP, "ru_160735_1416706.png")
image = np.array(Image.open(source).convert("RGB")).astype(np.int16)
lines = hunter.ocr(source)
header = next(line for line in lines if "1416706" in line["text"])
icons = hunter.header_icons(image, header["y"], header["x"])
if len(icons) != 1:
    raise SystemExit(f"Неоднозначный эталон: найдено иконок {len(icons)}")
directory = os.path.join(hunter.BASE_DIR, "assets")
os.makedirs(directory, exist_ok=True)
path = os.path.join(directory, "galaxy_store.png")
if os.path.exists(path):
    raise SystemExit("Эталон уже существует; он не перезаписан")
Image.fromarray(icons[0].astype(np.uint8)).save(path)
print(path)
