# -*- coding: utf-8 -*-
"""离屏渲染测试：用假屏幕调用 draw()，检查不抛异常并把帧打印出来看排版。"""
import sys, types, importlib.util, os

fake = types.ModuleType("curses")
for name, val in {
    "A_BOLD": 1, "A_DIM": 2, "A_REVERSE": 4, "A_UNDERLINE": 8, "A_NORMAL": 0,
    "COLOR_CYAN": 6, "COLOR_GREEN": 2, "COLOR_RED": 1, "COLOR_YELLOW": 3,
    "COLOR_MAGENTA": 5, "KEY_ENTER": 343, "KEY_BACKSPACE": 263, "KEY_DOWN": 258,
    "KEY_UP": 259, "KEY_LEFT": 260, "KEY_RIGHT": 261, "KEY_HOME": 262,
    "KEY_END": 360, "KEY_NPAGE": 338, "KEY_PPAGE": 339, "KEY_BTAB": 353,
    "KEY_RESIZE": 410,
}.items():
    setattr(fake, name, val)
fake.error = Exception
fake.color_pair = lambda n: 0
fake.init_pair = lambda *a: None
fake.start_color = lambda: None
fake.use_default_colors = lambda: None
fake.curs_set = lambda n: None
sys.modules["curses"] = fake

HERE = os.path.dirname(os.path.abspath(__file__))      # tests/
BIN = os.path.join(os.path.dirname(HERE), "bin")       # fishkit/bin
SCREEN_W = [80]


