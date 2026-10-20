#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aptui —— fzf 风格的 apt 包浏览 / 安装 TUI。

列出 apt 缓存里的全部包（不只是已安装的），输入即时模糊搜索，回车安装。

用法:
    ./aptui.py                 # 打开界面
    ./aptui.py -y              # 安装时自动确认（apt-get install -y）
    ./aptui.py --dump -f gcc   # 非交互：打印模糊匹配结果

依赖: 仅 Python 3 标准库（curses）+ apt / dpkg / sudo。

按键（fzf 风格，输入即搜索）:
    直接打字          追加到搜索词（模糊匹配，子序列 + 连续/词首加分）
    Backspace         删一个字符        Ctrl-u  清空搜索词
    ↑ ↓ / Ctrl-p n    上下移动          Ctrl-d  半页下翻   Ctrl-u 已被占用→用 PgDn
    Enter             安装选中的包（会先让你确认）
    Ctrl-x            卸载选中的包（需确认）
    Ctrl-r            重新读取 apt 缓存
    Ctrl-g            帮助
    Esc               清空搜索词，再按一次退出      Ctrl-c / Ctrl-q 退出

说明:
    - 安装/卸载通过 sudo apt-get 在真实终端里执行，期间临时退出 curses。
    - 详情面板里的体积、主页、长描述是选中时才向 apt-cache 查询的（带缓存）。
