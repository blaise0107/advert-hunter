import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
import hunter2 as h
import numpy as np
from PIL import Image
p = str(_Root(__file__).resolve().parents[1] / 'shots') + r"\d480_list.png"
a = np.array(Image.open(p).convert("RGB")).astype(np.int16)
m = h.green_mask(a)
ys, xs = np.where(m)
sel = (xs > 780) & (xs <= 1060) & (ys > 300) & (ys < 2240)
ys, xs = ys[sel], xs[sel]
yy = np.unique(ys)
groups = []
s = pr = yy[0]
for y in yy[1:]:
    if y - pr > 15:
        groups.append((s, pr)); s = y
    pr = y
groups.append((s, pr))
for y0, y1 in groups:
    mm = (ys >= y0) & (ys <= y1)
    x0, x1 = int(xs[mm].min()), int(xs[mm].max())
    print("band", y0, y1, "h", y1-y0, "x", x0, x1, "w", x1-x0, "n", int(mm.sum()))
