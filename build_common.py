"""What the three builds leave out, and why.

Read by EasyAI.spec, EasyAI Setup.spec and EasyAI Studio.spec, so the answer
lives in one place rather than drifting between three.

The problem this solves: PyInstaller works out what to include by following
imports, and this machine's Python is a shared one carrying torch, transformers,
opencv and the rest. Anything those pull in that is merely *importable* can end
up inside a viewer's download. Pillow arriving for the Read tab is what made it
obvious - it dragged in numpy, and numpy dragged in 6 MB of OpenBLAS, for code
that never runs.

Everything named here was checked rather than assumed:

  * the Python packages by loading the real window, reading a PNG's prompt,
    grabbing a video frame and calling the engine, then asking sys.modules what
    had actually been imported;
  * the Qt libraries by reading the import tables of the DLLs that are used -
    Qt6Multimedia, Qt6Widgets and Qt6Gui name only Core, Gui and Network
    between them, never Quick, Qml or Pdf.

If a program ever does start using one of these, delete the line. Leaving a
needed module in this list produces an ImportError at run time, in a windowed
build where nobody can see it - so treat additions carefully and rebuild.
"""
from __future__ import annotations

#: Never imported by any of the three programs, on any path.
UNUSED_PACKAGES = [
    # Pulled in through Pillow's optional array interop. Never called, and the
    # single most expensive passenger: numpy itself plus the OpenBLAS it ships.
    "numpy",
    # urllib3 talks TLS through the standard library's ssl module. This arrives
    # only because it is installed, and costs 3.4 MB of compiled Rust.
    "cryptography",
    "yaml",
    # Nothing here draws a chart or opens a Tk window, but both are large and
    # both are easy for a stray import to reach.
    "matplotlib",
    "tkinter",
]

#: Qt modules with no counterpart in this codebase. Everything is QtWidgets;
#: there is no QML, no 3D and no PDF anywhere.
UNUSED_QT = [
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.Qt3DCore",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtOpenGLWidgets",
]

#: Image formats nothing reads. app/prompts.py accepts PNG, WEBP and JPEG, so
#: those plugins stay; AVIF alone is 4 MB of decoder for a format no result is
#: ever saved in.
UNUSED_IMAGE_FORMATS = [
    "PIL.AvifImagePlugin",
]

EXCLUDES = UNUSED_PACKAGES + UNUSED_QT + UNUSED_IMAGE_FORMATS

#: Qt libraries PySide6's PyInstaller hook copies in wholesale, whether or not
#: anything imports the matching Python module - so excluding the module above
#: is not enough to stop the DLL travelling. Matched case-insensitively against
#: the file name.
UNUSED_QT_LIBRARIES = (
    "qt6quick",
    "qt6qml",
    "qt6pdf",
)

#: Deliberately *not* removed, though it looks like an easy 7 MB: Qt falls back
#: to opengl32sw.dll when a machine has no usable OpenGL driver, and this is
#: shipped to viewers whose machines cannot be checked first. A build that
#: cannot draw its own window is a worse outcome than a larger download.
KEPT_ON_PURPOSE = ("opengl32sw.dll",)


def strip_unused(binaries):
    """Drop the collected-but-unreferenced Qt libraries from a build.

    Takes and returns PyInstaller's list of (name, path, kind) tuples.
    """
    kept = []
    for entry in binaries:
        name = str(entry[0]).replace("\\", "/").rsplit("/", 1)[-1].lower()
        if any(name.startswith(unused) for unused in UNUSED_QT_LIBRARIES):
            continue
        kept.append(entry)
    removed = len(binaries) - len(kept)
    print(f"[build_common] left out {removed} unused Qt libraries")
    return kept
