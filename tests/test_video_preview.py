"""Checks for the video first-frame preview.

Run with:  python -m pytest tests/test_video_preview.py -q

Decoding needs a real file and a Qt event loop, so these are skipped where
there are no results to read. The behaviour worth guarding is the awkward part:
a frame arrives before its duration is known, and touching the player from
inside its own callback crashes the decoder.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
VIDEOS = sorted((ROOT / "output" / "video").glob("*.mp4"))

pytestmark = pytest.mark.skipif(not VIDEOS, reason="no video results to read")


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def wait_for(check, timeout_ms: int = 20000) -> None:
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(timeout_ms, loop.quit)
    poll = QTimer()
    poll.timeout.connect(lambda: loop.quit() if check() else None)
    poll.start(50)
    loop.exec()


def test_a_frame_and_its_length_come_back(qt_app):
    from app.ui.video import FrameGrabber

    grabber = FrameGrabber()
    seen = {}
    grabber.ready.connect(lambda p, i, info: seen.update(
        {"path": p, "image": i, "info": info}))
    grabber.request(VIDEOS[0])
    wait_for(lambda: "image" in seen)

    assert seen, "no frame came back"
    assert not seen["image"].isNull()
    assert seen["image"].width() > 0 and seen["image"].height() > 0
    # The duration usually arrives after the frame; reporting the picture
    # without its length, or with the previous file's, is the bug this guards.
    assert seen["info"].seconds > 0
    assert "×" in seen["info"].summary()


def test_a_whole_batch_survives(qt_app):
    """Starting the next file from inside the last one's frame callback
    crashed the decoder outright - a segmentation fault part-way through."""
    from app.ui.video import FrameGrabber

    grabber = FrameGrabber()
    done = []
    grabber.ready.connect(lambda p, i, info: done.append((p, info.seconds)))
    grabber.failed.connect(lambda p: done.append((p, None)))

    wanted = VIDEOS[:6]
    for video in wanted:
        grabber.request(video)
    wait_for(lambda: len(done) >= len(wanted), 60000)

    assert len(done) == len(wanted)
    lengths = [seconds for _, seconds in done if seconds]
    assert lengths, "no durations at all"
    # Every file gets its own length rather than inheriting the last one's.
    assert len(set(lengths)) > 1 or len(wanted) == 1


def test_a_file_that_is_not_there_fails_quietly(qt_app):
    from app.ui.video import FrameGrabber

    grabber = FrameGrabber()
    failed = []
    grabber.failed.connect(failed.append)
    grabber.request(ROOT / "output" / "video" / "no-such-file.mp4")
    wait_for(lambda: bool(failed))
    assert failed, "a missing file should report failure, not hang"


def test_asking_twice_uses_the_cache(qt_app):
    from app.ui.video import FrameGrabber

    grabber = FrameGrabber()
    seen = []
    grabber.ready.connect(lambda p, i, info: seen.append(p))
    grabber.request(VIDEOS[0])
    wait_for(lambda: bool(seen))

    assert grabber.cached(str(VIDEOS[0])) is not None
    grabber.request(VIDEOS[0])          # answered immediately, no decoding
    assert len(seen) == 2


def test_the_gallery_replaces_the_symbol_with_a_thumbnail(qt_app):
    from PySide6.QtCore import Qt

    from app.ui.widgets import ResultGallery

    gallery = ResultGallery()
    gallery.add(VIDEOS[0])
    item = gallery.list.item(0)
    assert item.icon().isNull(), "the icon should arrive later, not block the add"
    assert "🎬" in item.text()

    wait_for(lambda: not gallery.list.item(0).icon().isNull())
    item = gallery.list.item(0)
    assert not item.icon().isNull(), "no thumbnail arrived"
    assert "🎬" not in item.text(), "the symbol is redundant once there is a picture"
    assert item.data(Qt.UserRole) == str(VIDEOS[0])


def test_the_preview_pane_shows_the_frame(qt_app):
    from app.ui.widgets import PreviewPane

    pane = PreviewPane()
    pane.resize(320, 240)
    pane.show_video(VIDEOS[0])

    # Either the frame was already cached and is showing, or the filename is
    # shown while it is fetched. What must never happen is an empty pane with
    # no picture and no explanation.
    showing = pane.pixmap() is not None and not pane.pixmap().isNull()
    assert showing or VIDEOS[0].name in pane.text()

    wait_for(lambda: pane.pixmap() is not None and not pane.pixmap().isNull())
    assert pane.pixmap() and not pane.pixmap().isNull()
    assert pane.text() == ""
    assert "×" in pane.toolTip()
