#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fish 静态检查：
   1. 块结构：if/for/while/switch/function/begin 与 end 是否配平
   2. bash 语法残留：${var}、$(cmd)、[[ ]] —— 这几样 fish 不认，写错了只有上机才炸

不是 fish 解析器，但能挡住"少一个 end"和"手滑写成 bash"这两类最常见的问题。
用法: python tests/fishcheck.py file.fish [file2.fish ...]
"""
import re
import sys

OPENERS = {"if", "for", "while", "switch", "function", "begin"}
# 这些词出现在行首时不算块开头
NOT_OPENER = {"else", "case"}

#: (片段, 说明) —— 在单引号字符串之外出现就算错
BASHISMS = (
    ("${", "fish 没有 ${var} 写法，用 $var"),
    ("$(", "命令替换用 (cmd)，不是 $(cmd)"),
    ("[[", "fish 用 test 或 [ ]，没有 [[ ]]"),
)


def strip_comment(line: str) -> str:
    out = []
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < len(line):
                out.append(line[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
        else:
            if ch in "\"'":
                quote = ch
                out.append(ch)
            elif ch == "#" and (i == 0 or line[i - 1].isspace()):
                break
            else:
                out.append(ch)
        i += 1
    return "".join(out)


def strip_single_quoted(line: str) -> str:
    """去掉单引号字符串的内容（fish 单引号里只有 \\' 和 \\\\ 两个转义）。"""
    out = []
    i, n = 0, len(line)
    inside = False
    while i < n:
        ch = line[i]
        if inside:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == "'":
                inside = False
            i += 1
            continue
        if ch == "'":
            inside = True
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def logical_lines(text: str):
    buf = ""
    for raw in text.splitlines():
        line = strip_comment(raw).rstrip()
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        yield buf
        buf = ""


def check(path: str) -> int:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    depth = 0
    stack = []
    bad = 0

    # bash 语法残留：只看代码部分（去掉注释和单引号字符串）
    for no, raw in enumerate(text.splitlines(), 1):
        code = strip_single_quoted(strip_comment(raw))
        for frag, msg in BASHISMS:
            if frag in code:
                print("%s:%d  %s" % (path, no, msg))
                bad += 1

    for no, line in enumerate(logical_lines(text), 1):
        for stmt in line.split(";"):
            words = stmt.strip().split()
            if not words:
                continue
            head = words[0]
            if head in NOT_OPENER:
                continue
            if head in OPENERS:
                # "function foo" 后面紧跟 end；记下来方便报错时定位
                stack.append((no, head))
                depth += 1
            elif "begin" in words:
                # `... ; and begin` 这种也算开一个块
                stack.append((no, "begin"))
                depth += 1
            elif head == "end":
                if depth == 0:
                    print("%s:%d  多余的 end" % (path, no))
                    bad += 1
                else:
                    depth -= 1
                    stack.pop()
    if depth != 0:
        for no, head in stack:
            print("%s:%d  %s 没有对应的 end" % (path, no, head))
        bad += 1
    return bad


def main(argv):
    total = 0
    for p in argv:
        n = check(p)
        print("%-52s %s" % (p, "OK" if n == 0 else "FAIL (%d)" % n))
        total += n
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
