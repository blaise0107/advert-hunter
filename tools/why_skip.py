"""Показывает, почему карточки <png> не считаются RU.

Запуск: python tools\\why_skip.py <png>
"""
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
import hunter2 as h

path = sys.argv[1]
image = Image.open(path)
a = np.array(image.convert("RGB")).astype(np.int16)
lines = h.ocr(path)
cards = h.analyze_cards(a, lines)
print(f"cards: {len(cards)}  size={image.size}")
for card in cards:
    print(
        f"{card['order']}: ru={card['ru']} reason={card.get('reason')!r} "
        f"foreign={card['foreign']} samsung={card['samsung']} low={card['low']} "
        f"cut={card['cut']} partner={card.get('partner')} "
        f"requirements={card.get('requirements')} top={card['top']}"
    )
    print(f"    title={card['title']}")
    print(f"    text={card['text'][:300]}")
