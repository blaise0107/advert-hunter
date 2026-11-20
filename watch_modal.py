import time
import hunter2 as h

h.start_app()
time.sleep(3)
for i in range(20):
    kind, a, path, lines = h.read_screen(f"watch{i}.png")
    text = " | ".join(x["text"] for x in lines)
    print(i, kind, text[:180], flush=True)
    if kind == "modal":
        break
    time.sleep(6)
