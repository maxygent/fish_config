#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pkgview —— 在 WSL / Linux 终端里查看已安装程序的 TUI 小工具，支持正则搜索。

用法:
    ./pkgview.py                      # 打开交互界面
    ./pkgview.py -s snap              # 直接打开 snap 页
    ./pkgview.py -f '^lib'            # 带初始正则
    ./pkgview.py --dump apt -f gcc    # 非交互模式，打印匹配结果（方便 grep / 脚本）

数据来源: apt(dpkg) / snap / flatpak / pip / PATH 可执行文件
依赖: 仅 Python 3 标准库（curses），无需 pip install。

快捷键:
    /        输入正则（实时过滤，Enter 确认，Esc 取消）
    j k      上下移动          g G     首行 / 末行
    Ctrl-d   Ctrl-u  翻页      空格 b   下翻 / 上翻
    Tab  h l ←→   切换数据源    1..9   直跳数据源
    s        切换排序           r       重新读取
    Esc      清除正则 / 退出     q       退出
    ?        帮助
"""

from __future__ import annotations

import argparse
import curses
import hashlib
import json
import locale
import os
import re
import shutil
import stat as statmod
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #

@dataclass
class Item:
    name: str
    version: str = ""
    size: int = -1                      # 字节；-1 表示未知
    summary: str = ""
    source: str = ""
    extra: Dict[str, str] = field(default_factory=dict)


@dataclass
class Source:
    key: str
    label: str
    loader: Callable[[], List[Item]]
    available: bool = True
    items: Optional[List[Item]] = None
    note: str = ""                      # 加载后的附注（跳过了什么、是否超预算）


SORT_MODES: List[Tuple[str, str, bool]] = [
    ("名称 ↑", "name", False),
    ("名称 ↓", "name", True),
    ("体积 ↓", "size", True),
    ("体积 ↑", "size", False),
]


# --------------------------------------------------------------------------- #
# 宽度 / 文本工具（按显示宽度处理中文）
# --------------------------------------------------------------------------- #

def char_w(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def dwidth(s: str) -> int:
    return sum(char_w(c) for c in s)


def clip(s: str, w: int) -> str:
    """按显示宽度截断，超长时末尾加省略号。"""
    if w <= 0:
        return ""
    if dwidth(s) <= w:
        return s
    if w == 1:
        return "…"
    out, acc = [], 0
    for ch in s:
        cw = char_w(ch)
        if acc + cw > w - 1:
            break
        out.append(ch)
        acc += cw
    return "".join(out) + "…"


def pad(s: str, w: int, align: str = "<") -> str:
    s = clip(s, w)
    gap = w - dwidth(s)
    if gap <= 0:
        return s
    return ((" " * gap) + s) if align == ">" else (s + " " * gap)


BREAK_CHARS = " /-_,，、）):："


def wrap(s: str, w: int) -> List[str]:
    """按显示宽度换行，尽量在空格 / 标点处断开，长 URL 不至于被切碎。"""
    if w <= 1:
        return [""]
    out: List[str] = []
    for para in s.split("\n"):
        rest = para
        while dwidth(rest) > w:
            acc, cut = 0, 0
            for i, ch in enumerate(rest):
                cw = char_w(ch)
                if acc + cw > w:
                    break
                acc += cw
                cut = i + 1
            if cut == 0:
                cut = 1
            for j in range(cut - 1, max(0, cut // 2) - 1, -1):
                if rest[j] in BREAK_CHARS:
                    cut = j + 1
                    break
            out.append(rest[:cut].rstrip())
            rest = rest[cut:].lstrip(" ")
        out.append(rest)
    return out or [""]


def human_size(n: int) -> str:
    if n is None or n < 0:
        return "-"
    units = ["B", "K", "M", "G", "T"]
    v = float(n)
    for u in units:
        if v < 1024 or u == units[-1]:
            return ("%d %s" % (v, u)) if u == "B" else ("%.1f %s" % (v, u))
        v /= 1024.0
    return "-"


# --------------------------------------------------------------------------- #
# 各个数据源的读取
# --------------------------------------------------------------------------- #

def _run(cmd: List[str], timeout: float = 25.0) -> str:
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=timeout)
        return p.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return ""


def load_apt() -> List[Item]:
    fmt = "\t".join([
        "${Package}", "${Version}", "${Installed-Size}", "${Architecture}",
        "${Section}", "${Maintainer}", "${Homepage}", "${binary:Summary}",
        "${db:Status-Abbrev}",
    ]) + "\n"
    out = _run(["dpkg-query", "-W", "-f", fmt], timeout=40)
    items: List[Item] = []
    for line in out.splitlines():
        f = line.split("\t")
        if len(f) < 8:
            continue
        name, ver, isize, arch, section, maint, home, summary = f[:8]
        if not name:
            continue
        status = f[8].strip() if len(f) > 8 else "ii"
        if not status.startswith("ii"):        # 只保留真正处于 installed 状态的
            continue
        try:
            size = int(isize) * 1024
        except (TypeError, ValueError):
            size = -1
        extra = {}
        if arch:
            extra["架构"] = arch
        if section:
            extra["分类"] = section
        if home:
            extra["主页"] = home
        if maint:
            extra["维护者"] = maint
        items.append(Item(name, ver, size, summary.strip(), "apt", extra))
    return items


def _split_cols(line: str) -> List[str]:
    if "\t" in line:
        return [c for c in line.split("\t")]
    return [c for c in re.split(r"\s{2,}", line.strip()) if c != ""]


def load_snap() -> List[Item]:
    out = _run(["snap", "list"])
    items: List[Item] = []
    for line in out.splitlines()[1:]:
        if not line.strip() or line.startswith("No snaps"):
            continue
        f = _split_cols(line)
        if len(f) < 2:
            continue
        extra = {}
        if len(f) > 3 and f[3] not in ("-", ""):
            extra["渠道"] = f[3]
        if len(f) > 4 and f[4] != "-":
            extra["发布者"] = f[4]
        if len(f) > 5 and f[5].strip():
            extra["备注"] = f[5].strip()
        items.append(Item(f[0], f[1], -1, "snap 包", "snap", extra))
    return items


def load_flatpak() -> List[Item]:
    out = _run(["flatpak", "list", "--columns=application,name,version,size,origin"])
    items: List[Item] = []
    for line in out.splitlines():
        f = _split_cols(line)
        if len(f) < 2:
            continue
        name, disp = f[0], f[1]
        ver = f[2] if len(f) > 2 else ""
        size_s = f[3] if len(f) > 3 else ""
        origin = f[4] if len(f) > 4 else ""
        size = -1
        m = re.match(r"([\d.]+)\s*([kMGTP]?)B?", size_s.replace(",", ""))
        if m:
            mult = {"": 1, "k": 1024, "M": 1024 ** 2, "G": 1024 ** 3,
                    "T": 1024 ** 4, "P": 1024 ** 5}.get(m.group(2), 1)
            try:
                size = int(float(m.group(1)) * mult)
            except ValueError:
                size = -1
        if disp and disp != name:
            extra = {"显示名": disp}
        else:
            extra = {}
        if origin:
            extra["远程"] = origin
        items.append(Item(name, ver, size, "flatpak 应用", "flatpak", extra))
    return items


def load_pip() -> List[Item]:
    pip = shutil.which("pip3") or shutil.which("pip")
    if not pip:
        return []
    out = _run([pip, "list", "--format=freeze", "--disable-pip-version-check",
                "--no-color"], timeout=45)
    items: List[Item] = []
    for line in out.splitlines():
        line = line.strip()
        if "==" not in line or line.startswith("-"):
            continue
        name, _, ver = line.partition("==")
        items.append(Item(name, ver, -1, "Python 包", "pip", {}))
    return items


#: 各数据源加载时想额外告诉用户的一句话（目前只有 cmd 用）
STATUS = {"note": ""}

CACHE_BASE = (os.environ.get("XDG_CACHE_HOME")
              or os.path.join(os.path.expanduser("~"), ".cache"))
CACHE_DIR = os.path.join(CACHE_BASE, "fishkit")
CMD_CACHE = os.path.join(CACHE_DIR, "cmd-scan.json")
LEGACY_CMD_CACHE = os.path.join(CACHE_BASE, "termtools", "cmd-scan.json")
CMD_MAX_DIR = 20000                      # 单目录最多看这么多条目
CMD_MAX_TOTAL = 60000                    # 全 PATH 上限
#: 后台加载线程里放一个 Event，loader 可以据此提前收工（取消 / 切页）
_TLS = threading.local()


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip() not in ("", "0", "no", "false")


def _cmd_budget() -> float:
    try:
        return max(0.5, float(os.environ.get("PKGVIEW_CMD_BUDGET", "3.0")))
    except ValueError:
        return 3.0


def _slow_dir(d: str) -> str:
    """这些目录扫起来要么慢到像卡死，要么没意义。返回原因，空串表示可以扫。"""
    p = os.path.abspath(d).replace("\\", "/")
    p = re.sub(r"^[A-Za-z]:", "", p)         # 只为在 Windows 上跑测试时也能识别
    while len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    if re.match(r"^/mnt/[A-Za-z](/|$)", p):
        # WSL 的 Windows 互通目录：每次 stat 都要过一次 9p，几万个文件能扫几分钟
        return "windows"
    if p in ("/proc", "/sys", "/dev") or re.match(r"^/(proc|sys|dev)/", p):
        return "virtual"
    return ""


def _cmd_dirs() -> List[str]:
    out: List[str] = []
    for d in os.environ.get("PATH", "").split(os.pathsep):
        d = d.strip()
        if not d:
            continue
        d = os.path.abspath(os.path.expanduser(d))
        if d not in out:
            out.append(d)
    return out


def _cmd_cache_key(dirs: List[str]) -> str:
    h = hashlib.sha1()
    for d in dirs:
        if _slow_dir(d):
            continue
        try:
            st = os.stat(d)
            h.update(("%s|%d|%d;" % (d, st.st_mtime_ns, st.st_ino)).encode("utf-8", "replace"))
        except OSError:
            h.update(("%s|missing;" % d).encode("utf-8", "replace"))
    h.update(("wsl=%d;" % _env_flag("PKGVIEW_CMD_WSL")).encode())
    return h.hexdigest()


def _cmd_cache_read(key: str) -> Optional[Tuple[List[Item], List[str]]]:
    data = None
    # 新路径读不到就退到 0.x 的 ~/.cache/termtools（不搬，写的时候自然落到新目录）
    for path in (CMD_CACHE, LEGACY_CMD_CACHE):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            break
        except (OSError, ValueError):
            continue
    if data is None:
        return None
    if not isinstance(data, dict) or data.get("key") != key:
        return None
    items: List[Item] = []
    for row in data.get("items") or []:
        try:
            name, path, size, link = row
        except (TypeError, ValueError):
            continue
        extra = {"路径": path}
        if link:
            extra["软链"] = link
        items.append(Item(name, "", int(size), "PATH 中的可执行文件", "cmd", extra))
    notes = [str(n) for n in (data.get("notes") or [])]
    return items, notes


def _cmd_cache_write(key: str, items: List[Item], notes: List[str]) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = CMD_CACHE + ".tmp"
        rows = [[it.name, it.extra.get("路径", ""), it.size, it.extra.get("软链", "")]
                for it in items]
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"key": key, "items": rows, "notes": notes}, fh)
        os.replace(tmp, CMD_CACHE)
    except (OSError, ValueError, TypeError):
        pass


def load_cmd(stop: Optional[threading.Event] = None) -> List[Item]:
    """扫描 PATH 里的可执行文件。

    比"能扫多全"更重要的是"别卡住"，所以有三道闸：
      1. 默认跳过 /mnt/<盘符>（Windows 互通目录，drvfs 上一次 stat 能到毫秒级）
      2. 单目录 + 全局的时间预算，到点就带着已扫到的结果收工
      3. 结果落盘缓存，PATH 和各目录 mtime 没变就直接复用
    """
    if stop is None:
        stop = getattr(_TLS, "stop", None)
    dirs = _cmd_dirs()
    include_wsl = _env_flag("PKGVIEW_CMD_WSL")
    notes: List[str] = []
    skipped: List[str] = []
    for d in dirs:
        why = _slow_dir(d)
        if why and not (why == "windows" and include_wsl):
            skipped.append(d)
    key = _cmd_cache_key(dirs)
    if stop is None:
        cached = _cmd_cache_read(key)
        if cached is not None:
            items, notes = cached
            STATUS["note"] = "；".join(notes)
            return items

    budget = _cmd_budget()
    deadline = time.monotonic() + budget
    seen = set()
    items: List[Item] = []
    truncated = False

    for d in dirs:
        if len(items) >= CMD_MAX_TOTAL:
            truncated = True
            break
        if stop is not None and stop.is_set():
            truncated = True
            break
        why = _slow_dir(d)
        if why and not (why == "windows" and include_wsl):
            continue
        if not os.path.isdir(d):
            continue
        n = 0
        try:
            scan_dir = os.scandir(d)
        except OSError:
            continue
        with scan_dir as scan:
            for ent in scan:
                n += 1
                if n > CMD_MAX_DIR:
                    truncated = True
                    break
                if (n & 0xFF) == 0:
                    if time.monotonic() > deadline:
                        truncated = True
                        break
                    if stop is not None and stop.is_set():
                        truncated = True
                        break
                try:
                    st = ent.stat(follow_symlinks=True)
                except OSError:
                    continue
                if not statmod.S_ISREG(st.st_mode) or not (st.st_mode & 0o111):
                    continue
                ident = (st.st_dev, st.st_ino)
                if not st.st_ino:                    # 有的文件系统 inode 恒为 0，退回真路径
                    try:
                        ident = ("path", os.path.realpath(ent.path))
                    except OSError:
                        ident = ("path", ent.path)
                if ident in seen:
                    continue
                seen.add(ident)
                link = ""
                try:
                    if ent.is_symlink():
                        link = ent.path
                        path = os.path.realpath(ent.path)
                    else:
                        path = ent.path
                except OSError:
                    path = ent.path
                extra = {"路径": path}
                if link:
                    extra["软链"] = link
                items.append(Item(ent.name, "", st.st_size, "PATH 中的可执行文件", "cmd", extra))
        if time.monotonic() > deadline:
            truncated = True
            break

    items.sort(key=lambda it: it.name.lower())
    if skipped and not include_wsl:
        notes.append("跳过 %d 个 Windows 互通目录（PKGVIEW_CMD_WSL=1 可包含）" % len(skipped))
    if truncated:
        notes.append("扫描到 %.1fs 预算上限，结果可能不全" % budget)
    STATUS["note"] = "；".join(notes)
    if not truncated and stop is None:
        _cmd_cache_write(key, items, notes)
    return items


LOADERS = {
    "apt": ("apt", load_apt, "dpkg-query"),
    "snap": ("snap", load_snap, "snap"),
    "flatpak": ("flatpak", load_flatpak, "flatpak"),
    "pip": ("pip", load_pip, None),
    "cmd": ("命令", load_cmd, None),
}


def build_sources() -> List[Source]:
    srcs: List[Source] = []

    def all_loader() -> List[Item]:
        merged: List[Item] = []
        for s in srcs:
            if s.key == "all" or not s.available:
                continue
            if s.items is None:
                try:
                    s.items = s.loader()
                except Exception:
                    s.items = []
            merged.extend(s.items)
        return merged

    srcs.append(Source("all", "全部", all_loader, True))
    for key in ("apt", "snap", "flatpak", "pip", "cmd"):
        label, fn, need = LOADERS[key]
        if need is None:
            ok = True
        else:
            ok = shutil.which(need) is not None
        if key == "pip" and ok:
            ok = bool(shutil.which("pip3") or shutil.which("pip"))
        srcs.append(Source(key, label, fn, ok))
    return srcs


# --------------------------------------------------------------------------- #
# TUI
# --------------------------------------------------------------------------- #

class App:
    def __init__(self, scr, sources: List[Source], initial: Optional[str] = None,
                 pattern: str = ""):
        self.scr = scr
        self.sources = sources
        # 默认停在 apt 页（dpkg-query 最快）；"全部"要扫 pip/flatpak，留到按需加载
        self.cur = 0
        want = initial
        if not want:
            want = "apt" if any(s.key == "apt" and s.available for s in sources) else None
        if want:
            for i, s in enumerate(sources):
                if s.key == want:
                    self.cur = i
                    break
        self.pattern = pattern
        self.filter_buf = pattern
        self.saved_pattern = pattern
        self.mode = "list"
        self.sel = 0
        self.top = 0
        self.sort_idx = 0
        self.visible: List[Item] = []
        self.total = 0
        self.regex: Optional[re.Pattern] = None
        self.regex_err = ""
        self.err = ""
        self.help = False
        self.h, self.w = 24, 80
        self.page_rows = 1
        self.C = self._init_colors()
        self.jobs: Dict[int, dict] = {}
        self.loading_label = ""
        self.loading_t0 = 0.0
        self._spinner = 0
        self._load(self.cur)
        self._recompute()

    # ---------------- 颜色 / 基础绘制 ----------------

    def _init_colors(self) -> Dict[str, int]:
        c = {"accent": curses.A_BOLD, "dim": curses.A_DIM,
             "err": curses.A_BOLD, "ok": curses.A_BOLD, "sel": curses.A_REVERSE}
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            curses.init_pair(3, curses.COLOR_RED, -1)
            curses.init_pair(4, curses.COLOR_YELLOW, -1)
            c["accent"] = curses.color_pair(1) | curses.A_BOLD
            c["ok"] = curses.color_pair(2)
            c["err"] = curses.color_pair(3) | curses.A_BOLD
            c["warn"] = curses.color_pair(4)
        except curses.error:
            c["warn"] = curses.A_BOLD
        return c

    def _put(self, y: int, x: int, s: str, attr: int = 0, maxw: Optional[int] = None):
        if y < 0 or y >= self.h or x >= self.w or not s:
            return
        if maxw is None:
            maxw = self.w - x
        if maxw <= 0:
            return
        try:
            self.scr.addstr(y, x, clip(s, maxw), attr)
        except (curses.error, UnicodeEncodeError):
            pass

    def _fill(self, y: int, x: int, w: int, attr: int = 0):
        if y < 0 or y >= self.h or w <= 0 or x >= self.w:
            return
        w = min(w, self.w - x)
        try:
            self.scr.addstr(y, x, " " * w, attr)
        except curses.error:
            pass

    # ---------------- 数据 ----------------

    def current(self) -> Source:
        return self.sources[self.cur]

    def _flash(self, msg: str):
        try:
            self.h, self.w = self.scr.getmaxyx()
        except curses.error:
            pass
        self._fill(self.h - 1, 0, self.w)
        self._put(self.h - 1, 0, " " + msg, self.C.get("warn", 0))
        try:
            self.scr.refresh()
        except curses.error:
            pass

    def _load(self, idx: int, force: bool = False):
        """启动后台线程读数据源，主循环继续转，不再把界面卡死。"""
        src = self.sources[idx]
        if not src.available:
            src.items = []
            return
        if src.items is not None and not force:
            return
        if idx in self.jobs:
            return
        if force:
            src.items = None
        stop = threading.Event()
        job = {"src": src, "stop": stop, "done": False, "items": None, "err": ""}
        self.jobs[idx] = job

        def work():
            _TLS.stop = stop
            try:
                res = src.loader()
                if stop.is_set():
                    return
                job["items"] = res
            except Exception as exc:                 # noqa: BLE001
                job["err"] = str(exc)
            finally:
                job["done"] = True

        th = threading.Thread(target=work, name="load-%s" % src.key, daemon=True)
        job["thread"] = th
        if idx == self.cur:
            self.loading_label = src.label
            self.loading_t0 = time.monotonic()
        th.start()

    def _cancel_load(self, idx: int) -> bool:
        job = self.jobs.pop(idx, None)
        if job is None:
            return False
        try:
            job["stop"].set()
        except Exception:                            # noqa: BLE001
            pass
        self.sources[idx].items = []
        self.sources[idx].note = "已取消读取"
        return True

    def _poll(self) -> bool:
        """收后台结果；有变化返回 True。"""
        changed = False
        for idx in list(self.jobs):
            job = self.jobs[idx]
            if not job["done"]:
                continue
            src = job["src"]
            self.jobs.pop(idx, None)
            if job["err"]:
                src.items = []
                self.err = "%s 读取失败: %s" % (src.label, job["err"])
            else:
                src.items = job["items"] or []
                if idx == self.cur:
                    self.err = ""
            if src.key == "cmd":
                src.note = STATUS.get("note", "")
            changed = True
        if changed:
            self._recompute()
        return changed

    def busy(self) -> bool:
        return self.cur in self.jobs

    def _recompute(self):
        src = self.current()
        items = list(src.items or [])
        self.regex_err = ""
        self.regex = None
        if self.pattern:
            try:
                self.regex = re.compile(self.pattern, re.IGNORECASE)
            except re.error as exc:
                self.regex_err = str(exc)
        if self.regex is not None:
            rx = self.regex
            items = [it for it in items if self._match(rx, it)]

        label, key, rev = SORT_MODES[self.sort_idx]
        if key == "name":
            items.sort(key=lambda it: it.name.lower(), reverse=rev)
        else:
            items.sort(key=lambda it: it.size, reverse=rev)

        total = len(src.items or [])
        self.visible = items
        self.total = total
        self._clamp()

    @staticmethod
    def _match(rx: re.Pattern, it: Item) -> bool:
        for s in (it.name, it.version, it.summary, it.source):
            if s and rx.search(s):
                return True
        for v in it.extra.values():
            if v and rx.search(v):
                return True
        return False

    def _clamp(self):
        n = len(self.visible)
        if n == 0:
            self.sel = 0
            self.top = 0
            return
        self.sel = max(0, min(self.sel, n - 1))
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + self.page_rows:
            self.top = self.sel - self.page_rows + 1
        self.top = max(0, min(self.top, max(0, n - self.page_rows)))

    # ---------------- 绘制 ----------------

    def draw(self):
        try:
            self.h, self.w = self.scr.getmaxyx()
        except curses.error:
            return
        self.scr.erase()
        if self.h < 9 or self.w < 44:
            self._put(0, 0, "  终端太小，至少需要 44x9（当前 %dx%d）" % (self.w, self.h),
                      self.C.get("err", 0))
            self.scr.refresh()
            return

        self._draw_tabs()
        self._draw_filter()
        self._draw_body()
        self._draw_status()
        if self.help:
            self._draw_help()
        try:
            self.scr.refresh()
        except curses.error:
            pass

    def _draw_tabs(self):
        self._fill(0, 0, self.w, curses.A_NORMAL)
        x = 1
        self._put(0, x, "pkgview", self.C["accent"])
        x += dwidth("pkgview") + 2
        for i, s in enumerate(self.sources):
            if x >= self.w - 4:
                break
            if s.items is None:
                cnt = "·" if s.available else "-"
            else:
                cnt = str(len(s.items))
            txt = " %s %s " % (s.label, cnt)
            attr = self.C["sel"] if i == self.cur else (
                curses.A_DIM if not s.available else curses.A_NORMAL)
            self._put(0, x, txt, attr)
            x += dwidth(txt) + 1
        self._fill(0, x, self.w - x, curses.A_DIM)

    def _draw_filter(self):
        y = 1
        self._fill(y, 0, self.w, curses.A_NORMAL)
        label = "正则 "
        self._put(y, 1, label, self.C["accent"])
        x = 1 + dwidth(label)
        editing = self.mode == "filter"
        shown = self.filter_buf if editing else self.pattern
        if shown:
            self._put(y, x, shown, self.C["ok"] if not self.regex_err else self.C["err"])
            x += dwidth(shown)
        if editing:
            self._put(y, x, "▌", self.C["accent"])
        elif not shown:
            self._put(y, x, "(空 = 显示全部，按 / 输入正则)", self.C["dim"])

        right = ""
        if self.regex_err:
            right = "正则错误: " + self.regex_err
            attr = self.C["err"]
        elif self.busy():
            right = "读取中 ..."
            attr = self.C["dim"]
        else:
            right = "匹配 %d / %d" % (len(self.visible), len(self.current().items or []))
            attr = self.C["dim"]
        rx0 = max(x + 2, self.w - dwidth(right) - 1)
        self._put(y, rx0, right, attr, self.w - rx0 - 1)

    def _draw_body(self):
        top = 3
        bottom = self.h - 1
        self.page_rows = max(1, bottom - top)
        lw = max(24, min(int(self.w * 0.52), 66))
        if lw > self.w - 20:
            lw = max(20, self.w - 20)

        ver_w = 14 if lw >= 46 else (11 if lw >= 36 else 0)
        size_w = 9 if lw >= 34 else 0
        name_w = max(6, lw - ver_w - size_w - 2)

        hdr = " " + pad("名称", name_w) + (" " if ver_w else "")
        if ver_w:
            hdr += pad("版本", ver_w)
        if size_w:
            hdr += pad("大小", size_w, ">")
        self._put(2, 0, hdr, self.C["dim"])
        self._put(2, lw, "│", curses.A_DIM)
        self._put(2, lw + 2, "详情", self.C["dim"])

        if self.busy():
            self._draw_loading(top, self.h - 1)
            return

        self._clamp()
        for row in range(self.page_rows):
            y = top + row
            i = self.top + row
            if i >= len(self.visible):
                self._fill(y, 0, lw)
                break
            it = self.visible[i]
            line = " " + pad(it.name, name_w)
            if ver_w:
                line += " " + pad(it.version, ver_w)
            if size_w:
                line += pad(human_size(it.size) if it.size >= 0 else "-", size_w, ">")
            attr = self.C["sel"] if i == self.sel else 0
            if i == self.sel:
                self._fill(y, 0, lw, attr)
                self._put(y, 0, line, attr, lw)
            else:
                self._put(y, 0, line, attr, lw)

        if not self.visible:
            self._put(top, 1, "无匹配项" if self.pattern else "（空）", self.C["dim"])
        # 滚动条
        self._draw_scrollbar(top, lw)

        for y in range(top, bottom):
            self._put(y, lw, "│", curses.A_DIM)

        # 详情面板
        if self.visible and self.sel < len(self.visible):
            it = self.visible[self.sel]
            lines = self._detail_lines(it, max(8, self.w - lw - 3))
            for row, (text, attr) in enumerate(lines[: self.page_rows]):
                self._put(top + row, lw + 2, text, attr,
                          max(1, self.w - lw - 2))

    def _draw_loading(self, top: int, bottom: int):
        """读取中的占位：转圈 + 已等时长 + 取消提示。"""
        self._spinner += 1
        spin = "◐◓◑◒"[self._spinner % 4]
        elapsed = max(0.0, time.monotonic() - self.loading_t0)
        y = top + max(0, (bottom - top) // 3)
        head = " %s 正在读取 %s ...  %.1fs" % (spin, self.loading_label, elapsed)
        self._put(y, 1, head, self.C["accent"])
        hint = " Esc 取消，其余按键照常可用"
        self._put(y + 1, 1, hint, self.C["dim"])
        why = ""
        if self.loading_label == "命令":
            why = " PATH 目录很多时较慢；已自动跳过 Windows 互通目录"
        elif elapsed > 2.5:
            why = " 首次读取会慢一点，之后走缓存"
        if why:
            self._put(y + 2, 1, why, self.C["dim"])

    def _draw_scrollbar(self, top: int, lw: int):
        n = len(self.visible)
        h = self.page_rows
        if n <= h or h <= 0:
            return
        x = lw - 1
        bar_h = max(1, int(h * h / n))
        pos = int((h - bar_h) * (self.top / max(1, n - h)))
        for row in range(h):
            ch = "█" if pos <= row < pos + bar_h else "░"
            self._put(top + row, x, ch, self.C["dim"])

    def _detail_lines(self, it: Item, dw: int) -> List[Tuple[str, int]]:
        lines: List[Tuple[str, int]] = []

        def add(text: str, attr: int = 0):
            for seg in wrap(text, dw):
                lines.append((seg, attr))

        add(it.name, self.C["accent"])
        meta = " · ".join(x for x in (it.source, it.version) if x)
        if meta:
            add(meta, self.C["dim"])
        lines.append(("", 0))
        add(pad("大小", 8) + human_size(it.size))
        for k, v in it.extra.items():
            if v:
                add(pad(k, 8) + v)
        if it.summary:
            lines.append(("", 0))
            for seg in wrap(it.summary, dw):
                lines.append((seg, curses.A_NORMAL))
        return lines

    def _draw_status(self):
        y = self.h - 1
        self._fill(y, 0, self.w)
        if self.err:
            left, attr = " " + self.err, self.C.get("err", curses.A_BOLD)
        elif self.current().note:
            left, attr = " " + self.current().note, self.C["dim"]
        else:
            left, attr = " / 正则  Tab 切换  j/k 移动  s 排序  r 刷新  ? 帮助  q 退出", \
                self.C["dim"]
        total = len(self.visible)
        pos = ("%d/%d" % (self.sel + 1, total)) if total else "0/0"
        self._put(y, 0, left, attr, self.w - dwidth(pos) - 2)
        self._put(y, self.w - dwidth(pos) - 1, pos, self.C["accent"])

    def _draw_help(self):
        rows = [
            ("/", "输入正则表达式（Python re 语法，忽略大小写）"),
            ("Enter / Esc", "确认 / 取消过滤，Esc 在列表页再次按下清除正则"),
            ("j k ↑ ↓", "上下移动        g G  首行 / 末行"),
            ("Ctrl-d Ctrl-u", "下翻 / 上翻页      空格 b 同"),
            ("Tab h l ← →", "切换数据源          1..9 直跳"),
            ("s", "切换排序（名称 / 体积）"),
            ("r", "重新读取当前数据源"),
            ("q", "退出"),
        ]
        bw = min(self.w - 4, 66)
        bh = len(rows) + 6
        y0 = max(0, (self.h - bh) // 2)
        x0 = max(0, (self.w - bw) // 2)
        for i in range(bh):
            self._fill(y0 + i, x0, bw, curses.A_REVERSE)
        self._fill(y0 + 2, x0, bw, curses.A_REVERSE)
        self._put(y0, x0 + 2, " pkgview 快捷键 ", self.C["accent"] | curses.A_REVERSE)
        for i, (k, v) in enumerate(rows):
            self._put(y0 + 3 + i, x0 + 2, pad(k, 16), curses.A_BOLD | curses.A_REVERSE, bw - 2)
            self._put(y0 + 3 + i, x0 + 19, v, curses.A_REVERSE, bw - 20)
        self._put(y0 + bh - 1, x0 + 2, "任意键关闭", curses.A_DIM | curses.A_REVERSE)

    # ---------------- 交互 ----------------

    def run(self):
        curses.curs_set(0)
        while True:
            self._poll()
            self.draw()
            try:
                self.scr.timeout(120 if self.jobs else -1)
            except curses.error:
                pass
            try:
                ch = self.scr.get_wch()
            except curses.error:
                continue
            except KeyboardInterrupt:
                break
            if not self.handle(ch):
                break
        curses.curs_set(0)

    def handle(self, ch) -> bool:
        if self.help:
            self.help = False
            return True
        if self.mode == "filter":
            return self._handle_filter(ch)
        return self._handle_list(ch)

    @staticmethod
    def _is(ch, name: str) -> bool:
        v = getattr(curses, name, None)
        return v is not None and ch == v

    def _handle_filter(self, ch) -> bool:
        if ch in ("\n", "\r", curses.KEY_ENTER):
            self.mode = "list"
            self.pattern = self.filter_buf
            self._recompute()
        elif ch == "\x1b":
            self.mode = "list"
            self.filter_buf = self.saved_pattern
            self.pattern = self.saved_pattern
            self._recompute()
        elif ch in ("\x7f", "\b", curses.KEY_BACKSPACE):
            self.filter_buf = self.filter_buf[:-1]
            self.pattern = self.filter_buf
            self.sel = 0
            self.top = 0
            self._recompute()
        elif ch == "\x15":                       # Ctrl-u 清空
            self.filter_buf = ""
            self.pattern = ""
            self.sel = 0
            self.top = 0
            self._recompute()
        elif isinstance(ch, str) and ch.isprintable():
            self.filter_buf += ch
            self.pattern = self.filter_buf
            self.sel = 0
            self.top = 0
            self._recompute()
        return True

    def _handle_list(self, ch) -> bool:
        if ch in ("q", "Q"):
            return False
        if ch == "\x1b" and self.busy():             # 读取中按 Esc 取消
            self._cancel_load(self.cur)
            self._recompute()
            return True
        if ch == "/":
            self.mode = "filter"
            self.saved_pattern = self.pattern
            self.filter_buf = self.pattern
            return True
        if ch == "\x1b":
            if self.pattern:
                self.pattern = ""
                self.filter_buf = ""
                self.sel = 0
                self.top = 0
                self._recompute()
                return True
            return False
        if ch in ("j",) or self._is(ch, "KEY_DOWN"):
            self._move(1)
        elif ch in ("k",) or self._is(ch, "KEY_UP"):
            self._move(-1)
        elif ch in ("g", "Home") or self._is(ch, "KEY_HOME"):
            self.sel = 0
            self._clamp()
        elif ch in ("G", "End") or self._is(ch, "KEY_END"):
            self.sel = max(0, len(self.visible) - 1)
            self._clamp()
        elif ch == "\x04":                        # Ctrl-d
            self._move(self.page_rows)
        elif ch == "\x15":                        # Ctrl-u
            self._move(-self.page_rows)
        elif ch in (" ", "f") or self._is(ch, "KEY_NPAGE"):
            self._move(self.page_rows)
        elif ch == "b" or self._is(ch, "KEY_PPAGE"):
            self._move(-self.page_rows)
        elif ch == "\t" or ch in ("l", "L") or self._is(ch, "KEY_RIGHT"):
            self._switch(1)
        elif self._is(ch, "KEY_BTAB") or ch in ("h", "H") or self._is(ch, "KEY_LEFT"):
            self._switch(-1)
        elif isinstance(ch, str) and ch.isdigit() and ch != "0":
            i = int(ch) - 1
            if 0 <= i < len(self.sources):
                self._switch_to(i)
        elif ch == "s":
            self.sort_idx = (self.sort_idx + 1) % len(SORT_MODES)
            self._recompute()
        elif ch == "r":
            self._load(self.cur, force=True)
            self._recompute()
        elif ch == "?":
            self.help = True
        return True

    def _move(self, delta: int):
        if not self.visible:
            return
        self.sel = max(0, min(self.sel + delta, len(self.visible) - 1))
        self._clamp()

    def _switch(self, delta: int):
        self._switch_to((self.cur + delta) % len(self.sources))

    def _switch_to(self, idx: int):
        if idx == self.cur:
            return
        self.cur = idx
        self.sel = 0
        self.top = 0
        self._load(idx)
        self._recompute()


# --------------------------------------------------------------------------- #
# 非交互模式
# --------------------------------------------------------------------------- #

def dump(source: str, pattern: str, limit: int) -> int:
    sources = build_sources()
    by_key = {s.key: s for s in sources}
    src = by_key.get(source)
    if src is None:
        sys.stderr.write("未知数据源 %r，可选: %s\n" % (source, ", ".join(by_key)))
        return 2
    try:
        items = src.loader()
    except Exception as exc:                     # noqa: BLE001
        sys.stderr.write("读取 %s 失败: %s\n" % (source, exc))
        return 1
    if pattern:
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            sys.stderr.write("正则错误: %s\n" % exc)
            return 2
        items = [it for it in items if App._match(rx, it)]
    items.sort(key=lambda it: it.name.lower())
    shown = items[:limit] if limit > 0 else items
    for it in shown:
        sys.stdout.write("%-34s %-18s %-9s %s\n" % (
            it.name, it.version[:18], human_size(it.size), it.summary[:60]))
    sys.stderr.write("共 %d 项%s\n" % (
        len(items), "（仅显示前 %d 项）" % limit if len(items) > len(shown) else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="pkgview",
        description="TUI 查看已安装程序，支持正则搜索")
    ap.add_argument("-s", "--source", default=None,
                    choices=["all", "apt", "snap", "flatpak", "pip", "cmd"],
                    help="启动时打开的数据源")
    ap.add_argument("-f", "--filter", default="", help="初始正则")
    ap.add_argument("--dump", nargs="?", const="all", default=None,
                    choices=["all", "apt", "snap", "flatpak", "pip", "cmd"],
                    help="非交互输出模式")
    ap.add_argument("-n", "--limit", type=int, default=0, help="--dump 时的最大条数")
    ap.add_argument("-V", "--version", action="version", version="pkgview 1.0")
    args = ap.parse_args()

    if args.dump:
        return dump(args.dump, args.filter, args.limit)

    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    os.environ.setdefault("ESCDELAY", "25")

    sources = build_sources()
    try:
        curses.wrapper(lambda scr: App(scr, sources, args.source, args.filter).run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
