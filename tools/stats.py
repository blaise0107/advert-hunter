"""tools/stats.py — метрики охоты из results.jsonl (stdlib, ничего не ломает).

Запуск: python tools/stats.py [путь_к_results.jsonl]
По умолчанию читает results.jsonl в корне проекта.
Предложение Qwen 4 MAX (секция C1): еженедельно отвечать на вопрос —
доля unknown растёт или падает после правок?
"""
import json
import os
import sys
from collections import Counter

DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "results.jsonl")
path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT

try:
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
except FileNotFoundError:
    sys.exit(f"нет файла: {path}")

if not rows:
    sys.exit("пусто")

st = Counter(r.get("status") for r in rows)
print(f"всего: {len(rows)}")
for s, n in st.most_common():
    print(f"  {s:14s} {n:4d}  {n/len(rows):.0%}")

print("\nunknown по stage:", dict(Counter(
    r.get("stage") for r in rows if r.get("status") == "unknown")))
print("accepted по часам:", dict(sorted(Counter(
    r["at"][11:13] for r in rows
    if r.get("status") in ("accepted", "install_done")).items())))
print("по устройствам:", dict(Counter(r.get("serial", "?") for r in rows)))
