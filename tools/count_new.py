"""Считает все карточки в «Новые» AdvertApp, прокручивая список до конца."""
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
import hunter2 as h

h.adb("shell", "am force-stop " + h.active_app())
h.start_app()
time.sleep(6)
a, path = h.open_new(timeout=20)
if a is None:
    print("не удалось открыть «Новые»")
    sys.exit(1)

total = {}
for page in range(7):
    kind, a, path, lines = h.read_screen("count.png")
    cards = h.analyze_cards(a, lines)
    print(f"page {page}: kind={kind} cards={len(cards)}")
    for c in cards:
        total[c["order"]] = c
        flags = []
        if c["ru"]:
            flags.append("RU")
        if c["samsung"]:
            flags.append("samsung")
        if c["foreign"]:
            flags.append("foreign")
        if c["cut"]:
            flags.append("cut")
        print(f"  {c['order']} top={c['top']} {'/'.join(flags) or '-'} :: {c['text'][:70]}")
    h.swipe(1048, 1900, 1048, 850, 250)
    time.sleep(0.7)

print("итого уникальных:", len(total))
print("RU:", sum(1 for c in total.values() if c["ru"]))
