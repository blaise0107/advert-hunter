import time, re, json
import hunter2 as h

seen = []
for step in range(12):
    kind, a, path, lines = h.read_screen("act.png", check_focus=False)
    for l in lines:
        if h.is_order_header(l):
            num = h.order_number(l)
            if num and num not in seen:
                seen.append(num)
    print(step, kind, len(seen), seen[-1] if seen else None)
    if kind != "active":
        break
    h.swipe(540, 1900, 540, 900, 400)
    time.sleep(0.7)
print("TOTAL", len(seen))
print(json.dumps(seen))
