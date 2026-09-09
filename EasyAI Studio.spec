# -*- mode: python ; coding: utf-8 -*-
"""EasyAI Studio - the publishing tool, for GarionHK not viewers.

Built by "Build EXE.bat". A spec rather than a command line because the unused
Qt libraries can only be dropped after PyInstaller has finished collecting
them - see build_common.py for what is left out and why.
"""
import sys

sys.path.insert(0, SPECPATH)
from build_common import EXCLUDES, strip_unused

a = Analysis(
    ['EasyAIStudio.py'],
    pathex=[],
    binaries=[],
    datas=[('lang', 'lang'), ('assets/icons', 'assets/icons'), ('tools', 'tools'), ('setup/catalog.json', 'setup'), ('setup/licences', 'setup/licences'), ('workflows', 'workflows')],
    hiddenimports=['py7zr', 'psutil', 'PIL.Image', 'PIL.PngImagePlugin'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES + [],
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
    name='EasyAI Studio',
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
)
