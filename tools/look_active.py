"""Смотрит экран «Активные» AdvertApp: что видно по каждому заказу."""
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
import hunter2 as h

h.start_app()
time.sleep(3)

kind, a, path, lines = h.read_screen("act0.png")
for _ in range(4):
    if kind == "menu":
        break
    h.tap(*h.BACK_BTN)
    time.sleep(1.2)
    kind, a, path, lines = h.read_screen("act0.png")
print("screen:", kind)

pos = h.find_word(lines, "активные")
print("активные:", pos)
if pos:
    h.tap(*pos)
    time.sleep(2.5)

kind, a, path, lines = h.read_screen("act1.png")
print("screen:", kind)
print(" | ".join(l["text"] for l in lines)[:2500])

for page in range(2):
    h.swipe(1048, 1900, 1048, 850, 250)
    time.sleep(0.8)
    kind, a, path, lines = h.read_screen(f"act{2 + page}.png")
    print(f"--- page {page + 1}: {kind}")
    print(" | ".join(l["text"] for l in lines)[:2500])
