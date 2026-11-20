import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
import hunter2 as h
p = str(_Root(__file__).resolve().parents[1] / 'frames') + r"\ru_154641_1416706.png"
a = h.shot(str(_Root(__file__).resolve().parents[1] / 'shots') + r"\now2.png")
lines = h.ocr(p)
for l in lines:
    if "акзап" in norm(l["text"]) or True:
        pass
def norm(s): return s.lower().replace("ё","е")
for l in lines:
    print(f"{l['y']:5} {l['x']:5} {l['text']}")
