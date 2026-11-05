"""Install 包检查、Home 配置修改与可回滚部署。可在本机或 RK3588 执行。"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import xml.etree.ElementTree as ET

import yaml

EXECUTABLES = (
    "servo2plan/lib/servo2plan/servo_motor_jiyi1_dualArm_Waist_node",
    "mc_task_manager/lib/mc_task_manager/robot_app",
    "mc_task_manager/lib/mc_task_manager/ros2_node_action_client",
)
DYNAMICS_EXECUTABLES = (
    "mc_dynamics_identification/lib/mc_dynamics_identification/excitation_data_logger_node",
    "mc_dynamics_identification/lib/mc_dynamics_identification/dynamics_identification_cli",
)
META_DIR = "mc_meta_tasks/share/mc_meta_tasks/config/meta_move"
HOME_XML = "mc_task_manager/share/mc_task_manager/config/move_dual_arm_waist_home.xml"
ROOT_FILES = {"manifest.json", "run_arm_dynamics_identification.py",
              "analog_output_snap.py", "workbench_identification_runner.py"}


def stamp():
    return time.strftime("%Y%m%d_%H%M%S") + "_%d" % time.time_ns()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def validate_members(members):
    """只接受 install 树和明确列出的脚本；链接不得越出 install。"""
    seen = set()
    total = 0
    for member in members:
        p = PurePosixPath(member.name)
        if p.is_absolute() or ".." in p.parts or not p.parts:
            raise ValueError("不安全的归档路径：%s" % member.name)
        name = str(p)
        if name in seen:
            raise ValueError("归档包含重复文件：%s" % name)
        seen.add(name)
        if p.parts[0] != "install" and name not in ROOT_FILES:
            raise ValueError("包中包含非部署文件：%s" % name)
        if not (member.isfile() or member.isdir() or member.issym()):
            raise ValueError("不支持的文件类型：%s" % name)
        if member.issym():
            # 运行包不应链接到开发机 build/src 或外部路径。
            target = PurePosixPath(member.linkname)
            combined = os.path.normpath(str(p.parent / target))
            if target.is_absolute() or not combined.startswith("install/"):
                raise ValueError("包中链接越出 install：%s -> %s" % (name, target))
        total += member.size
        if total > 8 * 1024 ** 3:
            raise ValueError("解压后超过 8 GiB，拒绝部署")
    # 不允许其它成员通过包里的链接写入目标文件。
    links = {str(PurePosixPath(m.name)) for m in members if m.issym()}
    for name in seen:
        if any(str(p) in links for p in PurePosixPath(name).parents):
            raise ValueError("归档通过链接写入文件：%s" % name)
    return seen


def inspect_archive(path):
    with tarfile.open(path, "r:*") as archive:
        names = validate_members(archive.getmembers())
        required = {"install/setup.bash"} | {"install/" + p for p in EXECUTABLES}
        missing = sorted(required - names)
        if missing:
            raise ValueError("install 包不完整：" + ", ".join(missing))
        dyn_missing = sorted({"install/" + p for p in DYNAMICS_EXECUTABLES} - names)
        if "run_arm_dynamics_identification.py" not in names:
            dyn_missing.append("run_arm_dynamics_identification.py")
        return {"file": str(Path(path).resolve()), "sha256": sha256(path),
                "dynamics_ready": not dyn_missing, "missing_dynamics": dyn_missing,
                "files": len(names)}


def patch_home(install):
    """XML 引用具名 MetaMove；修改它实际读取的 goal，保留左臂 location。"""
    install = Path(install)
    xml = install / HOME_XML
    tree = ET.parse(xml)
    actions = {a.get("name") for a in tree.iter("Action")}
    if not {"move_left_arm_home", "move_right_arm_home"} <= actions:
        raise ValueError("Home XML 结构与已核实版本不同，停止自动修改")
    left = install / META_DIR / "move_left_arm_home.yaml"
    right = install / META_DIR / "move_right_arm_home.yaml"
    configs = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in (left, right)]
    goals = [c["request"]["goal"] for c in configs]
    if any(not isinstance(g, list) or len(g) != 7 or
           any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in g)
           for g in goals):
        raise ValueError("Home goal 必须为 7 个有限数值")
    if configs[0]["request"].get("component_location") != 1 or configs[1]["request"].get("component_location") != 2:
        raise ValueError("左右臂 location 与预期不符")
    if any(float(v) != 0 for v in goals[1]):
        raise ValueError("右臂 Home 不是全零，请核实机型后再修改")
    original = left.read_text(encoding="utf-8")
    replacement = "  goal: " + json.dumps([float(v) for v in goals[1]])
    changed, count = re.subn(r"^\s*goal:\s*\[[^\n]*\].*$", replacement, original, flags=re.M)
    if count != 1:
        raise ValueError("Home goal 格式不同，停止自动修改")
    backup = left.with_name(left.name + ".bak-" + stamp())
    shutil.copy2(left, backup)
    temporary = left.with_name(left.name + ".tmp-" + stamp())
    temporary.write_text(changed, encoding="utf-8")
    temporary.chmod(left.stat().st_mode & 0o777)
    temporary.replace(left)
    verified = yaml.safe_load(left.read_text(encoding="utf-8"))
    assert verified["request"]["goal"] == goals[1]
    return {"file": str(left), "backup": str(backup), "before": goals[0],
            "after": verified["request"]["goal"], "xml": str(xml)}


def pack(install, output, origin, vendor=None, analog=None, patch=False):
    """始终在副本修改，源 install 不变。没有原厂辨识脚本时明确标记缺失。"""
    install, output = Path(install).resolve(), Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    with tempfile.TemporaryDirectory(prefix="arm-package-") as temporary:
        root = Path(temporary)
        shutil.copytree(install, root / "install", symlinks=True)
        changes = patch_home(root / "install") if patch else None
        for path, name in ((vendor, "run_arm_dynamics_identification.py"),
                           (analog, "analog_output_snap.py")):
            if path and Path(path).is_file():
                shutil.copy2(path, root / name)
        shutil.copy2(Path(__file__).with_name("identification_runner.py"),
                     root / "workbench_identification_runner.py")
        manifest = {"format": 1, "origin": origin, "created": stamp(),
                    "home_patch": changes, "vendor_included": (root / "run_arm_dynamics_identification.py").is_file(),
                    "note": "含原始运控二进制；未自动写入动力学运行参数"}
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        partial = output.with_name(output.name + ".partial")
        try:
            with tarfile.open(partial, "w:gz", dereference=False) as tar:
                for child in sorted(root.iterdir()):
                    tar.add(child, arcname=child.name)
            result = inspect_archive(partial)
            partial.replace(output)
        finally:
            partial.unlink(missing_ok=True)
        result.update(file=str(output), manifest=manifest)
        output.with_name(output.name + ".sha256").write_text(result["sha256"] + "  " + output.name + "\n")
        return result


def ensure_stopped():
    check = subprocess.run(["pgrep", "-f", "[r]obot_app|[s]ervo_motor_jiyi1|[h]w_controller_manager.py"],
                           capture_output=True, text=True)
    if check.returncode == 0:
        raise RuntimeError("运控进程仍在运行，请先停止运控再修改或部署 install")
    if check.returncode != 1:
        raise RuntimeError("无法确认运控进程状态：" + check.stderr)


def deploy(archive_path, home=None):
    """同文件系统内原子切换 install；出错恢复原 install 和脚本。"""
    ensure_stopped()
    info = inspect_archive(archive_path)
    home = Path(home or Path.home()).resolve()
    current = home / "install"
    if current.is_symlink():
        raise ValueError("~/install 是符号链接，请先人工核实部署位置")
    work = home / ".arm_workbench" / stamp()
    work.mkdir(parents=True)
    stage = work / "stage"
    stage.mkdir()
    with tarfile.open(archive_path) as tar:
        members = tar.getmembers()
        validate_members(members)
        for member in members:
            member.mode &= 0o777
        tar.extractall(stage)
    for item in EXECUTABLES + DYNAMICS_EXECUTABLES:
        executable = stage / "install" / item
        if executable.is_file():
            executable.chmod(executable.stat().st_mode | 0o111)
    scripts = sorted(ROOT_FILES - {"manifest.json"})
    backup = work / "previous"
    backup.mkdir()
    swapped = False
    modified = []
    try:
        ensure_stopped()
        for name in scripts:
            source = stage / name
            if not source.is_file():
                continue
            target = home / name
            if target.exists() or target.is_symlink():
                shutil.copy2(target, backup / name, follow_symlinks=False)
            modified.append(name)
            pending = work / (name + ".new")
            shutil.copy2(source, pending)
            pending.replace(target)
            target.chmod(target.stat().st_mode | 0o111)
        if current.exists():
            current.rename(backup / "install")
        try:
            (stage / "install").rename(current)
        except Exception:
            if (backup / "install").exists():
                (backup / "install").rename(current)
            raise
        swapped = True
    finally:
        if not swapped:
            for name in reversed(modified):
                target = home / name
                target.unlink(missing_ok=True)
                if (backup / name).exists() or (backup / name).is_symlink():
                    shutil.copy2(backup / name, target, follow_symlinks=False)
    return dict(info, installed=str(current), backup=str(backup))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("archive")
    patch = sub.add_parser("patch-home")
    patch.add_argument("install")
    package = sub.add_parser("pack")
    package.add_argument("install")
    package.add_argument("output")
    package.add_argument("--origin", required=True)
    package.add_argument("--vendor")
    package.add_argument("--analog")
    package.add_argument("--patch-home", action="store_true")
    deployment = sub.add_parser("deploy")
    deployment.add_argument("archive")
    args = parser.parse_args()
    if args.operation == "inspect":
        result = inspect_archive(args.archive)
    elif args.operation == "patch-home":
        ensure_stopped()
        result = patch_home(args.install)
    elif args.operation == "pack":
        result = pack(args.install, args.output, args.origin, args.vendor, args.analog, args.patch_home)
    else:
        result = deploy(args.archive)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
