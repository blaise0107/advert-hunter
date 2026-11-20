"""Печатает строки блока карточки заказа в «Активных» с долей зелёного."""
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
import hunter2 as h
import tasks as t

order = sys.argv[1]
a, path, lines = t.open_active()
if a is None:
    print("не удалось открыть «Активные»")
    sys.exit(1)

for page in range(12):
    kind, a, path, lines = h.read_screen(f"ic{page}.png")
    if kind not in ("active", "list"):
        print("kind:", kind)
        break
    headers = [l for l in lines if h.is_order_header(l) and 250 <= l["y"] < h.CONTENT_BOTTOM]
    headers.sort(key=lambda l: l["y"])
    target = None
    for i, hd in enumerate(headers):
        if h.order_number(hd) == order:
            target = (i, hd)
            break
    if target:
        i, hd = target
        top = hd["y"]
        bottom = headers[i + 1]["y"] if i + 1 < len(headers) else h.CONTENT_BOTTOM
        print(f"header y={top} bottom={bottom}")
        for l in lines:
            if top <= l["y"] < bottom:
                print(f"y={l['y']:4d} x={l['x']:3d} green={t.green_ratio(a, l):.3f} {l['text']}")
        sys.exit(0)
    h.swipe(1048, 1900, 1048, 850, 250)
    import time
    time.sleep(0.6)
print("заказ не найден")
