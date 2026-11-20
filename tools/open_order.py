"""Открывает карточку заказа в «Активных» по номеру и печатает её OCR."""
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
import hunter2 as h
import tasks as t

order = sys.argv[1]
a, path, lines = t.open_active()
if a is None:
    print("не удалось открыть «Активные»")
    sys.exit(1)

for page in range(12):
    kind, a, path, lines = h.read_screen(f"oo{page}.png")
    if kind not in ("active", "list"):
        print("после прокрутки kind:", kind)
        break
    for card in t.active_cards(a, lines):
        if card["order"] != order:
            continue
        line = next((l for l in lines if order in l["text"]), None)
        pos = t.line_center(line) if line else (540, card["top"] + 40)
        print("card:", card)
        print("tap:", pos)
        t.raw_tap(*pos)
        time.sleep(2.5)
        kind2, a2, p2, l2 = h.read_screen("ood.png")
        print("after kind:", kind2)
        for l in l2:
            print(f"y={l['y']:4d} x={l['x']:3d} {l['text']}")
        sys.exit(0)
    h.swipe(1048, 1900, 1048, 850, 250)
    time.sleep(0.6)
print("заказ не найден")
