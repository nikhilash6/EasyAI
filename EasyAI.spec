# -*- mode: python ; coding: utf-8 -*-
"""EasyAI - the program viewers use.

Built by "Build EXE.bat". A spec rather than a command line because the unused
Qt libraries can only be dropped after PyInstaller has finished collecting
them - see build_common.py for what is left out and why.
"""
import sys

sys.path.insert(0, SPECPATH)
from build_common import EXCLUDES, strip_unused, version_info

a = Analysis(
    ['EasyAI.py'],
    pathex=[],
    binaries=[],
    datas=[('lang', 'lang'), ('assets/icons', 'assets/icons')],
    hiddenimports=['PIL.Image', 'PIL.PngImagePlugin'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES + ['py7zr', 'psutil'],
    noarchive=False,
    optimize=0,
)
a.binaries = strip_unused(a.binaries)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='EasyAI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icons/EasyAI.ico'],
    version=version_info('EasyAI', 'EasyAI - simple AI creation', 'EasyAI.exe'),
)
