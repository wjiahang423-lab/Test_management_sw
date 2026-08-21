#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_cit_package.py — 打包"仅 CIT 接口测试"精简部署包

仅包含运行测试管理系统所必需的核心代码，以及 CIT 接口测试相关的
测试脚本与测试用例，方便部署到树莓派等远程 Linux 主机。

包含内容:
    核心代码: main.py, app/, UI/, assets/, requirements.txt
    CIT 相关: scripts/cit_api.py
              scripts/cit_loop_run.yaml
              scripts/cit_loop_describe.yaml
              data/plans/CIT接口测试.plan
    运行数据: data/users.json  (admin/admin, 免登录直接进入)

用法:
    python3 build_cit_package.py [源目录] [输出 tar.gz]

默认值:
    源目录: 脚本上级目录 ../  (即 Test_management_sw)
    输出:   ./EOL_CIT.tar.gz  (deploy 目录下)

退出码: 0 成功, 1 失败
"""
import os
import sys
import tarfile

DEFAULT_SOURCE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
DEFAULT_OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "EOL_CIT.tar.gz")

# 核心代码（必须）
CORE_DIRS = ("app", "UI", "assets")
CORE_FILES = ("main.py", "requirements.txt")

# CIT 测试相关（精简：只带 CIT）
RELATIVE_FILES = (
    "scripts/cit_api.py",
    "scripts/cit_loop_run.yaml",
    "scripts/cit_loop_describe.yaml",
    "data/plans/CIT接口测试.plan",
    "data/users.json",
)


def main():
    source = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    output = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT

    if not os.path.isdir(source):
        print("[ERROR] 源目录不存在: {}".format(source))
        return 1

    arcroot = os.path.basename(source.rstrip(os.sep)) or "EOL"

    def root_path(*parts):
        return os.path.join(source, *parts)

    # 校验必需路径
    missing = [p for p in RELATIVE_FILES
               if not os.path.exists(root_path(*p.split("/")))]
    missing_dirs = [d for d in CORE_DIRS if not os.path.isdir(root_path(d))]
    if missing or missing_dirs:
        print("[ERROR] 下列文件/目录缺失，无法打包:")
        for m in missing + missing_dirs:
            print("  " + m)
        return 1

    def include(member):
        if "__pycache__" in member.name or member.name.endswith(".pyc"):
            return None
        return member

    print("[INFO] 源目录: {}".format(source))
    print("[INFO] 输出文件: {}".format(output))
    print("[INFO] 正在打包 CIT 精简部署包 ...")

    items = (
        [root_path(d) for d in CORE_DIRS]
        + [root_path(f) for f in CORE_FILES]
        + [root_path(*p.split("/")) for p in RELATIVE_FILES]
    )

    try:
        with tarfile.open(output, "w:gz") as tf:
            for it in items:
                tf.add(it, arcname=os.path.join(arcroot, os.path.relpath(it, source)),
                       filter=include)
    except (OSError, tarfile.TarError) as exc:
        print("[ERROR] 打包失败: {}".format(exc))
        return 1

    print("[OK] 打包完成: {} ({:.1f} KB)".format(output, os.path.getsize(output) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
