import json, subprocess, sys, os
BASE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(BASE, ".venv-ocr", "Scripts", "python.exe")
SRV = os.path.join(BASE, "ocr_rapid_server.py")
paths = sys.argv[1:]
p = subprocess.Popen([PY, "-u", SRV, "-Server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
try:
    for path in paths:
        p.stdin.write(json.dumps({"path": os.path.abspath(path)}, ensure_ascii=False) + "\n")
        p.stdin.flush()
        line = p.stdout.readline()
        data = json.loads(line)
        print("===", path)
        for it in (data if isinstance(data, list) else [data]):
            print(f"  y={it.get('y')} x={it.get('x')} w={it.get('w')} h={it.get('h')} '{it.get('t') or it.get('text')}'")
finally:
    try:
        p.stdin.close(); p.wait(timeout=3)
    except Exception:
        p.kill()
