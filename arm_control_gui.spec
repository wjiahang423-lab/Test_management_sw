# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.dirname(os.path.abspath(SPEC))
ARM = os.path.join(ROOT, "scripts", "arm")

datas = [
    (os.path.join(ROOT, "tools", "handeye_capture_gui.py"), "tools"),
    (os.path.join(ROOT, "tools", "arm_instruction_set.json"), "tools"),
    (os.path.join(ROOT, "tools", "robot_workbench"), "tools/robot_workbench"),
    (os.path.join(ARM, "arm_driver.py"), "scripts/arm"),
    (os.path.join(ARM, "arm_test.py"), "scripts/arm"),
    (os.path.join(ARM, "arm_enable.py"), "scripts/arm"),
    (os.path.join(ARM, "etherkit_master.py"), "scripts/arm"),
    (os.path.join(ARM, "jog_panel.py"), "scripts/arm"),
    (os.path.join(ARM, "arm_interface.yaml"), "scripts/arm"),
    (os.path.join(ARM, "motion_interface.yaml"), "scripts/arm"),
    (os.path.join(ARM, "cit_items.xml"), "scripts/arm"),
    (os.path.join(ARM, "components_config.yaml"), "scripts/arm"),
]
cv2_datas, cv2_binaries, cv2_hiddenimports = collect_all("cv2")

a = Analysis(
    [os.path.join(ROOT, "tools", "arm_control_gui.py")],
    pathex=[ARM, os.path.join(ROOT, "tools")],
    binaries=cv2_binaries,
    datas=datas + cv2_datas,
    hiddenimports=[
        "yaml", "paramiko",
        "robot_workbench.panel", "robot_workbench.service", "robot_workbench.bundle",
        "arm_driver", "arm_test", "arm_enable", "etherkit_master", "jog_panel",
    ] + cv2_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="arm_control_gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="arm_control_gui",
)
