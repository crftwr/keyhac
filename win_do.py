"""Guest desktop: a list of steps. key:<vk,vk,...> click:<x>,<y> wait:<s> shot:<path>"""
import ctypes, sys, time
from ctypes import wintypes
from PIL import ImageGrab
u = ctypes.windll.user32
ctypes.windll.shcore.SetProcessDpiAwareness(2)
class KI(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
class U(ctypes.Union):
    _fields_ = [("ki", KI), ("pad", ctypes.c_byte * 32)]
class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", U)]
u.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
u.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
def key(vk, up=False):
    i = INPUT(type=1); i.u.ki = KI(vk, 0, 2 if up else 0, 0, 0)
    u.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT)); time.sleep(0.08)
for step in sys.argv[1:]:
    op, _, arg = step.partition(":")
    if op == "key":
        vks = [int(v, 16) for v in arg.split(",")]
        for v in vks: key(v)
        for v in reversed(vks): key(v, True)
    elif op == "click":
        x, y = map(int, arg.split(","))
        u.SetCursorPos(x, y); time.sleep(0.4)
        u.mouse_event(2, 0, 0, 0, 0); time.sleep(0.06); u.mouse_event(4, 0, 0, 0, 0)
    elif op == "wait":
        time.sleep(float(arg))
    elif op == "shot":
        ImageGrab.grab(all_screens=True).save(arg)
    print(step, flush=True)
print("done", flush=True)
