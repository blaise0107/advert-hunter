import time
import tasks as t
import hunter2 as h

a, path, lines = t.ensure_menu()
print("menu:", path)
a, path, lines = t.open_active()
print("active:", path)
card = t.locate("1410398")
print("card:", None if card is None else {k: v for k, v in card.items() if k != "a"})
if card:
    h.tap(540, card["top"] + 60)
    time.sleep(3)
    kind, a, path, lines = h.read_screen("probe1410398.png")
    print("kind:", kind, path)
    for ln in lines:
        print("  ", ln.get("text", ln))
