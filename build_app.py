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

    print("\n构建完成：{}".format(os.path.join(ROOT, "dist", APP_NAME)))
    print("运行方式：{}{}".format(os.path.join(ROOT, "dist", APP_NAME, APP_NAME), ".exe" if os.name == "nt" else ""))


if __name__ == "__main__":
    build()
