import time
import tasks as t
import hunter2 as h

a, p, l = t.ensure_menu()
a, p, l = t.open_active()
c = t.locate("1410398")
print("card:", c and {k: v for k, v in c.items() if k != "a"})
h.tap(540, c["top"] + 60)
time.sleep(2)
for i in range(5):
    kind, a, p, l = h.read_screen(f"det{i}.png")
    print("---", i, kind, p)
    print(" | ".join(x["text"] for x in l))
    h.swipe(540, 1900, 540, 900, 300)
    time.sleep(0.6)
