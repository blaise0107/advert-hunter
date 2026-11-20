import sys, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
import hunter2 as h
import numpy as np
from PIL import Image
p = str(_Root(__file__).resolve().parents[1] / 'frames') + r"\ru_154641_1416706.png"
a = np.array(Image.open(p).convert("RGB")).astype(np.int16)
lines = h.ocr(p)
for l in lines:
    print(f"{l['y']:5} {l['x']:5}  {l['text']}")
print("---- cards ----")
for c in h.analyze_cards(a, lines):
    print(c["top"], c["order"], "foreign", c["foreign"], "icon", c["icon"], "samsung", c.get("samsung"), "cut", c["cut"], "RU", c["ru"])
