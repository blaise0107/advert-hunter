import time, re
import hunter2 as h

# Перейти в главное меню и открыть АКТИВНЫЕ
kind, a, path, lines = h.read_screen("base.png", check_focus=False)
print("now:", kind)
for _ in range(8):
    if kind == "menu":
        break
    h.key_back()
    time.sleep(0.8)
    kind, a, path, lines = h.read_screen("base.png", check_focus=False)
    print("now:", kind)
if kind != "menu":
    print("меню не найдено"); raise SystemExit
pos = h.find_word(lines, "активные")
print("активные:", pos)
if pos:
    h.tap(*pos)
    time.sleep(2.0)
    kind, a, path, lines = h.read_screen("base2.png", check_focus=False)
    print("after:", kind)
    t = " | ".join(l["text"] for l in lines)
    print("text:", t[:600])
