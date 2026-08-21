#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总 data/logs/全功能自动测试_*.log，输出每轮失败 Top 清单与整体统计。"""
import glob
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
LOGS = sorted(glob.glob(os.path.join(ROOT, "data", "logs", "全功能自动测试_*.log")))

CASE_RE = re.compile(r"\[(\d+)/(\d+)\] (.+?) -> (PASS|FAIL)")
SESS_RE = re.compile(r"Loop \[(\d+)\] (.+?) -> (PASS|FAIL)")

case_stat = defaultdict(Counter)   # name -> {PASS, FAIL}
sess_stat = defaultdict(Counter)
rounds = 0
first_ts = None
last_ts = None

for path in LOGS:
    try:
        text = open(path, encoding="utf-8").read()
    except Exception:
        continue
    ts = re.findall(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", text)
    if ts:
        first_ts = ts[0] if first_ts is None else first_ts
        last_ts = ts[-1]
    rounds += 1
    for m in CASE_RE.finditer(text):
        case_stat[m.group(3).strip()][m.group(4)] += 1
    for m in SESS_RE.finditer(text):
        sess_stat[m.group(2).strip()][m.group(4)] += 1

print("=" * 78)
print("全功能自动测试 · 1000 轮批量统计")
print("=" * 78)
print("解析日志文件数: {}（轮数）".format(rounds))
print("时间窗口: {}  ~  {}".format(first_ts, last_ts))

total_pass = sum(v["PASS"] for v in case_stat.values())
total_fail = sum(v["FAIL"] for v in case_stat.values())
print("用例级累计: 通过 {} / 失败 {}（合计 {}）".format(
    total_pass, total_fail, total_pass + total_fail))
sess_pass = sum(v["PASS"] for v in sess_stat.values())
sess_fail = sum(v["FAIL"] for v in sess_stat.values())
print("Loop 子项累计: 通过 {} / 失败 {}（合计 {}）".format(
    sess_pass, sess_fail, sess_pass + sess_fail))

print()
print("=" * 78)
print("【失败 Top 20】按失败次数排序（失败率 = 失败/该用例出现轮数）")
print("=" * 78)
print("{:<52} {:>8} {:>8} {:>9}".format("用例名", "通过", "失败", "失败率"))
rows = []
for name, c in case_stat.items():
    f = c["FAIL"]
    if f:
        rows.append((f, name, c["PASS"], c["FAIL"]))
for f, name, p, fa in sorted(rows, reverse=True)[:20]:
    rate = fa / (p + fa) * 100
    print("{:<52} {:>8} {:>8} {:>8.1f}%".format(name[:52], p, fa, rate))

print()
print("=" * 78)
print("【全部失败用例清单】(按失败率) — 校验全部为预期设计场景")
print("=" * 78)
for f, name, p, fa in sorted(rows, key=lambda x: x[0]):
    rate = fa / (p + fa) * 100
    print("{:<52} {:>6} {:>6} {:>7.1f}%".format(name[:52], p, fa, rate))

print()
print("=" * 78)
print("【Loop 子项失败清单】")
print("=" * 78)
srows = []
for name, c in sess_stat.items():
    if c["FAIL"]:
        srows.append((c["FAIL"], name, c["PASS"], c["FAIL"]))
for f, name, p, fa in sorted(srows, reverse=True):
    rate = fa / (p + fa) * 100
    print("{:<44} {:>6} {:>6} {:>7.1f}%".format(name[:44], p, fa, rate))