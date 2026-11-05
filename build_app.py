"""Build the app into a single distributable folder (PyInstaller --onedir).

Usage:
    python3 build_app.py            # normal build
    python3 build_app.py --onefile  # (optional) build a single exe instead

Output:
    dist/EOL/                       # runnable folder (EOL executable)
        EOL(.exe)                   # app icon = white bg blue "EOL"
        _internal/                  # bundled code + UI/scripts/assets
        data/                       # runtime data (created next to exe)
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(ROOT, "main.py")
ASSETS_DIR = os.path.join(ROOT, "assets")
ICON = os.path.join(ASSETS_DIR, "icon_eol.ico")
APP_NAME = "EOL"

SEP = ";" if os.name == "nt" else ":"


def ensure_assets():
    """(Re)generate logo/icon images if missing."""
    logo = os.path.join(ASSETS_DIR, "logo_zioneer.png")
    ico = os.path.join(ASSETS_DIR, "icon_eol.ico")
    if os.path.exists(logo) and os.path.exists(ico):
        return
    gen = os.path.join(ROOT, "tools", "generate_assets.py")
    if os.path.exists(gen):
        subprocess.check_call([sys.executable, gen])


def add_data(relative_dir):
    src = os.path.join(ROOT, relative_dir)
    return "--add-data={}{}{}".format(src, SEP, relative_dir)


def build():
    ensure_assets()

    opts = [
        MAIN,
        "--name", APP_NAME,
        "--onedir",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--icon", ICON,
        "--hidden-import", "requests",
        "--hidden-import", "yaml",
        "--hidden-import", "paramiko",
        "--hidden-import", "pysoem",
        "--hidden-import", "can",
        "--hidden-import", "zlgcan",
        "--hidden-import", "zlgcan.zlgcan",
        "--hidden-import", "sqlite3",
        "--hidden-import", "_sqlite3",
        "--hidden-import", "csv",
        "--hidden-import", "gzip",
        "--hidden-import", "pathlib",
        "--hidden-import", "errno",
        add_data("UI"),
        add_data("scripts"),
        add_data("assets"),
        add_data("library"),
    ]
    # exclude unused heavy modules to keep the folder smaller
    for mod in ("tkinter", "matplotlib", "scipy", "numpy", "pandas", "PIL"):
        opts += ["--exclude-module", mod]

    # collect the sqlite3 C extension explicitly (hidden-import alone is unreliable)
    try:
        import sysconfig
        destshared = sysconfig.get_config_var("DESTSHARED") or ""
        import glob as _glob
        for pat in ("_sqlite3*.so", "_sqlite3*.pyd"):
            for so in _glob.glob(os.path.join(destshared, pat)):
                opts += ["--add-binary={}:{}".format(so, ".")]
    except Exception:
        pass

    print("PyInstaller options:", " ".join(opts))
    import PyInstaller.__main__ as pyi
    pyi.run(opts)

    # seed example plans so the packaged app opens out-of-the-box
    dist_data_plans = os.path.join(ROOT, "dist", APP_NAME, "data", "plans")
    os.makedirs(dist_data_plans, exist_ok=True)
    src_plans = os.path.join(ROOT, "data", "plans")
    if os.path.isdir(src_plans):
        for name in os.listdir(src_plans):
            if name.endswith((".plan", ".json")):
                shutil.copy2(os.path.join(src_plans, name), os.path.join(dist_data_plans, name))

    # seed images used by picture/pop cases (e.g. eyoushow.jpg)
    src_images = os.path.join(ROOT, "data", "images")
    dist_data_images = os.path.join(ROOT, "dist", APP_NAME, "data", "images")
    if os.path.isdir(src_images):
        os.makedirs(dist_data_images, exist_ok=True)
        for name in os.listdir(src_images):
            src = os.path.join(src_images, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(dist_data_images, name))

    dist_root = os.path.join(ROOT, "dist", APP_NAME)

    # ship documentation next to the executable (offline reference)
    src_docs = os.path.join(ROOT, "docs")
    dist_docs = os.path.join(dist_root, "docs")
    if os.path.isdir(src_docs):
        os.makedirs(dist_docs, exist_ok=True)
        for name in os.listdir(src_docs):
            if name.lower().endswith((".md", ".docx", ".pdf")):
                src = os.path.join(src_docs, name)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(dist_docs, name))
    for name in ("README.md", "requirements.txt"):
        src = os.path.join(ROOT, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dist_root, name))

    write_release_notes(dist_root)
    clean_pycache(dist_root)
    verify_bundle(dist_root)

    print("\n构建完成：{}".format(dist_root))
    print("运行方式：{}{}".format(os.path.join(dist_root, APP_NAME), ".exe" if os.name == "nt" else ""))


def write_release_notes(dist_root):
    """Write a short release note for the person receiving the package."""
    import datetime
    text = """# EOL 测试软件发布包

