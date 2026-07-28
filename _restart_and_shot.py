# -*- coding: utf-8 -*-
import json, os, subprocess, sys, time
from pathlib import Path

ROOT = Path(r"C:\GROK\jiankong")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

# kill old monitor
try:
    import psutil
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cl = " ".join(p.info.get("cmdline") or [])
            if "monitor.py" in cl:
                print("kill", p.info["pid"], cl[:120])
                p.kill()
        except Exception:
            pass
except Exception as e:
    print("psutil", e)

time.sleep(0.8)
exe = r"C:\Python314\pythonw.exe"
if not Path(exe).is_file():
    exe = r"C:\Python314\python.exe"
script = str(ROOT / "monitor.py")
print("start", exe, script)
subprocess.Popen([exe, script], cwd=str(ROOT), close_fds=True)
time.sleep(5)

from PIL import ImageGrab
pos = {"x": 2200, "y": 800}
try:
    pos = json.loads((ROOT / "window_pos.json").read_text(encoding="utf-8"))
except Exception:
    pass
x, y = int(pos.get("x", 0)), int(pos.get("y", 0))
print("pos", x, y)
img = ImageGrab.grab(bbox=(x, y, x + 300, y + 560))
out = ROOT / "verify_fix_final.png"
img.save(out)
data = list(img.getdata())
br = sum(sum(p[:3]) for p in data[::40]) / (max(1, len(data[::40])) * 3)
print("saved", out, img.size, "br", round(br, 1))
img.crop((0, 0, 300, 185)).save(ROOT / "verify_fix_rings.png")
img.crop((0, 155, 300, 255)).save(ROOT / "verify_fix_speed.png")
img.crop((0, 250, 300, 430)).save(ROOT / "verify_fix_chart.png")
print("crops ok")