class Screen:
    def __init__(self, h, w):
        self.h, self.w = h, w
        self.buf = [[" "] * w for _ in range(h)]

    def getmaxyx(self):
        return self.h, self.w

    def erase(self):
        self.buf = [[" "] * self.w for _ in range(self.h)]

    def addstr(self, y, x, s, attr=0):
        if y < 0 or y >= self.h or x < 0 or x >= self.w:
            raise fake.error("out of bounds %r %r" % (y, x))
        for i, ch in enumerate(s):
            cx = x + i
            if cx >= self.w:
                raise fake.error("overwrite edge")
            self.buf[y][cx] = ch

    def refresh(self):
        pass

    def clear(self):
        self.erase()

    def move(self, y, x):
        pass

    def get_wch(self):
        raise fake.error("no input")

    def timeout(self, ms):
        pass


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(BIN, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def frame(scr):
    return "\n".join("".join(r).rstrip() for r in scr.buf)


fails = 0


def render(app, h, w, label):
    global fails
    scr = Screen(h, w)
    app.scr = scr
    app.h, app.w = h, w
    try:
        app.draw()
    except Exception as exc:
        fails += 1
        print("!! draw failed [%s %dx%d]: %r" % (label, w, h, exc))
        import traceback
        traceback.print_exc()
        return
    print("\n=== %s (%dx%d) ===" % (label, w, h))
    print(frame(scr))


# --------------------------- pkgview ---------------------------
pv = load("pkgview")
items = [
    pv.Item("libpython3.12-stdlib", "3.12.3-0ubuntu1", 12 * 1024 * 1024,
            "Interactive high-level object-oriented language (standard library)",
            "apt", {"架构": "amd64", "分类": "python", "主页": "https://www.python.org/",
                    "维护者": "Ubuntu Developers <ubuntu-devel@lists.ubuntu.com>"}),
    pv.Item("bash", "5.2.21-2ubuntu4", 1835 * 1024, "GNU Bourne Again SHell", "apt",
            {"架构": "amd64", "分类": "shells"}),
    pv.Item("中文测试包", "1.0", -1, "这是一个用于测试显示宽度的很长的中文描述，"
            "需要在详情面板里正确换行而不能把边框撑破。", "apt", {}),
]
srcs = [
    pv.Source("apt", "apt", lambda: items, True, items),
    pv.Source("snap", "snap", list, True, []),
    pv.Source("cmd", "命令", list, False, None),
]
app = pv.App(Screen(24, 80), srcs, "apt", "")
app.message = "匹配 3 / 3"
render(app, 24, 80, "pkgview 主页")
app.sel = 2
render(app, 24, 100, "pkgview 选中中文长描述")
app.pattern = "python|任"
app._recompute()
render(app, 20, 60, "pkgview 窄屏 + 过滤")
app.pattern = "[bad"
app._recompute()
render(app, 20, 70, "pkgview 正则错误")
app.pattern = ""
app._recompute()
app.help = True
render(app, 26, 90, "pkgview 帮助")
app.help = False
render(app, 8, 40, "pkgview 终端过小")

# --------------------------- aptui ---------------------------
au = load("aptui")
pkgs = [
    au.Pkg("linux-source-6.8.0", "noble-updates", "6.8.0-45.45", "all"),
    au.Pkg("bash", "noble,now", "5.2.21-2ubuntu4", "amd64", True, "5.2.21-2ubuntu4"),
    au.Pkg("bash-completion", "noble", "1:2.11-8", "all", True, "1:2.11-8", True),
    au.Pkg("curl", "noble-updates", "8.5.0-2ubuntu10.6", "amd64"),
    au.Pkg("vlc", "noble", "3.0.20-3build1", "amd64"),
]
au.load_installed = lambda: {"bash": "5.2.21-2ubuntu4", "bash-completion": "1:2.11-8"}
au.load_apt_cache = lambda force=False: (pkgs, "测试 0.00s")
au.fetch_details = lambda name: {
    "Version": "5.2.21-2ubuntu4", "Architecture": "amd64", "Installed-Size": "1835",
    "Section": "shells", "Priority": "required", "Maintainer": "Ubuntu Developers",
    "Homepage": "http://tiswww.case.edu/php/chet/bash/bashtop.html", "Size": "1003520",
    "Description": "GNU Bourne Again SHell\n Bash is an sh-compatible command language "
                   "interpreter that executes commands read from the standard input.",
}
app2 = au.App(Screen(24, 80), False)
app2.load(first=False)
app2.detail_cache = {p.name: au.fetch_details(p.name) for p in pkgs}
render(app2, 24, 80, "aptui 主页")
app2.query = "bsh"
app2.refresh_view()
app2.sel = 0
render(app2, 24, 90, "aptui 模糊搜索 bsh")
app2.query = ""
app2.refresh_view()
app2.pending = ("install", "bash")
render(app2, 24, 80, "aptui 安装确认")
app2.pending = None
app2.help = True
render(app2, 26, 90, "aptui 帮助")
app2.help = False
render(app2, 8, 40, "aptui 终端过小")

# 预填搜索词（pkg apt gcc 走的就是这条）
app3 = au.App(Screen(24, 80), False, "bsh")
app3.load(first=False)
print("\n预填 query=%r → 匹配 %d 项: %s" %
      (app3.query, len(app3.view), [p.name for p, _, _ in app3.view]))
if len(app3.view) != 2:
    fails += 1
    print("!! -q 预填搜索词不生效")
render(app3, 20, 80, "aptui 预填搜索词")

# --------------------------- 读取中：不能卡死界面 ---------------------------
import threading as _th
import time as _time

gate = _th.Event()
srcs_busy = [pv.Source("cmd", "命令", lambda: (gate.wait(5), [])[1], True, None)]
app5 = pv.App(Screen(24, 80), srcs_busy, "cmd", "")
busy_ok = app5.busy()                     # 构造完就该在后台读，而不是阻塞住
render(app5, 24, 80, "pkgview 读取中（命令）")
app5._spinner += 1
render(app5, 24, 80, "pkgview 读取中（转了一格）")
gate.set()
for _ in range(200):
    if app5._poll():
        break
    _time.sleep(0.02)
if not busy_ok or app5.busy() or app5.current().items != []:
    fails += 1
    print("!! 后台加载状态不对 busy=%r now=%r items=%r" %
          (busy_ok, app5.busy(), app5.current().items))

# Esc 取消：不能等线程跑完
gate2 = _th.Event()
srcs_busy2 = [pv.Source("cmd", "命令", lambda: (gate2.wait(5), [])[1], True, None)]
app6 = pv.App(Screen(24, 80), srcs_busy2, "cmd", "")
t0 = _time.time()
app6._handle_list("\x1b")
if app6.busy() or _time.time() - t0 > 0.3:
    fails += 1
    print("!! Esc 取消失败 busy=%r" % app6.busy())
gate2.set()
print("\n渲染失败次数: %d" % fails)
sys.exit(1 if fails else 0)