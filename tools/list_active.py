"""Печатает все карточки в «Активных» с их строками и долей зелёного."""
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
import hunter2 as h
import tasks as t

a, path, lines = t.open_active()
if a is None:
    print("не удалось открыть «Активные»")
    sys.exit(1)

seen = set()
for page in range(14):
    kind, a, path, lines = h.read_screen(f"la{page}.png")
    if kind not in ("active", "list"):
        print("kind:", kind)
        break
    cards = t.active_cards(a, lines)
    fresh = [c for c in cards if c["order"] not in seen]
    if not fresh:
        break
    for card in fresh:
        seen.add(card["order"])
        print(f"=== {card['order']} top={card['top']} actionable={card['actionable']} green={card['green']!r}")
        block = [l for l in lines if card["top"] <= l["y"] < card["top"] + 420]
        for l in block:
            print(f"    y={l['y']:4d} x={l['x']:3d} g={t.green_ratio(a, l):.3f} {l['text']}")
    h.swipe(1048, 1900, 1048, 850, 250)
    time.sleep(0.6)
print("всего карточек:", len(seen))
