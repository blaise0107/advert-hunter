import subprocess, time, sys, os
from datetime import datetime

ADB = r"C:\Users\RZE\AppData\Local\Android\Sdk\platform-tools\adb.exe"
OUT = r"C:\Users\RZE\AppData\Local\Temp\opencode\obs"
os.makedirs(OUT, exist_ok=True)

start = datetime.strptime(sys.argv[1], "%H:%M:%S").time() if len(sys.argv) > 1 else None
end = datetime.strptime(sys.argv[2], "%H:%M:%S").time() if len(sys.argv) > 2 else None
interval = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0

def now_in_range():
    n = datetime.now().time()
    if start and n < start:
        return False
    if end and n > end:
        return False
    return True

print("waiting until", start, flush=True)
while not now_in_range():
    time.sleep(0.2)

i = 0
while now_in_range():
    t0 = time.time()
    ts = datetime.now().strftime("%H%M%S_%f")[:-3]
    p = os.path.join(OUT, f"f_{ts}.png")
    with open(p, "wb") as f:
        subprocess.run([ADB, "-s", "fab1701", "exec-out", "screencap", "-p"], stdout=f, timeout=20)
    i += 1
    dt = time.time() - t0
    print(f"{ts} {os.path.getsize(p)} {dt:.2f}s", flush=True)
    wait = interval - dt
    if wait > 0:
        time.sleep(wait)
print("done", i, flush=True)
