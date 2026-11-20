import sys, time
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
_Root = __import__("pathlib").Path  # Qwen: базовая директория для путей tools/
import hunter2 as h
p = str(_Root(__file__).resolve().parents[1] / 'shots') + r"\after_ok.png"
t0 = time.time()
a = h.shot(p)
t1 = time.time(); print(f"screencap {t1-t0:.2f}s")
lines = h.ocr(p)
t2 = time.time(); print(f"ocr {t2-t1:.2f}s, {len(lines)} lines")
cards = h.analyze_cards(a, lines)
for c in cards:
    print(c["order"], "top", c["top"], "foreign", c["foreign"], "icon", c["icon"], "ru", c["ru"], "cut", c["cut"])
