import subprocess, time
from PIL import Image
ADB = r"C:\Users\RZE\AppData\Local\Android\Sdk\platform-tools\adb.exe"; S="fab1701"
def adb(*a): return subprocess.run([ADB,"-s",S,*a], capture_output=True)
print(adb("shell","settings","get","system","policy_control").stdout.decode().strip())
adb("shell","am","force-stop","com.advert.mobileearnings.app")
time.sleep(1)
adb("shell","am","start","-n","com.advert.mobileearnings.app/.MainActivity")
for i in (8, 25, 40):
    time.sleep(i - (8 if i==8 else 25 if i==25 else 15))
    png = adb("exec-out","screencap","-p").stdout
    p = str(_Root(__file__).resolve().parents[1] / 'shots') + rf"\pol_{i}.png"
    open(p,"wb").write(png)
    im = Image.open(p); a = im.convert("RGB")
    print(i, im.size, "top-left px", a.getpixel((5,60)), "row200", a.getpixel((540,200)))
print(adb("shell","dumpsys window | grep mCurrentFocus").stdout.decode().strip())
