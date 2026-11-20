_Root = __import__("pathlib").Path  # Qwen: базовая директория для путей tools/
from PIL import Image
import numpy as np
im = Image.open(str(_Root(__file__).resolve().parents[1] / 'shots') + r"\act_detail.png").convert("RGB")
a = np.array(im).astype(int)
print(a.shape)
crops = {
 "req_done": (215, 340, 250, 375),     # зелёная галочка «Установить приложение»
 "req_pending": (215, 690, 250, 725),  # жёлтый круг «Открыть на 8 день»
 "req_fail": (215, 1610, 250, 1645),   # красный крестик у 833306
 "ring_red": (780, 400, 930, 560),     # красное кольцо 8.00
}
for k, (x0, y0, x1, y1) in crops.items():
    b = a[y0:y1, x0:x1]
    r, g, bl = b[:,:,0], b[:,:,1], b[:,:,2]
    print(k, "mean", tuple(int(v) for v in b.reshape(-1,3).mean(0)))
    print("   green px", int(((g>140)&(r<130)&(bl<130)).sum()),
          "red px", int(((r>140)&(g<110)&(bl<110)).sum()),
          "yellow px", int(((r>170)&(g>140)&(bl<130)).sum()))
# широкие цветовые полосы по всему кадру
r, g, bl = a[:,:,0], a[:,:,1], a[:,:,2]
green = (g>140)&(r<130)&(bl<130)&((g-r)>60)
red = (r>150)&(g<100)&(bl<100)&((r-g)>60)
yellow = (r>190)&(g>150)&(bl<130)
for name, m in (("green", green), ("red", red), ("yellow", yellow)):
    ys, xs = np.where(m)
    if len(ys)==0: print(name, "none"); continue
    print(name, "n", len(ys), "bbox x", xs.min(), xs.max(), "y", ys.min(), ys.max())
    rows = sorted(set(ys.tolist()))
    groups=[]; s=pr=rows[0]
    for y in rows[1:]:
        if y-pr>15: groups.append((s,pr)); s=y
        pr=y
    groups.append((s,pr))
    print("   bands", [(a,b) for a,b in groups if b-a>5][:20])
