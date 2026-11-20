import time, json
import hunter2 as h

kind, a, path, lines = h.read_screen("m.png", check_focus=False)
for _ in range(8):
    if kind == "menu":
        break
    h.key_back(); time.sleep(0.8)
    kind, a, path, lines = h.read_screen("m.png", check_focus=False)
print("menu:", kind)
pos = h.find_word(lines, "деньги")
print("деньги:", pos)
if pos:
    h.tap(*pos); time.sleep(2.0)
    kind, a, path, lines = h.read_screen("money.png", check_focus=False)
    print("kind:", kind)
    print(json.dumps([{"t": l["text"], "y": l["y"]} for l in lines], ensure_ascii=False))
