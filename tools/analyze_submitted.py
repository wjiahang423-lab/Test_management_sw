#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析 mock 服务日志中的全部 JSON 上报，汇总每轮失败 Top 清单与整体统计。

用法：python3 tools/analyze_submitted.py /path/to/mock_server.log
"""
import json
import re
import sys
from collections import Counter, defaultdict

LINE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (.*)$")

case_stat = defaultdict(Counter)   # case_name -> {PASS, FAIL}
child_stat = defaultdict(Counter)  # loop 子项 name -> {PASS, FAIL}
type_stat = defaultdict(lambda: Counter())  # type -> {PASS, FAIL}
rounds = 0
bad_rounds = []
first_ts = last_ts = None

with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
    for raw in f:
        m = LINE_RE.match(raw.rstrip("\n"))
        if not m:
            continue
        ts, text = m.group(1), m.group(2)
        if '"records"' not in text:
            continue
        try:
            payload = json.loads(text)
        except Exception:
            continue
        records = payload.get("records") or []
        rounds += 1
        first_ts = first_ts or ts
        last_ts = ts
        if len(records) != 101:
            bad_rounds.append((ts, len(records)))
        for r in records:
            name = r.get("case_name", "")
            st = r.get("status", "")
            tp = r.get("type", "")
            case_stat[name][st] += 1
            type_stat[tp][st] += 1
            if tp == "loop":
                for it in r.get("list") or []:
                    child_stat[it.get("name", "")][it.get("status", "")] += 1

print("=" * 80)
print("服务器收到的完整上报 · {} 轮".format(rounds))
print("时间窗口: {}  ~  {}".format(first_ts, last_ts))
total_pass = sum(v["PASS"] for v in case_stat.values())
total_fail = sum(v["FAIL"] for v in case_stat.values())
print("用例级累计: 通过 {} / 失败 {}（合计 {}）".format(total_pass, total_fail, total_pass + total_fail))
child_pass = sum(v["PASS"] for v in child_stat.values())
child_fail = sum(v["FAIL"] for v in child_stat.values())
print("Loop 子项累计: 通过 {} / 失败 {}（合计 {}）".format(child_pass, child_fail, child_pass + child_fail))

print()
print("各类型 PASS/FAIL：")
for tp, c in sorted(type_stat.items()):
    print("  {:<12} 通过 {:>6}  失败 {:>6}".format(tp, c["PASS"], c["FAIL"]))

if bad_rounds:
    print()
    print("异常轮(records≠101)：{} 条，如 {}".format(len(bad_rounds), bad_rounds[:3]))
else:
    print()
    print("异常轮：0（每轮都是 101 条记录）")

print()
print("=" * 80)
print("【失败 Top 20】")
print("=" * 80)
rows = [(c["FAIL"], name, c["PASS"], c["FAIL"]) for name, c in case_stat.items() if c["FAIL"]]
print("{:<54} {:>7} {:>7} {:>8}".format("用例名", "通过", "失败", "失败率"))
for f, name, p, fa in sorted(rows, reverse=True)[:20]:
    print("{:<54} {:>7} {:>7} {:>7.1f}%".format(name[:54], p, fa, fa / (p + fa) * 100))

print()
print("=" * 80)
print("【全部失败用例】（按失败率升序，逐条校验是否预期场景）")
print("=" * 80)
for f, name, p, fa in sorted(rows, key=lambda x: x[2] / max(1, x[2] + x[3])):
    print("{:<54} {:>7} {:>7} {:>7.1f}%".format(name[:54], p, fa, fa / (p + fa) * 100))

print()
print("=" * 80)
print("【Loop 子项失败清单】")
print("=" * 80)
crow = [(c["FAIL"], name, c["PASS"], c["FAIL"]) for name, c in child_stat.items() if c["FAIL"]]
for f, name, p, fa in sorted(crow, reverse=True):
    print("{:<44} {:>7} {:>7} {:>7.1f}%".format(name[:44], p, fa, fa / (p + fa) * 100))