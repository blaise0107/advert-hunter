"""Показывает верх «Активных» и статус конкретного заказа."""
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
import hunter2 as h
import tasks as t


def ensure_menu():
    for _ in range(6):
        if not h.app_in_focus():
            h.start_app()
            time.sleep(4)
            continue
        kind, a, path, lines = h.read_screen("peek.png")
        if kind == "menu":
            return a, path, lines
        if kind == "modal":
            h.close_modal(a, h.modal_lines(a, path))
            time.sleep(0.6)
            continue
        h.tap(*h.BACK_BTN)
        time.sleep(1.0)
    return None, None, None


a, path, lines = ensure_menu()
print("menu:", a is not None)
if a is None:
    sys.exit(1)

pos = h.find_word(lines, "активные")
print("active tile:", pos)
h.tap(*pos)
time.sleep(3)

for page in range(3):
    kind, a, path, lines = h.read_screen(f"peek{page}.png")
    cards = t.active_cards(a, lines)
    print(f"--- page {page}: kind={kind}, cards={len(cards)}")
    for card in cards:
        print(f"  {card['order']} top={card['top']} action={card['actionable']} green={card['green']!r}")
        if card["order"] == "833306":
            top = card["top"]
            block = [l for l in lines if top - 10 <= l["y"] <= top + 260]
            for l in block:
                print(f"      y={l['y']:4d} x={l['x']:3d} {l['text']}")
    h.swipe(1048, 1900, 1048, 850, 250)
    time.sleep(0.7)
