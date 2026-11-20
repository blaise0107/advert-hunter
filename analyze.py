import os, sys, glob
from PIL import Image
import numpy as np

D = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\RZE\AppData\Local\Temp\opencode\obs"
files = sorted(glob.glob(os.path.join(D, "f_*.png")))
print("frames", len(files))

def analyze(path):
    im = Image.open(path).convert("RGB")
    a = np.array(im).astype(int)
    h, w, _ = a.shape
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    # dim overlay check at left edge
    dim = (a[600, 20].max() < 200) and (a[1500, 20].max() < 200)
    green = (g > 150) & (r < 130) & (b < 130) & ((g - r) > 70) & ((g - b) > 70)
    ys, xs = np.where(green)
    sel = (xs > 820) & (ys > 300)
    ys2, xs2 = ys[sel], xs[sel]
    centers = []
    if len(ys2):
        yy = np.unique(ys2)
        groups = []
        s = pr = yy[0]
        for y in yy[1:]:
            if y - pr > 12:
                groups.append((s, pr))
                s = y
            pr = y
        groups.append((s, pr))
        for (y0, y1) in groups:
            m = (ys2 >= y0) & (ys2 <= y1)
            if m.sum() < 200:
                continue
            centers.append(((y0 + y1) // 2, int(xs2[m].min()), int(xs2[m].max())))
    cards = []
    for (cy, x0, x1) in centers:
        top = cy - 135
        band = a[max(0, top + 20):top + 80, 250:430]
        br, bg, bb = band[:, :, 0], band[:, :, 1], band[:, :, 2]
        mx = np.maximum(np.maximum(br, bg), bb)
        mn = np.minimum(np.minimum(br, bg), bb)
        colored = ((mx - mn) > 50) & (mx > 80)
        dark = (mx < 150) & ((mx - mn) < 45)
        dc = np.where(dark.any(axis=0))[0]
        cc = np.where(colored.any(axis=0))[0]
        cards.append({
            "cy": cy, "top": top,
            "text_x": int(dc.min() + 250) if len(dc) else None,
            "icon": bool(len(cc)),
            "icon_x": (int(cc.min() + 250), int(cc.max() + 250)) if len(cc) else None,
        })
    return dim, cards

rows = []
for p in files:
    try:
        dim, cards = analyze(p)
    except Exception as e:
        rows.append((os.path.basename(p), "ERR", str(e)))
        continue
    ts = os.path.basename(p)[2:-4]
    desc = " ".join(f"[{c['cy']}:tx={c['text_x']}:ic={int(c['icon'])}]" for c in cards)
    rows.append((ts, "DIM" if dim else "---", desc))
    print(ts, "DIM" if dim else "---", desc, flush=True)

with open(os.path.join(D, "summary.txt"), "w", encoding="utf-8") as f:
    for row in rows:
        f.write(" ".join(str(x) for x in row) + "\n")
print("written summary")
