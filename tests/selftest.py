# -*- coding: utf-8 -*-
"""在 Windows 上做逻辑自测：用假的 curses 模块导入两个工具，验证纯逻辑函数。"""
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
# 注意：故意不定义 curses.A_NONE —— 真 curses 里没有这个常量，
# 之前就是因为测试桩自己加了它，才漏掉了 aptui 里的 AttributeError。
fake.error = Exception


class _Pair(int):
    pass


fake.color_pair = lambda n: 0
fake.init_pair = lambda *a: None
fake.start_color = lambda: None
fake.use_default_colors = lambda: None
fake.curs_set = lambda n: None
sys.modules["curses"] = fake

HERE = os.path.dirname(os.path.abspath(__file__))      # tests/
BIN = os.path.join(os.path.dirname(HERE), "bin")       # fishkit/bin


def load(mod_name):
    spec = importlib.util.spec_from_file_location(mod_name, os.path.join(BIN, mod_name + ".py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = m
    spec.loader.exec_module(m)
    return m


pv = load("pkgview")
au = load("aptui")

ok = 0
fail = 0


def check(cond, label, extra=""):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print("FAIL:", label, extra)


# ---------------- pkgview ----------------
check(pv.dwidth("abc") == 3, "dwidth ascii")
check(pv.dwidth("中文") == 4, "dwidth cjk")
check(pv.clip("abcdef", 4) == "abc…", "clip", pv.clip("abcdef", 4))
check(pv.dwidth(pv.clip("中文中文", 5)) <= 5, "clip cjk width", pv.clip("中文中文", 5))
check(pv.pad("ab", 5) == "ab   ", "pad left")
check(pv.pad("ab", 5, ">") == "   ab", "pad right")
check(pv.human_size(0) == "0 B", "size 0", pv.human_size(0))
check(pv.human_size(1536) == "1.5 K", "size 1536", pv.human_size(1536))
check(pv.human_size(5 * 1024 ** 3) == "5.0 G", "size 5G", pv.human_size(5 * 1024 ** 3))
check(pv.human_size(-1) == "-", "size unknown")
w = pv.wrap("一二三四五六", 4)
check(all(pv.dwidth(s) <= 4 for s in w) and "".join(w) == "一二三四五六", "wrap cjk", w)

# dpkg 解析
sample = "bash\t5.2.21-2ubuntu4\t1835\tamd64\tshells\tMatthias Klose <x>\thttp://gnu.org/bash\tGNU Bourne Again SHell\n"
_orig = pv._run
pv._run = lambda cmd, timeout=25.0: sample
items = pv.load_apt()
check(len(items) == 1 and items[0].name == "bash", "load_apt parse", items)
check(items[0].size == 1835 * 1024, "load_apt size", items[0].size)
check(items[0].extra.get("主页") == "http://gnu.org/bash", "load_apt homepage")
check(items[0].summary.startswith("GNU Bourne"), "load_apt summary")

# snap 解析
pv._run = lambda cmd, timeout=25.0: (
    "Name     Version   Rev    Tracking       Publisher   Notes\n"
    "core22   20240408  1380   latest/stable  canonical*  base\n"
    "code     1.89.1    175    latest/stable  vscode*     classic\n"
)
snaps = pv.load_snap()
check(len(snaps) == 2 and snaps[0].name == "core22", "load_snap", snaps)
check(snaps[1].extra.get("发布者") == "vscode*", "load_snap publisher", snaps[1].extra)

# 正则匹配
import re
rx = re.compile("^bash$", re.I)
check(pv.App._match(rx, pv.Item("bash", "5", 1, "", "apt", {})), "match exact")
check(not pv.App._match(rx, pv.Item("bashful", "5", 1, "", "apt", {})), "match anchored")

# ---------------- aptui ----------------
check(au.dwidth("包") == 2, "aptui dwidth")
check(au.human_kb(1835) == "1.8 M", "human_kb", au.human_kb(1835))
check(au.human_b(1024 * 1024) == "1.0 M", "human_b", au.human_b(1024 * 1024))

r = au.fuzzy("lsrc", "linux-source-6.8.0")
check(r is not None, "fuzzy subsequence lsrc")
r2 = au.fuzzy("lskr", "bash")
check(r2 is None, "fuzzy no match")
a = au.fuzzy("bash", "bash")
b = au.fuzzy("bash", "bash-completion")
check(a[0] > b[0], "prefix shorter scores higher", (a, b))
c = au.fuzzy("bas", "bash")
d = au.fuzzy("bas", "b-a-s")
check(c[0] > d[0], "consecutive beats scattered", (c, d))
check(au.fuzzy("", "anything")[0] == 0.0, "empty pattern")
pos = au.fuzzy("bsh", "bash")[1]
check(pos == [0, 2, 3], "highlight positions", pos)

# apt list 解析
apt_sample = (
    "Listing...\n"
    "bash/noble,now 5.2.21-2ubuntu4 amd64 [installed]\n"
    "curl/noble-updates 8.5.0-2ubuntu10.6 amd64 [upgradable from: 8.5.0-2ubuntu10.1]\n"
    "zlib1g/noble 1:1.3.dfsg-3.1ubuntu2 amd64\n"
)
au._run = lambda cmd, timeout=60.0, env=None: apt_sample if cmd[0] == "apt" else ""
pkgs = au.load_apt_cache()[0]
check(len(pkgs) == 3, "apt list count", [p.name for p in pkgs])
d0 = {p.name: p for p in pkgs}
check(d0["bash"].installed and d0["bash"].installed_ver == "5.2.21-2ubuntu4", "apt installed",
      d0["bash"])
check(d0["curl"].upgradable and d0["curl"].installed_ver == "8.5.0-2ubuntu10.1", "apt upgradable",
      d0["curl"])
check(not d0["zlib1g"].installed, "apt not installed")
check(d0["zlib1g"].version == "1:1.3.dfsg-3.1ubuntu2", "apt version parse")

# apt-cache show 解析
show_sample = """Package: bash
Version: 5.2.21-2ubuntu4
Architecture: amd64
Installed-Size: 1835
Maintainer: Ubuntu Developers
Section: shells
Priority: required
Homepage: http://tiswww.case.edu/php/chet/bash/bashtop.html
Description: GNU Bourne Again SHell
 Bash is an sh-compatible command language interpreter.
"""
au._run = lambda cmd, timeout=10.0, env=None: show_sample
dd = au.fetch_details("bash")
check(dd.get("Version") == "5.2.21-2ubuntu4", "show version", dd)
check("sh-compatible" in dd.get("Description", ""), "show description", dd.get("Description"))
check(dd.get("Homepage", "").startswith("http"), "show homepage")

# ---------------- 性能：apt list 的量级 ----------------
import time

N = 120000
lines = ["Listing..."]
for i in range(N):
    lines.append("pkg-name-%06d/noble-updates %d.0.1-1ubuntu2 amd64%s"
                 % (i, i, " [installed]" if i % 50 == 0 else ""))
big = "\n".join(lines) + "\n"

t0 = time.time()
parsed = au._parse_apt_list(big)
dt_parse = time.time() - t0
check(len(parsed) == N, "parse 12 万行条数", len(parsed))
check(dt_parse < 3.0, "parse 12 万行速度", "%.2fs" % dt_parse)

names = [p.name.lower() for p in parsed]
t0 = time.time()
hits = 0
for nm in names:
    if au.fuzzy("lsrc", nm) is not None:
        hits += 1
dt_fuzzy = time.time() - t0

print("\n解析 12 万行 %.2fs；全量模糊匹配一次 %.2fs（命中 %d）" % (dt_parse, dt_fuzzy, hits))
check(dt_fuzzy < 3.0, "全量模糊匹配速度", "%.2fs" % dt_fuzzy)

# ---------------- 命令源：扫 PATH 不能卡死 ----------------
# 线上事故：WSL 的 PATH 里混着 /mnt/c/...，一次 stat 过 9p，扫几分钟像卡死
import tempfile
import threading

check(pv._slow_dir("/mnt/c/Windows/System32") == "windows", "/mnt/c 判为慢目录")
check(pv._slow_dir("/mnt/d") == "windows", "/mnt/d 判为慢目录")
check(pv._slow_dir("/usr/local/bin") == "", "/usr/local/bin 正常")
check(pv._slow_dir("/home/eest/.local/bin") == "", "家目录 bin 正常")

tmp = tempfile.mkdtemp(prefix="pv-cmd-")
d1, d2 = os.path.abspath(os.path.join(tmp, "a")), os.path.abspath(os.path.join(tmp, "b"))
os.makedirs(d1)
os.makedirs(d2)

real_scandir = os.scandir


def _boom(*a, **kw):
    raise AssertionError("不该重新扫盘")


# Windows 上 os.chmod 造不出 exec 位，所以用假 scandir 喂条目，逻辑在哪个平台都能测
import types as _types


class FakeEnt:
    def __init__(self, path, mode, size=100, ino=1, link=False):
        self.path = path
        self.name = os.path.basename(path)
        self._st = _types.SimpleNamespace(st_mode=mode, st_size=size, st_dev=7, st_ino=ino)
        self._link = link

    def stat(self, follow_symlinks=True):
        return self._st

    def is_symlink(self):
        return self._link


entries = {
    d1: [FakeEnt(os.path.join(d1, "alpha"), 0o100755, 100, 1),
         FakeEnt(os.path.join(d1, "beta"), 0o100755, 200, 2),
         FakeEnt(os.path.join(d1, "notexec"), 0o100644, 300, 3)],
    d2: [FakeEnt(os.path.join(d2, "gamma"), 0o100755, 400, 4),
         # 和 alpha 同一个 inode，模拟软链 / 硬链重复
         FakeEnt(os.path.join(d2, "alpha-link"), 0o100755, 100, 1, link=True)],
}


def fake_scandir(path):
    class CM:
        def __enter__(self_):
            return iter(entries.get(os.path.abspath(path), []))

        def __exit__(self_, *a):
            return False

    return CM()


old_path = os.environ.get("PATH", "")
old_cache = pv.CMD_CACHE
os.environ["PATH"] = d1 + os.pathsep + d2
pv.CMD_CACHE = os.path.join(tmp, "cmd-scan.json")
pv.os.scandir = fake_scandir
try:
    items = pv.load_cmd()
finally:
    pv.os.scandir = real_scandir
names = sorted(it.name for it in items)
check(names == ["alpha", "beta", "gamma"], "cmd 只收可执行文件", names)
check([it.size for it in items if it.name == "beta"] == [200], "cmd 拿到体积")
check(len([it for it in items if it.name.startswith("alpha")]) == 1,
      "同 inode 只出现一次（软链去重）", names)

# 缓存命中：把 scandir 打成筛子，还能秒回说明走的是缓存
pv.os.scandir = _boom
try:
    again = pv.load_cmd()
finally:
    pv.os.scandir = real_scandir
check(sorted(it.name for it in again) == names, "cmd 缓存命中")

# 目录一变（比如 apt 装了新命令），缓存必须失效并重新扫
entries[d1].append(FakeEnt(os.path.join(d1, "delta"), 0o100755, 500, 5))
os.utime(d1, None)
pv.os.scandir = fake_scandir
try:
    items2 = pv.load_cmd()
finally:
    pv.os.scandir = real_scandir
check("delta" in [it.name for it in items2], "目录 mtime 变了就重新扫",
      [it.name for it in items2])

# 取消：Event 已置位时必须立刻返回，不能继续扫
stop = threading.Event()
stop.set()
pv.os.scandir = _boom                       # 一旦真去扫就会炸，证明它提前退出了
t0 = time.time()
try:
    cancelled = pv.load_cmd(stop=stop)
finally:
    pv.os.scandir = real_scandir
dt_cancel = time.time() - t0
check(cancelled == [] and dt_cancel < 0.5, "取消后立即返回", "%.3fs" % dt_cancel)

# 跳过 Windows 互通目录时，状态栏要说清楚
os.environ["PATH"] = d1 + os.pathsep + "/mnt/c/Windows"
pv.CMD_CACHE = os.path.join(tmp, "cmd-scan2.json")
pv.STATUS["note"] = ""
pv.os.scandir = fake_scandir
try:
    pv.load_cmd()
finally:
    pv.os.scandir = real_scandir
check("跳过" in pv.STATUS["note"], "提示跳过了互通目录", pv.STATUS["note"])
check(pv._cmd_budget() >= 0.5, "预算可配置且有下限", pv._cmd_budget())

os.environ["PATH"] = old_path
pv.CMD_CACHE = old_cache
import shutil as _sh

_sh.rmtree(tmp, ignore_errors=True)

# ---------------- 静态守卫：别再用到真 curses 里不存在的属性 ----------------
# 上次就是 A_NONE 这种"测试桩里有、真 curses 里没有"的东西直接炸在用户终端上
import re as _re

VALID_CURSES = {
    "A_ALTCHARSET", "A_BLINK", "A_BOLD", "A_CHARTEXT", "A_DIM", "A_INVIS",
    "A_ITALIC", "A_NORMAL", "A_PROTECT", "A_REVERSE", "A_STANDOUT", "A_UNDERLINE",
    "COLOR_BLACK", "COLOR_BLUE", "COLOR_CYAN", "COLOR_GREEN", "COLOR_MAGENTA",
    "COLOR_RED", "COLOR_WHITE", "COLOR_YELLOW",
    "COLOR_PAIR", "KEY_BACKSPACE", "KEY_BTAB", "KEY_DOWN", "KEY_END", "KEY_ENTER",
    "KEY_HOME", "KEY_LEFT", "KEY_NPAGE", "KEY_PPAGE", "KEY_RESIZE", "KEY_RIGHT",
    "KEY_UP", "color_pair", "curs_set", "def_prog_mode", "endwin", "error",
    "getch", "init_pair", "reset_prog_mode", "setupterm", "start_color",
    "use_default_colors", "wrapper",
}
_src = ""
for _f in ("aptui.py", "pkgview.py"):
    with open(os.path.join(BIN, _f), encoding="utf-8") as _fh:
        _src += _fh.read()
_used = set(_re.findall(r"curses\.([A-Za-z_]+)", _src))
_bad = _used - VALID_CURSES
check(not _bad, "curses 属性都真实存在", _bad)

print("\n通过 %d，失败 %d" % (ok, fail))
sys.exit(1 if fail else 0)
