"""Печатает OCR-строки кадра: python tools\\ocr_dump.py <png> [y0] [y1]."""
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
import hunter2 as h

path = sys.argv[1]
y0 = int(sys.argv[2]) if len(sys.argv) > 2 else 0
y1 = int(sys.argv[3]) if len(sys.argv) > 3 else 2412

lines = h.ocr(path)
print(f"lines: {len(lines)}")
for line in lines:
    if y0 <= line["y"] <= y1:
        print(f"y={line['y']:4d} x={line['x']:3d} {line['text']}")
