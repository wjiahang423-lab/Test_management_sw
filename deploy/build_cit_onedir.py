#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_cit_onedir.py — 把"仅 CIT 接口测试"打包成 PyInstaller onedir(单文件夹)可执行程序。

onedir 形式: 生成 dist/EOL_CIT/ 文件夹, 内含
    EOL_CIT           # Linux/macOS 可执行文件(或 EOL_CIT.exe on Windows)
    _internal/        # 核心代码 + UI/scripts/assets(只带 CIT 相关)
    data/             # 运行数据(可写, 与可执行文件同级)

目标机器无需安装 Python/PyQt5, 直接运行可执行文件即可。

用法:
    python3 build_cit_onedir.py [源目录] [输出目录]

依赖: pip install pyinstaller
"""
import os
import shutil
import subprocess
import sys

DEFAULT_SOURCE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
DEFAULT_OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")

APP_NAME = "EOL_CIT"
CIT_SCRIPTS = ("cit_api.py", "cit_loop_run.yaml", "cit_loop_describe.yaml")
CIT_PLANS = ("CIT接口测试.plan",)
DATA_FILES = ("users.json",)
EXCLUDES = ("tkinter", "matplotlib", "scipy", "numpy", "pandas", "PIL")

SEP = ";" if os.name == "nt" else ":"


def _add_data(root, rel, out_rel):
    return "--add-data={}{}{}".format(os.path.join(root, rel), SEP, out_rel)


def build():
    source = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    output = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT

    main = os.path.join(source, "main.py")
    icon = os.path.join(source, "assets", "icon_eol.ico")
    if not os.path.isfile(main):
        print("[ERROR] 源目录缺少 main.py: {}".format(source))
        return 1

    # 构造仅含 CIT 脚本的临时 scripts 目录
    staging = os.path.join(output, ".cit_staging_scripts")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    for f in CIT_SCRIPTS:
        src = os.path.join(source, "scripts", f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(staging, f))
        else:
            print("[WARN] 缺少脚本: {}".format(f))

    opts = [
        main,
        "--name", APP_NAME,
        "--onedir",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--icon", icon,
        "--hidden-import", "requests",
        "--hidden-import", "yaml",
        "--hidden-import", "paramiko",
        _add_data(source, "UI", "UI"),
        _add_data(staging, ".", "scripts"),
        _add_data(source, "assets", "assets"),
    ]
    for mod in EXCLUDES:
        opts += ["--exclude-module", mod]

    print("PyInstaller options:", " ".join(opts))
    import PyInstaller.__main__ as pyi
    pyi.run(opts)

    dist_app = os.path.join(output, APP_NAME)

    # 只拷贝 CIT 用例
    dist_plans = os.path.join(dist_app, "data", "plans")
    os.makedirs(dist_plans, exist_ok=True)
    for p in CIT_PLANS:
        src = os.path.join(source, "data", "plans", p)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dist_plans, p))

    # 拷贝运行数据
    dist_data = os.path.join(dist_app, "data")
    os.makedirs(dist_data, exist_ok=True)
    for d in DATA_FILES:
        src = os.path.join(source, "data", d)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dist_data, d))

    shutil.rmtree(staging, ignore_errors=True)

    print("\n构建完成: {}".format(dist_app))
    print("运行方式: {}{}".format(os.path.join(dist_app, APP_NAME), ".exe" if os.name == "nt" else ""))
    return 0


if __name__ == "__main__":
    sys.exit(build())
