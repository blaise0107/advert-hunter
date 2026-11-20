"""Проверка вырезания иконки из модалки по сохранённому кадру (только чтение)."""
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hunter2 as h

path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "runs", "fab1701", "frames",
                    "result_20261120_081608_536560_1426335.png")
with Image.open(path) as image:
    a = np.array(image.convert("RGB"))
lines = h.ocr(path)
bar = h.find_green_bar(a, 900, 1800, 300)
icon = h.instruction_icon(a, lines, bar)
print("bar:", bar)
print("icon shape:", None if icon is None else icon.shape)
print("pkg по иконке:", h.pkg_from_icon(icon))
print("строки результата на модалке:", len(h.store_result_rows(a, lines, icon)))
