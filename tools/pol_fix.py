import subprocess, time
from PIL import Image
ADB = r"C:\Users\RZE\AppData\Local\Android\Sdk\platform-tools\adb.exe"; S="fab1701"
def adb(*a): return subprocess.run([ADB,"-s",S,*a], capture_output=True)
def shot(name):
    png = adb("exec-out","screencap","-p").stdout
    p = str(_Root(__file__).resolve().parents[1] / 'shots') + rf"\{name}.png"
    open(p,"wb").write(png); return p
# откат эксперимента с плотностью и policy_control к состоянию пользователя
print(adb("shell","wm","density","408").stdout.decode().strip())
print(adb("shell","settings","put","system","policy_control","full=com.advert.mobileearnings.app").returncode)
print("density:", adb("shell","wm","density").stdout.decode().strip())
adb("shell","am","force-stop","com.advert.mobileearnings.app")
time.sleep(1)
adb("shell","am","start","-n","com.advert.mobileearnings.app/.MainActivity")
time.sleep(35)
p = shot("menu_408_policy")
im = Image.open(p).convert("RGB")
print(im.size, "px(5,60)", im.getpixel((5,60)), "px(540,200)", im.getpixel((540,200)))
print(adb("shell","dumpsys window | grep mCurrentFocus").stdout.decode().strip())
