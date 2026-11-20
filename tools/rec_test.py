_Root = __import__("pathlib").Path  # Qwen: базовая директория для путей tools/
import subprocess, os
from PIL import Image
ADB = r"C:\Users\RZE\AppData\Local\Android\Sdk\platform-tools\adb.exe"
S = "fab1701"
def adb(*a): return subprocess.run([ADB,"-s",S,*a], capture_output=True)
# 1) убрать системные панели (полный экран) и плотность обратно на штатную 480
print(adb("shell","wm","density","reset").stdout.decode(),"|",adb("shell","wm","density").stdout.decode())
d = adb("shell","settings","put","system","policy_control","full=advert.mobileearnings.app")
print("policy rc", d.returncode, d.stderr.decode().strip())
os.makedirs(str(__import__("pathlib").Path(__file__).resolve().parents[1] / "shots"), exist_ok=True)
for app in ["com.advert.mobileearnings.app", "com.rze.advertsplit"]:
    adb("shell","am","force-stop",app)
    adb("shell","monkey","-p",app,"-c","android.intent.category.LAUNCHER","1")
    import time; time.sleep(6)
    png = adb("exec-out","screencap","-p").stdout
    open(str(_Root(__file__).resolve().parents[1] / 'shots') + rf"\rec_{app.split('.')[-1]}.png","wb").write(png)
    print(app, len(png))
