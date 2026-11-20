"""Диагностика совпадения иконки модалки с кэшем Swift Backup."""
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunter2 as h
import tasks as t

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
path = os.path.join(base, "runs", "fab1701", "frames",
                    "result_20261120_081608_536560_1426335.png")
with Image.open(path) as image:
    a = np.array(image.convert("RGB"))
lines = h.ocr(path)
icon = h.instruction_icon(a, lines)
crop = h.crop_app_icon(icon)
Image.fromarray(crop).save(os.path.join(base, "shots", "test_modal_icon.png"))
Image.fromarray(icon).save(os.path.join(base, "shots", "test_modal_icon_raw.png"))
print("crop shape:", crop.shape)
vec = t.icon_vector(Image.fromarray(crop))
print("vector:", None if vec is None else (vec.shape, round(float((vec**2).mean()), 3)))
scored = sorted((float(((vec - v) ** 2).mean()), pkg) for pkg, v in t.icon_index().items())
for score, pkg in scored[:8]:
    print(f"  {score:.3f} {pkg}")
print("яндекс:", [(round(s, 3), p) for s, p in scored if "yandex" in p][:8])
