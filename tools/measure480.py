import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
_Root = __import__("pathlib").Path  # Qwen: базовая директория для путей tools/
import hunter2 as h
import numpy as np
from PIL import Image
p = str(_Root(__file__).resolve().parents[1] / 'shots') + r"\d480_list.png"
a = np.array(Image.open(p).convert("RGB")).astype(np.int16)
print("rings:")
for r in h.find_rings(a):
    print(" ", r)
m = h.green_mask(a)
for y in range(2150, 2412, 20):
    row = m[y]
    xs = np.where(row)[0]
    if len(xs):
        print("y", y, "n", len(xs), "x", xs.min(), xs.max())
