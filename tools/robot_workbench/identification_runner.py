#!/usr/bin/env python3
"""SOP 辨识入口封装：调用原厂程序、转发交互、记录本次新增/变更的数据。

不实现或替代原厂动力学模型/激励轨迹；依赖同目录的
run_arm_dynamics_identification.py 及 install/mc_dynamics_identification。
默认不发布参数，APPLY 必须由界面操作员显式输入。
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


def snapshot(root):
    found = {}
    if root.exists():
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                stat = path.stat()
                found[str(path.relative_to(root))] = [stat.st_mtime_ns, stat.st_size]
    return found


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("arm", choices=("left", "right"))
    p.add_argument("serial")
    p.add_argument("--home", default=str(Path.home()))
    p.add_argument("--check", action="store_true")
    args = p.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.serial):
        p.error("机械臂编号只允许字母、数字、下划线和短横线")
    home = Path(args.home).resolve()
    vendor = home / "run_arm_dynamics_identification.py"
    required = [vendor, home / "install/setup.bash"] + [
        home / "install/mc_dynamics_identification/lib/mc_dynamics_identification" / name
        for name in ("excitation_data_logger_node", "dynamics_identification_cli")]
    missing = [str(x) for x in required if not x.is_file()]
    if missing:
        print("缺少原厂辨识文件，未启动运动：" + ", ".join(missing), flush=True)
        return 2
    if args.check:
        print(json.dumps({"ready": True, "arm": args.arm, "serial": args.serial}), flush=True)
        return 0
    data = home / "experiment_data"
    before = snapshot(data)
    start = time.time()
    # 继承标准输入，让 UI 的“发布/跳过”直接回应原厂 APPLY 提示。
    result = subprocess.run([sys.executable, "-u", str(vendor), args.arm, args.serial], cwd=home)
    changed = [name for name, value in snapshot(data).items() if before.get(name) != value]
    report = {"arm": args.arm, "serial": args.serial, "started": start,
              "finished": time.time(), "returncode": result.returncode,
              "files": changed,
              "status": "程序正常结束，验收结论请查看原厂输出" if result.returncode == 0 else "辨识程序失败"}
    for name in changed:
        path = data / name
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        report.setdefault("sha256", {})[name] = h
    reports = home / ".arm_workbench" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    output = reports / (args.arm + "_" + args.serial + "_" + str(time.time_ns()) + ".json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WORKBENCH_REPORT=" + str(output), flush=True)
    if result.returncode == 0 and not changed:
        print("程序退出但没有新增或变更的 experiment_data，不能确认辨识结果。", flush=True)
        return 3
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
