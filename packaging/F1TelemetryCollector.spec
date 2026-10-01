# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys


project_root = Path(SPECPATH).resolve().parent

analysis = Analysis(
    [str(project_root / "collector" / "gui_main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[],
    # hashlib falls back to CPython's built-in SHA implementation. No feature
    # in this offline/UDP application needs HTTPS or the OpenSSL backend.
    hiddenimports=["_sha2"] if sys.version_info >= (3, 13) else ["_sha256", "_sha512"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["ssl", "_ssl", "_hashlib"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="F1TelemetryLab",
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
    version=str(project_root / "packaging" / "windows_version_info.txt"),
)

distribution = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="F1TelemetryLab-1.0",
)

# Ship the player-facing guide beside the EXE on every build.
import shutil
shutil.copy2(project_root / "docs" / "F1TelemetryCollector-使用说明.txt",
             Path(distribution.name) / "F1TelemetryLab-1.0-使用说明.txt")
