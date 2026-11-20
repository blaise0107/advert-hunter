from PIL import Image
import numpy as np, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
_Root = __import__("pathlib").Path  # Qwen: базовая директория для путей tools/
import hunter2 as h

im = Image.open(str(_Root(__file__).resolve().parents[1] / 'shots') + r"\d480_list.png").convert("RGB")
a = np.array(im).astype(np.int16)
m = h.green_mask(a)
ys, xs = np.where(m)
sel = (xs > 820) & (xs <= 1040) & (ys > 300)
ys, xs = ys[sel], xs[sel]
yy = sorted(set(ys.tolist()))
groups=[]; s=pr=yy[0]
for y in yy[1:]:
    if y-pr>12: groups.append((s,pr)); s=y
    pr=y
groups.append((s,pr))
print("NEW list rings:")
for y0,y1 in groups:
    mm=(ys>=y0)&(ys<=y1)
    print("   band", y0, y1, "n", int(mm.sum()), "x", int(xs[mm].min()), int(xs[mm].max()))

im2 = Image.open(str(_Root(__file__).resolve().parents[1] / 'shots') + r"\act_detail.png").convert("RGB")
b = np.array(im2).astype(np.int16)
m2 = h.green_mask(b)
ys2, xs2 = np.where(m2)
sel2 = (xs2 > 820) & (xs2 <= 1040) & (ys2 > 300)
ys2, xs2 = ys2[sel2], xs2[sel2]
print("ACTIVE rings via green_mask:", len(ys2))
if len(ys2):
    yy2 = sorted(set(ys2.tolist()))
    g2=[]; s=pr=yy2[0]
    for y in yy2[1:]:
        if y-pr>12: g2.append((s,pr)); s=y
        pr=y
    g2.append((s,pr))
    for y0,y1 in g2:
        mm=(ys2>=y0)&(ys2<=y1)
        print("   band", y0, y1, "n", int(mm.sum()), "x", int(xs2[mm].min()), int(xs2[mm].max()))
