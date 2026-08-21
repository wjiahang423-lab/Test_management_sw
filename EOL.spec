# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['/home/jyzn/Test_management_sw/main.py'],
    pathex=[],
    binaries=[],
    datas=[('/home/jyzn/Test_management_sw/UI', 'UI'), ('/home/jyzn/Test_management_sw/scripts', 'scripts'), ('/home/jyzn/Test_management_sw/assets', 'assets')],
    hiddenimports=['requests', 'yaml', 'paramiko'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'numpy', 'pandas', 'PIL'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='EOL',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['/home/jyzn/Test_management_sw/assets/icon_eol.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='EOL',
)