构建时间：{time}

## 包含内容

- EOL：主程序（PyInstaller onedir，目标机无需安装 Python）
- _internal/scripts：测试脚本（含头部 eyou_head、腰部 Tongchun_waist）
- _internal/library：ZLG CANFD 动态库
- data/plans：测试计划（头部、腰部、CIT、Demo 等）
- data/images：弹窗/图片用例素材（eyoushow.jpg、tongchuan1.jpg、tongchuan2.jpg）
- docs：操作手册、部署文档、脚本接口文档、服务器对接文档等

## 首次运行

1. 将整个 EOL 目录拷贝到目标机（x86_64 Linux，需图形桌面）。
2. Linux 下给网卡权限（头部 USB-CAN 另需 udev 规则）：

   sudo setcap cap_net_raw,cap_net_admin+ep /路径/EOL

3. 运行 ./EOL。首次启动会在程序目录生成 data/ 下的日志、报告、设置等。

## 头部 / 腰部测试

- 头部：data/plans/头部分装线测试.plan
- 头部（2026-10-28 变体，关节测试默认跳过）：data/plans/头部分装线测试20261028.plan
- 腰部：data/plans/腰部分装线测试.plan

计划内脚本使用相对路径 scripts/...，会优先在程序目录 scripts/ 查找，
找不到再使用 _internal/scripts/；把计划拷到其他机器时无需改路径。

## 注意

- PyInstaller 不能跨架构：本包是 x86_64 Linux 产物，ARM 设备需在目标机重新打包。
- 头部 CANFD 使用 scripts/eyou_head/USB_CAN_FD_Driver/libcontrolcanfd.so，
  需要 udev 规则 04d8:0053 -> MODE="0666"。
- 腰部 EtherCAT 需要独立网卡（计划默认 enp2s0）和 CAP_NET_RAW 权限。
""".format(time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    try:
        with open(os.path.join(dist_root, "RELEASE_NOTES.md"), "w", encoding="utf-8") as f:
            f.write(text)
    except Exception as e:
        print("写发布说明失败（不影响主程序）：", e)


def clean_pycache(root):
    """Remove __pycache__ / *.pyc copied from the source tree."""
    for cur, dirs, files in os.walk(root):
        for name in list(dirs):
            if name == "__pycache__":
                shutil.rmtree(os.path.join(cur, name), ignore_errors=True)
                dirs.remove(name)
        for name in files:
            if name.endswith((".pyc", ".pyo")):
                try:
                    os.remove(os.path.join(cur, name))
                except OSError:
                    pass


def verify_bundle(dist_root):
    """Fail the build early if an expected plan/script/doc is missing."""
    required = [
        "EOL",
        "RELEASE_NOTES.md",
        "docs/操作手册.md",
        "docs/部署文档.md",
        "_internal/scripts/eyou_head/eyou_test.py",
        "_internal/scripts/eyou_head/eyou_driver.py",
        "_internal/scripts/Tongchun_waist/tongchuan_test.py",
        "_internal/scripts/Tongchun_waist/tongchuan_driver.py",
        "data/plans/头部分装线测试.plan",
        "data/plans/腰部分装线测试.plan",
        "data/images/eyoushow.jpg",
        "data/images/tongchuan1.jpg",
        "data/images/tongchuan2.jpg",
    ]
    missing = [p for p in required if not os.path.exists(os.path.join(dist_root, p))]
    if missing:
        raise SystemExit("打包产物缺少必要文件：\n  " + "\n  ".join(missing))
    print("产物自检通过：头部/腰部 PLAN、脚本、图片、文档齐全")


if __name__ == "__main__":
    build()