"""

from __future__ import annotations

import argparse
import curses
import locale
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


# --------------------------------------------------------------------------- #
# 文本宽度工具
# --------------------------------------------------------------------------- #

def char_w(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def dwidth(s: str) -> int:
    return sum(char_w(c) for c in s)


def clip(s: str, w: int) -> str:
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


def wrap(s: str, w: int) -> List[str]:
    if w <= 1:
        return [""]
    brk = " /-_,，、）):："
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
                if rest[j] in brk:
                    cut = j + 1
                    break
            out.append(rest[:cut].rstrip())
            rest = rest[cut:].lstrip(" ")
        out.append(rest)
    return out or [""]


def _fmt(v: float) -> str:
    units = ["B", "K", "M", "G", "T"]
    i = 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024.0
        i += 1
    return ("%d %s" % (v, units[i])) if i <= 1 else ("%.1f %s" % (v, units[i]))


def human_kb(kb: Optional[int]) -> str:
    """apt 的 Installed-Size 单位是 KB。"""
    if kb is None or kb < 0:
        return "-"
    return _fmt(float(kb) * 1024.0)


def human_b(b: Optional[int]) -> str:
    """apt 的 Size 单位是字节。"""
    if b is None or b < 0:
        return "-"
    return _fmt(float(b))


# --------------------------------------------------------------------------- #
# 模糊匹配（fzf 风格打分）
# --------------------------------------------------------------------------- #

BOUNDARY = set(" -_.:/@+~,()[]{}")


def fuzzy(pat: str, text: str) -> Optional[Tuple[float, List[int]]]:
    """子序列匹配，返回 (得分, 命中下标)。不匹配返回 None。"""
    if not pat:
        return 0.0, []
    n, m = len(text), len(pat)
    if m > n:
        return None
    score = 0.0
    pos: List[int] = []
    i = 0
    prev = -2
    for idx in range(n):
        if text[idx] != pat[i]:
            continue
        score += 1.0
        if idx == prev + 1:
            score += 8.0
        if idx == 0:
            score += 12.0
        elif text[idx - 1] in BOUNDARY:
            score += 7.0
        pos.append(idx)
        prev = idx
        i += 1
        if i == m:
            if text.startswith(pat):
                score += 15.0
            score -= n * 0.05          # 同等条件下偏好短名字
            return score, pos
    return None


# --------------------------------------------------------------------------- #
# 数据
# --------------------------------------------------------------------------- #

def _run(cmd: List[str], timeout: float = 60.0,
         env: Optional[Dict[str, str]] = None) -> str:
    run_env = None
    if env:
        run_env = dict(os.environ)
        run_env.update(env)
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=timeout, env=run_env)
        return p.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return ""


@dataclass
class Pkg:
    name: str
    suite: str = ""
    version: str = ""
    arch: str = ""
    installed: bool = False
    installed_ver: str = ""
    upgradable: bool = False


def load_installed() -> Dict[str, str]:
    out = _run(["dpkg-query", "-W", "-f", "${Package}\t${Version}\n"], timeout=40)
    d: Dict[str, str] = {}
    for line in out.splitlines():
        p = line.split("\t")
        if len(p) >= 2 and p[0]:
            d[p[0]] = p[1]
    return d


CACHE_BASE = (os.environ.get("XDG_CACHE_HOME")
              or os.path.join(os.path.expanduser("~"), ".cache"))
CACHE_DIR = os.path.join(CACHE_BASE, "fishkit")
CACHE_FILE = os.path.join(CACHE_DIR, "apt-list.txt")
LEGACY_CACHE = os.path.join(CACHE_BASE, "termtools", "apt-list.txt")


def _migrate_cache() -> None:
    """0.x 把缓存放在 ~/.cache/termtools，遇到就搬到新目录（只搬一次，保留 mtime）。"""
    if os.path.exists(CACHE_FILE) or not os.path.exists(LEGACY_CACHE):
        return
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        os.replace(LEGACY_CACHE, CACHE_FILE)
    except OSError:
        pass


def _apt_lists_stamp() -> float:
    """apt 索引目录的 mtime；apt update 之后它会变，用来判断缓存是否过期。"""
    try:
        return os.path.getmtime("/var/lib/apt/lists")
    except OSError:
        return 0.0


def _cache_fresh() -> bool:
    try:
        return (os.path.exists(CACHE_FILE)
                and os.path.getmtime(CACHE_FILE) >= _apt_lists_stamp())
    except OSError:
        return False


def _parse_apt_list(raw: str) -> List[Pkg]:
    """快解析 apt list 的输出。手工切分比 split() 快一倍多（十几万行）。"""
    pkgs: List[Pkg] = []
    add = pkgs.append
    for line in raw.splitlines():
        if not line or line[0] in " \t":
            continue                     # "Listing..." / WARNING
        i = line.find(" ")
        if i < 0:
            continue
        head = line[:i]
        j = line.find(" ", i + 1)
        if j < 0:
            continue
        k = line.find(" ", j + 1)
        ver = line[i + 1:j]
        arch = line[j + 1:k] if k > 0 else line[j + 1:]
        tail = line[k + 1:] if k > 0 else ""
        name, _, suite = head.partition("/")
        if not name:
            continue
        suite = suite.partition(",")[0]
        installed = head.endswith(",now") or "[installed" in tail
        upgradable = "upgradable" in tail
        iver = ""
        if upgradable:
            p = tail.find("upgradable from:")
            if p >= 0:
                iver = tail[p + 16:].strip(" ])")
        elif installed:
            iver = ver
        add(Pkg(name, suite, ver, arch, installed and not upgradable, iver, upgradable))
    return pkgs


def load_apt_cache(force: bool = False) -> Tuple[List[Pkg], str]:
    """返回 (包列表, 来源说明)。

    首次扫描 apt list 要几秒（十几万包），所以结果落在
    ~/.cache/fishkit/apt-list.txt，之后启动直接读缓存；
    apt update 会更新 /var/lib/apt/lists 的 mtime，缓存自动失效。
    """
    t0 = time.time()
    _migrate_cache()
    raw, how = "", ""
    if not force and _cache_fresh():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
            how = "缓存"
        except OSError:
            raw = ""

    if not raw:
        raw = _run(["apt", "list"], timeout=240, env={"LC_ALL": "C", "LANG": "C"})
        how = "扫描"
        if raw:
            try:
                os.makedirs(CACHE_DIR, exist_ok=True)
                tmp = CACHE_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write(raw)
                os.replace(tmp, CACHE_FILE)
            except OSError:
                pass

    pkgs = _parse_apt_list(raw)
    if not pkgs:                                   # 退化：只有名字
        for name in _run(["apt-cache", "pkgnames"]).split():
            pkgs.append(Pkg(name))
    pkgs.sort(key=lambda p: p.name)
    return pkgs, "%s %.2fs" % (how, time.time() - t0)


def fetch_details(name: str) -> Dict[str, str]:
    out = _run(["apt-cache", "show", name], timeout=10)
    stanza = out.split("\n\n")[0]
    fields: Dict[str, str] = {}
    key = ""
    for line in stanza.splitlines():
        if line[:1] in (" ", "\t") and key:
            fields[key] = fields.get(key, "") + "\n" + line.strip()
        elif ":" in line:
            k, v = line.split(":", 1)
            key = k.strip()
            fields[key] = v.strip()
    return fields


# --------------------------------------------------------------------------- #
# TUI
# --------------------------------------------------------------------------- #

class App:
    def __init__(self, scr, auto_yes: bool = False, query: str = ""):
        self.scr = scr
        self.auto_yes = auto_yes
        self.C = self._init_colors()
        self.h, self.w = 24, 80
        self.page_rows = 1
        self.query = query
        self.sel = 0
        self.top = 0
        self.view: List[Tuple[Pkg, float, List[int]]] = []
        self.pending: Optional[Tuple[str, str]] = None      # (动作, 包名)
        self.message = ""
        self.help = False
        self.detail_cache: Dict[str, Dict[str, str]] = {}
        self.pkgs: List[Pkg] = []
        self.installed: Dict[str, str] = {}
        self.loading = True
        self.load_note = ""
        self.lc: List[str] = []                              # 小写名字，模糊匹配用
        self._prev_q = ""
        self._prev_idx: Optional[List[int]] = None
        self._lock = threading.Lock()                        # 后台取详情
        self._pending: Set[str] = set()
        self._ready: List[Tuple[str, Dict[str, str]]] = []

    # ---------------- 颜色 ----------------

    def _init_colors(self) -> Dict[str, int]:
        c = {"accent": curses.A_BOLD, "dim": curses.A_DIM, "err": curses.A_BOLD,
             "ok": curses.A_BOLD, "sel": curses.A_REVERSE, "hit": curses.A_BOLD,
             "warn": curses.A_BOLD}
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            curses.init_pair(3, curses.COLOR_RED, -1)
            curses.init_pair(4, curses.COLOR_YELLOW, -1)
            curses.init_pair(5, curses.COLOR_MAGENTA, -1)
            c["accent"] = curses.color_pair(1) | curses.A_BOLD
            c["ok"] = curses.color_pair(2)
            c["err"] = curses.color_pair(3) | curses.A_BOLD
            c["warn"] = curses.color_pair(4) | curses.A_BOLD
            c["hit"] = curses.color_pair(5) | curses.A_BOLD
        except curses.error:
            pass
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

    # ---------------- 数据加载 ----------------

    def load(self, first: bool = False, force: bool = False):
        if first or force:
            self.loading = True
            if force or not _cache_fresh():
                self._splash("正在扫描 apt 缓存（十几万个包，约几秒）…")
            else:
                self._splash("正在读取 apt 缓存 …")
        self.installed = load_installed()
        self.pkgs, self.load_note = load_apt_cache(force=force)
        self.lc = [p.name.lower() for p in self.pkgs]
        for p in self.pkgs:
            iv = self.installed.get(p.name)
            if iv:
                p.installed_ver = iv
                p.installed = True
        self.loading = False
        self._prev_q, self._prev_idx = "", None
        self.refresh_view()

    def _splash(self, msg: str):
        try:
            self.h, self.w = self.scr.getmaxyx()
        except curses.error:
            return
        self.scr.erase()
        self._put(max(0, self.h // 2), max(0, (self.w - dwidth(msg)) // 2), msg,
                  self.C["accent"])
        try:
            self.scr.refresh()
        except curses.error:
            pass

    def refresh_view(self):
        q = self.query.strip().lower()
        if not q:
            self.view = [(p, 0.0, []) for p in self.pkgs]
            self._prev_q, self._prev_idx = "", None
            self._clamp()
            return

        # 增量收窄：新词是在旧词后面接着打的，只需在上一轮命中里再筛一遍
        if self._prev_q and q.startswith(self._prev_q) and self._prev_idx is not None:
            cand = self._prev_idx
        else:
            cand = range(len(self.pkgs))

        lc, pkgs = self.lc, self.pkgs
        out: List[Tuple[Pkg, float, List[int]]] = []
        idxs: List[int] = []
        for i in cand:
            r = fuzzy(q, lc[i])
            if r is not None:
                idxs.append(i)
                out.append((pkgs[i], r[0], r[1]))
        out.sort(key=lambda t: (-t[1], t[0].name))
        self.view = out
        self._prev_q, self._prev_idx = q, idxs
        self._clamp()

    def _clamp(self):
        n = len(self.view)
        if n == 0:
            self.sel = self.top = 0
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
            self._put(0, 0, "  终端太小，至少 44x9（当前 %dx%d）" % (self.w, self.h),
                      self.C["err"])
            self.scr.refresh()
            return
        self._draw_header()
        self._draw_query()
        self._draw_list()
        self._draw_status()
        if self.help:
            self._draw_help()
        if self.pending:
            self._draw_confirm()
        try:
            self.scr.refresh()
        except curses.error:
            pass
        if not self.help and not self.pending:
            try:
                curses.curs_set(1)
                self.scr.move(1, min(self.w - 2, 2 + dwidth(self.query)))
            except curses.error:
                pass

    def _draw_header(self):
        installed = sum(1 for p in self.pkgs if p.installed)
        self._put(0, 1, "aptui", self.C["accent"])
        x = 7
        self._put(0, x, "apt 缓存 %d 个包" % len(self.pkgs), curses.A_BOLD)
        x += dwidth("apt 缓存 %d 个包" % len(self.pkgs)) + 2
        self._put(0, x, "已安装 %d" % installed, self.C["ok"])
        x += dwidth("已安装 %d" % installed) + 3
        hint = "Enter 安装  Ctrl-x 卸载  Ctrl-g 帮助  Ctrl-q 退出"
        self._put(0, max(x, self.w - dwidth(hint) - 2), hint, self.C["dim"],
                  self.w - 2)

    def _draw_query(self):
        y = 1
        self._fill(y, 0, self.w)
        self._put(y, 0, "❯", self.C["accent"])
        self._put(y, 2, self.query, self.C["warn"], self.w - 3)
        if not self.query:
            self._put(y, 2, "输入关键词模糊搜索（子序列即可，如 lskr = linux-source）",
                      self.C["dim"], self.w - 3)

    def _draw_list(self):
        top = 3
        bottom = self.h - 1
        self.page_rows = max(1, bottom - top)
        lw = max(30, min(int(self.w * 0.56), 74))
        if lw > self.w - 22:
            lw = max(22, self.w - 22)

        ver_w = 16
        name_w = max(10, lw - 6 - ver_w)
        hdr = "    " + pad("包名", name_w) + " " + pad("候选版本", ver_w)
        self._put(2, 0, hdr, self.C["dim"])
        self._put(2, lw, "│", curses.A_DIM)
        self._put(2, lw + 2, "详情", self.C["dim"])

        self._clamp()
        for row in range(self.page_rows):
            y = top + row
            i = self.top + row
            if i >= len(self.view):
                self._fill(y, 0, lw)
                continue
            pkg, _score, hl = self.view[i]
            selected = i == self.sel
            base = self.C["sel"] if selected else 0
            if selected:
                self._fill(y, 0, lw, base)
            mark = "●" if pkg.installed else "○"
            mark_attr = (self.C["ok"] if pkg.installed else curses.A_DIM)
            if selected:
                mark_attr = base | (curses.A_BOLD if pkg.installed else 0)
            self._put(y, 1, mark, mark_attr)
            if pkg.upgradable:
                self._put(y, 2, "↑", self.C["warn"] | (base if selected else 0))
            hset: Set[int] = set(hl)
            self._put_hl(y, 4, pkg.name, hset, name_w, base)
            ver = pkg.installed_ver or pkg.version
            self._put(y, 5 + name_w, pad(ver, ver_w), curses.A_DIM | base, ver_w)
        if not self.view:
            self._put(top, 1, "无匹配（Backspace 删字符，Ctrl-u 清空）", self.C["dim"])
        self._draw_scrollbar(top, lw)

        for y in range(top, bottom):
            self._put(y, lw, "│", curses.A_DIM)

        if self.view and self.sel < len(self.view):
            self._draw_detail(self.view[self.sel][0], top, lw)

    def _put_hl(self, y, x, text, hl: Set[int], maxw: int, base: int):
        text = clip(text, maxw)
        acc = 0
        for i, ch in enumerate(text):
            cw = char_w(ch)
            if acc + cw > maxw:
                break
            attr = base
            if i in hl:
                attr = (curses.A_REVERSE | curses.A_BOLD) if (base & curses.A_REVERSE) \
                    else self.C["hit"]
            try:
                self.scr.addstr(y, x + acc, ch, attr)
            except (curses.error, UnicodeEncodeError):
                pass
            acc += cw
        if acc < maxw:
            self._fill(y, x + acc, maxw - acc, base)

    def _draw_scrollbar(self, top: int, lw: int):
        n, h = len(self.view), self.page_rows
        if n <= h or h <= 0:
            return
        bar = max(1, int(h * h / n))
        pos = int((h - bar) * (self.top / max(1, n - h)))
        for row in range(h):
            self._put(top + row, lw - 1, "█" if pos <= row < pos + bar else "░",
                      self.C["dim"])

    def _draw_detail(self, pkg: Pkg, top: int, lw: int):
        dw = max(8, self.w - lw - 3)
        lines: List[Tuple[str, int]] = []

        def add(t: str, a: int = 0):
            for seg in wrap(t, dw):
                lines.append((seg, a))

        add(pkg.name, self.C["accent"])
        if pkg.installed_ver:
            add("已安装 " + pkg.installed_ver, self.C["ok"])
        if pkg.upgradable:
            add("可升级 → " + pkg.version, self.C["warn"])
        elif not pkg.installed:
            add("未安装，候选 " + pkg.version, self.C["dim"])
        lines.append(("", 0))
        add("软件源  " + (pkg.suite or "-"))
        add("架构    " + (pkg.arch or "-"))

        d = self.detail_cache.get(pkg.name)
        if d is None:
            if not self.loading:
                add("", 0)
                add("… 查询详情中", self.C["dim"])
        else:
            size = d.get("Size", "")
            isize = d.get("Installed-Size", "")
            try:
                sz = human_b(int(size)) if size else "-"
            except ValueError:
                sz = "-"
            try:
                isz = human_kb(int(isize)) if isize else "-"
            except ValueError:
                isz = "-"
            add("下载    " + sz)
            add("安装后  " + isz)
            for k in ("Section", "Priority", "Maintainer", "Homepage"):
                if d.get(k):
                    add(pad(k, 11) + d[k])
            desc = d.get("Description", "")
            if desc:
                lines.append(("", 0))
                for seg in wrap(desc, dw):
                    lines.append((seg, curses.A_NORMAL))

        for row, (text, attr) in enumerate(lines[: self.page_rows]):
            self._put(top + row, lw + 2, text, attr, max(1, self.w - lw - 2))

    def _draw_status(self):
        y = self.h - 1
        self._fill(y, 0, self.w)
        if self.pending:
            act, name = self.pending
            verb = "安装" if act == "install" else "卸载"
            msg = "确认%s %s ？(y/N)" % (verb, name)
            self._put(y, 1, msg, self.C["warn"], self.w - 2)
            return
        if self.message:
            self._put(y, 1, self.message, self.C["accent"], self.w - 12)
        else:
            hint = "模糊搜索 · Enter 安装 · Ctrl-x 卸载 · Esc 清空/退出"
            if self.load_note:
                hint = "%d 包(%s) · %s" % (len(self.pkgs), self.load_note, hint)
            self._put(y, 1, hint, self.C["dim"], self.w - 12)
        pos = "%d/%d" % (self.sel + 1, len(self.view)) if self.view else "0/0"
        self._put(y, self.w - dwidth(pos) - 1, pos, self.C["accent"])

    def _draw_confirm(self):
        act, name = self.pending
        verb = "安装" if act == "install" else "卸载"
        lines = [
            "即将执行：",
            "  sudo apt-get %s %s" % ("install" if act == "install" else "remove", name),
            "",
            "按 y 确认，其它任意键取消",
        ]
        bw = min(self.w - 6, 62)
        bh = len(lines) + 2
        y0 = max(0, (self.h - bh) // 2)
        x0 = max(0, (self.w - bw) // 2)
        for i in range(bh):
            self._fill(y0 + i, x0, bw, curses.A_REVERSE)
        self._put(y0 + 1, x0 + 2, lines[0], self.C["warn"] | curses.A_REVERSE, bw - 2)
        for i, ln in enumerate(lines[1:], start=2):
            self._put(y0 + i, x0 + 2, ln, curses.A_REVERSE, bw - 2)

    def _draw_help(self):
        rows = [
            ("直接打字", "追加到搜索词（模糊：子序列 + 连续/词首加分）"),
            ("Backspace", "删一个字符        Ctrl-u  清空搜索词"),
            ("↑ ↓ Ctrl-p/n", "上下移动          PgUp/PgDn  翻页"),
            ("g G", "首行 / 末行"),
            ("Enter", "安装选中的包（先弹确认）"),
            ("Ctrl-x", "卸载选中的包（需确认）"),
            ("Ctrl-r", "重新读取 apt 缓存"),
            ("Esc", "清空搜索词；空搜索词时退出"),
            ("Ctrl-q / Ctrl-c", "退出"),
        ]
        bw = min(self.w - 4, 74)
        bh = len(rows) + 4
        y0 = max(0, (self.h - bh) // 2)
        x0 = max(0, (self.w - bw) // 2)
        for i in range(bh):
            self._fill(y0 + i, x0, bw, curses.A_REVERSE)
        self._put(y0 + 1, x0 + 2, " aptui 快捷键 ", self.C["accent"] | curses.A_REVERSE)
        for i, (k, v) in enumerate(rows):
            self._put(y0 + 2 + i, x0 + 2, pad(k, 17), curses.A_BOLD | curses.A_REVERSE, 17)
            self._put(y0 + 2 + i, x0 + 20, v, curses.A_REVERSE, bw - 21)
        self._put(y0 + bh - 1, x0 + 2, "任意键关闭", curses.A_DIM | curses.A_REVERSE)

    # ---------------- 执行外部命令 ----------------

    def _external(self, cmd: List[str]) -> int:
        curses.def_prog_mode()
        curses.endwin()
        try:
            sys.stdout.write("\n$ " + " ".join(cmd) + "\n")
            sys.stdout.flush()
            return subprocess.call(cmd)
        except OSError as exc:
            sys.stderr.write("执行失败: %s\n" % exc)
            return 127
        finally:
            try:
                input("\n[回车返回 aptui]")
            except (EOFError, OSError):
                pass
            try:
                curses.reset_prog_mode()
            except Exception:                        # noqa: BLE001
                pass
            self.scr.clear()
            try:
                self.scr.timeout(120)
            except curses.error:
                pass
            self.scr.refresh()

    def _do(self, act: str, name: str):
        if shutil.which("sudo") is None:
            self.message = "找不到 sudo，无法执行"
            return
        sub = "install" if act == "install" else "remove"
        cmd = ["sudo", "apt-get", sub]
        if act == "install" and self.auto_yes:
            cmd.append("-y")
        cmd.append(name)
        rc = self._external(cmd)
        self.message = ("%s %s 完成 (rc=%d)" % ("安装" if act == "install" else "卸载",
                                              name, rc)) if rc == 0 else \
                       ("%s %s 失败 (rc=%d)" % ("安装" if act == "install" else "卸载",
                                               name, rc))
        self.detail_cache.pop(name, None)
        self.installed = load_installed()
        for p in self.pkgs:
            iv = self.installed.get(p.name)
            p.installed = bool(iv)
            p.installed_ver = iv or p.installed_ver
        self.refresh_view()

    # ---------------- 主循环 ----------------

    # ---------------- 后台取详情 ----------------

    def _request_detail(self, name: str):
        """不阻塞界面：详情丢给后台线程取，取到再画出来。"""
        if name in self.detail_cache or name in self._pending:
            return
        if len(self._pending) > 8:            # 上下键连按时别把线程开爆
            return
        self._pending.add(name)
        threading.Thread(target=self._fetch_worker, args=(name,),
                         daemon=True).start()

    def _fetch_worker(self, name: str):
        try:
            d = fetch_details(name)
        except Exception:                     # noqa: BLE001
            d = {}
        with self._lock:
            self._ready.append((name, d))

    def _poll_details(self) -> bool:
        with self._lock:
            if not self._ready:
                return False
            items, self._ready = self._ready, []
        for name, d in items:
            self._pending.discard(name)
            self.detail_cache[name] = d
        return True

    # ---------------- 主循环 ----------------

    def run(self):
        self.load(first=True)
        try:
            self.scr.timeout(120)             # 每秒最多醒 8 次，用来接收后台结果
        except curses.error:
            pass
        dirty = True
        while True:
            if self._poll_details():
                dirty = True
            if self.view and self.sel < len(self.view):
                self._request_detail(self.view[self.sel][0].name)
            if dirty:
                self.draw()
                dirty = False
            try:
                ch = self.scr.get_wch()
            except curses.error:              # 超时，没有按键
                continue
            except KeyboardInterrupt:
                break
            dirty = True
            if not self.handle(ch):
                break

    @staticmethod
    def _is(ch, name: str) -> bool:
        v = getattr(curses, name, None)
        return v is not None and ch == v

    def handle(self, ch) -> bool:
        if self.help:
            self.help = False
            return True
        if self.pending:
            act, name = self.pending
            self.pending = None
            if ch in ("y", "Y"):
                self._do(act, name)
            return True

        if ch in ("\x03", "\x11"):                       # Ctrl-c / Ctrl-q
            return False
        if ch == "\x07":                                 # Ctrl-g 帮助
            self.help = True
            return True
        if ch == "\x12":                                 # Ctrl-r 重扫
            self.load(force=True)
            self.message = "已重新扫描 (%d 个包, %s)" % (len(self.pkgs), self.load_note)
            return True
        if ch == "\x18":                                 # Ctrl-x 卸载
            if self.view:
                self.pending = ("remove", self.view[self.sel][0].name)
            return True

        if ch in ("\n", "\r", curses.KEY_ENTER):
            if self.view:
                self.pending = ("install", self.view[self.sel][0].name)
            return True
        if ch in ("\x7f", "\b", curses.KEY_BACKSPACE):
            if self.query:
                self.query = self.query[:-1]
                self.sel = self.top = 0
                self.refresh_view()
            return True
        if ch == "\x15":                                 # Ctrl-u
            self.query = ""
            self.sel = self.top = 0
            self.refresh_view()
            return True
        if ch == "\x1b":
            if self.query:
                self.query = ""
                self.sel = self.top = 0
                self.refresh_view()
                return True
            return False
        if self._is(ch, "KEY_DOWN"):
            self.sel = min(len(self.view) - 1, self.sel + 1)
            self._clamp()
            return True
        if self._is(ch, "KEY_UP"):
            self.sel = max(0, self.sel - 1)
            self._clamp()
            return True
        if self._is(ch, "KEY_NPAGE"):
            self.sel = min(len(self.view) - 1, self.sel + self.page_rows)
            self._clamp()
            return True
        if self._is(ch, "KEY_PPAGE"):
            self.sel = max(0, self.sel - self.page_rows)
            self._clamp()
            return True
        if ch == "\x0e":                                 # Ctrl-n
            self.sel = min(len(self.view) - 1, self.sel + 1)
            self._clamp()
            return True
        if ch == "\x10":                                 # Ctrl-p
            self.sel = max(0, self.sel - 1)
            self._clamp()
            return True
        if ch in ("\x01",):                              # Ctrl-a → 首行
            self.sel = 0
            self._clamp()
            return True
        if ch in ("\x05",):                              # Ctrl-e → 末行
            self.sel = max(0, len(self.view) - 1)
            self._clamp()
            return True
        if isinstance(ch, str) and ch.isprintable():
            self.query += ch
            self.sel = self.top = 0
            self.message = ""
            self.refresh_view()
        return True


# --------------------------------------------------------------------------- #
# 非交互
# --------------------------------------------------------------------------- #

def dump(key: str, limit: int, plain: bool = False) -> int:
    pkgs, note = load_apt_cache()
    if key:
        q = key.strip().lower()
        hits = []
        for p in pkgs:
            r = fuzzy(q, p.name.lower())
            if r:
                hits.append((p, r[0]))
        hits.sort(key=lambda t: (-t[1], t[0].name))
        items = [p for p, _ in hits]
    else:
        items = pkgs
    shown = items[:limit] if limit > 0 else items
    for p in shown:
        if plain:
            # 给 fzf 用：标记在最前（列窄被截断也看得见），包名在第二列
            sys.stdout.write("%s %-38s %s\n" % (
                "●" if p.installed else "○", p.name,
                (p.installed_ver or p.version)[:30]))
        else:
            sys.stdout.write("%s%-34s %-20s %s\n" %
                             ("*" if p.installed else " ", p.name,
                              p.installed_ver or p.version, p.suite))
    if not plain:
        sys.stderr.write("共 %d 项（%s）%s\n" % (
            len(items), note,
            "，仅显示前 %d 项" % limit if len(items) > len(shown) else ""))
    return 0


def show(name: str) -> int:
    """fzf 预览用：把一个包的元信息打成几行。"""
    d = fetch_details(name)
    if not d:
        sys.stderr.write("没有找到包: %s\n" % name)
        return 1
    try:
        width = int(os.environ.get("FZF_PREVIEW_COLUMNS") or 78)
    except ValueError:
        width = 78

    out = ["  " + name, ""]
    for label, key in (("版本", "Version"), ("分类", "Section"),
                       ("优先级", "Priority"), ("维护者", "Maintainer"),
                       ("主页", "Homepage")):
        v = (d.get(key) or "").strip()
        if v:
            out.append("  %s  %s" % (label, v))

    size, isize = d.get("Size", ""), d.get("Installed-Size", "")
    try:
        sz = human_b(int(size)) if size else "-"
    except ValueError:
        sz = "-"
    try:
        isz = human_kb(int(isize)) if isize else "-"
    except ValueError:
        isz = "-"
    out.append("  体积    下载 %s / 安装 %s" % (sz, isz))

    desc = (d.get("Description") or "").strip()
    if desc:
        out.append("")
        for line in wrap(desc, max(20, width - 4)):
            out.append("  " + line)
    sys.stdout.write("\n".join(out) + "\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="aptui", description="fzf 风格的 apt 包浏览/安装 TUI")
    ap.add_argument("-y", "--yes", action="store_true", help="安装时自动加 -y")
    ap.add_argument("-q", "--query", default="", help="打开时预填的搜索词")
    ap.add_argument("--dump", nargs="?", const="", default=None, metavar="PATTERN",
                    help="非交互：打印模糊匹配结果")
    ap.add_argument("-n", "--limit", type=int, default=0, help="--dump 的最大条数")
    ap.add_argument("--plain", action="store_true",
                    help="--dump 输出精简列（包名 / 版本 / ●已装 ○未装），给 fzf 用")
    ap.add_argument("--show", metavar="PKG", default=None,
                    help="非交互：打印单个包的详情（fzf 预览用）")
    ap.add_argument("-V", "--version", action="version", version="aptui 1.1")
    args = ap.parse_args()

    if args.show:
        return show(args.show)

    if args.dump is not None:
        return dump(args.dump, args.limit, args.plain)


    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    os.environ.setdefault("ESCDELAY", "25")
    try:
        curses.wrapper(lambda scr: App(scr, args.yes, args.query).run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
