#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析 mock 服务日志 → 生成 HTML 统计报告（含条形图，纯 CSS 无依赖）。

用法：python3 tools/gen_stats_html.py /path/to/mock_server.log [输出.html]
"""
import html
import json
import re
import sys
from collections import Counter, defaultdict

LINE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (.*)$")

case_stat = defaultdict(Counter)
child_stat = defaultdict(Counter)
type_stat = defaultdict(lambda: Counter())
rounds = 0
first_ts = last_ts = None

src = sys.argv[1]
out = sys.argv[2] if len(sys.argv) > 2 else "data/reports/auto_loop_statistics.html"

with open(src, encoding="utf-8", errors="replace") as f:
    for raw in f:
        m = LINE_RE.match(raw.rstrip("\n"))
        if not m:
            continue
        text = m.group(2)
        if '"records"' not in text:
            continue
        try:
            payload = json.loads(text)
        except Exception:
            continue
        records = payload.get("records") or []
        rounds += 1
        first_ts = first_ts or m.group(1)
        last_ts = m.group(1)
        for r in records:
            name = r.get("case_name", "")
            st = r.get("status", "")
            tp = r.get("type", "")
            case_stat[name][st] += 1
            type_stat[tp][st] += 1
            if tp == "loop":
                for it in r.get("list") or []:
                    child_stat[it.get("name", "")][it.get("status", "")] += 1

case_rows = [(c["FAIL"], name, c["PASS"]) for name, c in case_stat.items() if c["FAIL"]]
child_rows = [(c["FAIL"], name, c["PASS"]) for name, c in child_stat.items() if c["FAIL"]]
top = sorted(case_rows, reverse=True)[:20]


def bar_row(name, p, fa, max_fa):
    rate = fa / (p + fa) * 100
    w = int(rate / max_fa * 100) if max_fa else 0
    color = "#e74c3c" if rate >= 50 else "#f39c12"
    return ("<tr><td class='nm'>{}</td><td>{}</td><td>{}</td><td>"
            "<div class='bar'><i style='width:{}%;background:{}'></i></div></td>"
            "<td class='rt'>{:.1f}%</td></tr>").format(
        html.escape(name[:46]), p, fa, w, color, rate)


type_rows = "".join(
    "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
        tp, c["PASS"], c["FAIL"]) for tp, c in sorted(type_stat.items()))

child_rows_html = "".join(
    bar_row(name, p, fa, max([f for f, _, _ in child_rows] or [1]))
    for fa, name, p in sorted(child_rows, reverse=True))

tot_p = sum(v["PASS"] for v in case_stat.values())
tot_f = sum(v["FAIL"] for v in case_stat.values())
cp = sum(v["PASS"] for v in child_stat.values())
cf = sum(v["FAIL"] for v in child_stat.values())

doc = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>全功能自动测试 · 1000轮统计</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;margin:28px;color:#333}}
h1{{color:#2c5aa0}} h2{{color:#34495e;margin-top:26px;border-bottom:2px solid #eee;padding-bottom:6px}}
table{{border-collapse:collapse;width:100%;margin-top:10px}}
th,td{{border:1px solid #e1e4e8;padding:6px 8px;font-size:13px;text-align:left}}
th{{background:#f8f9fa}} .nm{{width:40%}} .rt{{font-weight:bold;width:90px}}
.bar{{background:#f0f2f5;border-radius:4px;height:16px;min-width:120px}}
.bar i{{display:block;height:16px;border-radius:4px}}
.kpi{{display:inline-block;margin:10px 16px 0 0;padding:10px 18px;border-radius:8px;color:#fff}}
.k1{{background:#27ae60}} .k2{{background:#e74c3c}} .k3{{background:#2c5aa0}} .k4{{background:#95a5a6}}
</style></head><body>
<h1>全功能自动测试 · 服务器端统计报告</h1>
<p>上报轮数：<b>{rounds}</b>（时间 {first} ~ {last}）</p>
<div class="kpi k3">用例级累计 {tot}</div>
<div class="kpi k1">通过 {tp}</div>
<div class="kpi k2">失败 {tf}</div>
<div class="kpi k4">Loop子项 通过 {cp} / 失败 {cf}</div>

<h2>各类型通过/失败</h2>
<table><tr><th>类型</th><th>通过</th><th>失败</th></tr>{type_rows}</table>

<h2>失败用例 Top 20（条形为失败率）</h2>
<table><tr><th>用例</th><th>通过</th><th>失败</th><th>失败率</th><th>比例</th></tr>
{top_rows}</table>

<h2>Loop 子项失败清单</h2>
<table><tr><th>子项</th><th>通过</th><th>失败</th><th>失败率</th><th>比例</th></tr>
{child_rows}</table>
</body></html>""".format(
    rounds=rounds, first=first_ts, last=last_ts, tot=tot_p + tot_f,
    tp=tot_p, tf=tot_f, cp=cp, cf=cf, type_rows=type_rows,
    top_rows="".join(bar_row(n, p, fa, max([f for f, _, _ in top] or [1]))
                     for fa, n, p in top),
    child_rows=child_rows_html)

with open(out, "w", encoding="utf-8") as f:
    f.write(doc)
print("已生成: {} （{} 轮）".format(out, rounds))