"""Checks for the translation system and the shipped catalogues.

Run with:  python -m pytest tests/test_i18n.py -q

The valuable tests here are not about the lookup - that is a dict - but about
the catalogues staying honest as the code changes: every string still present,
every placeholder intact, and nothing accidentally left in English.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app import i18n

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

FIELD = re.compile(r"\{(\w+)\}")
TRANSLATED = [code for code in i18n.LANGUAGES if code != "en"]


@pytest.fixture(autouse=True)
def _restore_language():
    """Every test starts from English and leaves it that way."""
    i18n.load("en")
    yield
    i18n.load("en")


def catalogue(code: str) -> dict[str, str]:
    path = i18n.LANG_DIR / f"{code}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def source_strings() -> list[str]:
    from make_lang import collect
    return collect()


# --- the engine ------------------------------------------------------------
def test_english_is_the_source_so_lookup_is_a_passthrough():
    assert i18n.t("Install") == "Install"
    assert i18n.t("a string nobody has translated") == "a string nobody has translated"


def test_a_missing_translation_falls_back_to_english():
    i18n.load("zh-Hant")
    assert i18n.t("!! not in any catalogue !!") == "!! not in any catalogue !!"


def test_placeholders_are_filled_after_translation():
    i18n.load("zh-Hant")
    out = i18n.t("{n} ready", n=7)
    assert "7" in out and "{n}" not in out


def test_a_broken_placeholder_degrades_to_english_rather_than_crashing(monkeypatch):
    """A translator typo must not take down the window that was about to open."""
    i18n.load("zh-Hant")
    monkeypatch.setitem(i18n._catalogue, "{n} ready", "{nope} 個")
    assert i18n.t("{n} ready", n=3) == "3 ready"


def test_a_damaged_catalogue_does_not_stop_the_program(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "LANG_DIR", tmp_path)
    (tmp_path / "zh-Hant.json").write_text("{ this is not json", encoding="utf-8")
    assert i18n.load("zh-Hant") == "en"
    assert i18n.t("Install") == "Install"


def test_a_catalogue_saved_by_notepad_still_loads(tmp_path, monkeypatch):
    """Windows Notepad writes JSON with a byte order mark, and json.loads
    rejects it. A translator using the most obvious editor must not find their
    whole language silently ignored."""
    monkeypatch.setattr(i18n, "LANG_DIR", tmp_path)
    (tmp_path / "zh-Hant.json").write_text(
        json.dumps({"Install": "安裝"}, ensure_ascii=False), encoding="utf-8-sig")
    assert i18n.load("zh-Hant") == "zh-Hant"
    assert i18n.t("Install") == "安裝"


def test_a_preference_file_with_a_bom_still_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "_preference_file", lambda: tmp_path / "language.json")
    (tmp_path / "language.json").write_text(
        '{"language": "zh-Hans"}', encoding="utf-8-sig")
    assert i18n.remembered() == "zh-Hans"


def test_an_unknown_language_falls_back_to_english():
    assert i18n.load("kl-in") == "en"


def test_plural_picks_the_form_then_translates_the_whole_sentence():
    i18n.load("en")
    assert i18n.plural(1, "{n} model", "{n} models") == "1 model"
    assert i18n.plural(4, "{n} model", "{n} models") == "4 models"
    i18n.load("zh-Hant")
    # Chinese has no plural form, so both English forms map to one translation.
    assert i18n.plural(1, "{n} model", "{n} models") == "1 個模型"
    assert i18n.plural(4, "{n} model", "{n} models") == "4 個模型"


def test_N_marks_without_translating():
    """Table data must not be frozen into whatever language was loaded first."""
    i18n.load("zh-Hant")
    assert i18n.N("Image") == "Image"
    assert i18n.t(i18n.N("Image")) == "圖片"


@pytest.mark.parametrize("locale,expected", [
    ("zh_TW", "zh-Hant"), ("zh_HK", "zh-Hant"), ("zh_MO", "zh-Hant"),
    ("zh_CN", "zh-Hans"), ("zh_SG", "zh-Hans"), ("zh", "zh-Hans"),
    ("en_GB", "en"), ("fr_FR", "en"), ("", "en"),
])
def test_windows_locale_maps_to_the_right_script(locale, expected, monkeypatch):
    """zh-TW and zh-CN are different scripts, not just different countries."""
    monkeypatch.setattr(i18n.locale, "getdefaultlocale", lambda: (locale, "UTF-8"))
    monkeypatch.setitem(i18n.os.environ, "LANG", "")
    assert i18n.detect() == expected


def test_the_saved_choice_beats_the_windows_locale(monkeypatch):
    monkeypatch.setattr(i18n, "remembered", lambda: "zh-Hans")
    monkeypatch.setattr(i18n, "detect", lambda: "zh-Hant")
    assert i18n.start() == "zh-Hans"


def test_available_lists_only_languages_with_a_file():
    found = i18n.available()
    assert found["en"] == "English"
    for code in found:
        assert code == "en" or (i18n.LANG_DIR / f"{code}.json").is_file()


# --- the catalogues --------------------------------------------------------
@pytest.mark.parametrize("code", TRANSLATED)
def test_every_source_string_is_in_the_catalogue(code):
    """A string added to the code but not to the catalogue silently stays English."""
    missing = [s for s in source_strings() if s not in catalogue(code)]
    assert not missing, (f"{len(missing)} strings missing from {code}.json - run "
                         f"python tools/make_lang.py --write:\n"
                         + "\n".join(repr(s[:70]) for s in missing[:10]))


@pytest.mark.parametrize("code", TRANSLATED)
def test_nothing_is_left_untranslated(code):
    blank = [k for k, v in catalogue(code).items() if not v.strip()]
    assert not blank, (f"{len(blank)} untranslated in {code}.json:\n"
                       + "\n".join(repr(s[:70]) for s in blank[:10]))


@pytest.mark.parametrize("code", TRANSLATED)
def test_placeholders_survive_translation(code):
    """A dropped {name} loses information; an invented one crashes the format."""
    wrong = []
    for english, translated in catalogue(code).items():
        if set(FIELD.findall(english)) != set(FIELD.findall(translated)):
            wrong.append((english, translated))
    assert not wrong, "\n".join(
        f"{e[:50]!r}\n   -> {v[:50]!r}" for e, v in wrong[:6])


@pytest.mark.parametrize("code", TRANSLATED)
def test_every_translation_actually_changed(code):
    """Anything identical to the English is almost certainly an oversight.

    A few strings legitimately do not change - units and shorthand that are
    written the same way in Chinese - so those are named rather than guessed.
    """
    same_by_design = {" seconds", "{n} MP", "{w} × {h} px", "{w} × {h} pixels",
                      "CTRL + ENTER  to run", "Image", "Video",
                      # A product name and a version number, written the same
                      # way in every language.
                      "ComfyUI {version}"}
    unchanged = [k for k, v in catalogue(code).items()
                 if v == k and k not in same_by_design]
    assert not unchanged, ("still in English:\n"
                           + "\n".join(repr(s[:70]) for s in unchanged[:10]))


@pytest.mark.parametrize("code", TRANSLATED)
def test_line_structure_is_preserved(code):
    """Message boxes rely on blank lines between paragraphs."""
    wrong = [(k, v) for k, v in catalogue(code).items()
             if k.count("\n") != v.count("\n")]
    assert not wrong, "\n".join(f"{k[:44]!r} lines differ" for k, v in wrong[:6])


@pytest.fixture(scope="module")
def qt_app():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_window_switches_language_without_a_restart(qt_app):
    from app.config import Config
    from app.ui.main_window import MainWindow

    i18n.load("en")
    window = MainWindow(Config())
    tab = window.tabs.widget(0)
    assert window.tabs.tabText(0).endswith("Image")
    assert tab.create_btn.text() == "Create Image"

    i18n.load("zh-Hant")
    window.retranslate()
    assert window.tabs.tabText(0).endswith("圖片")
    assert tab.create_btn.text() == "開始製作圖片"
    assert tab.ratio_picker.heading.text() == "尺寸比例"
    assert tab.gallery.open_folder_btn.text() == "開啟資料夾"
    assert "簡單好用" in window.windowTitle()

    # And back again, so nothing is a one-way trip.
    i18n.load("en")
    window.retranslate()
    assert tab.create_btn.text() == "Create Image"
    window.close()


def test_switching_language_keeps_what_the_user_typed(qt_app):
    """Rebuilding the tab would be simpler and would discard their work."""
    from app.config import Config
    from app.ui.main_window import MainWindow

    i18n.load("en")
    window = MainWindow(Config())
    tab = window.tabs.widget(0)
    tab.prompt_box.setPlainText("a fox in the snow")

    i18n.load("zh-Hans")
    window.retranslate()
    assert tab.prompt_box.toPlainText() == "a fox in the snow"
    assert tab is window.tabs.widget(0), "the tab was replaced, not retranslated"
    window.close()


def test_every_tab_can_retranslate(qt_app):
    """Including the Prompt Helper, which renames the shared widgets."""
    from app.config import Config
    from app.ui.main_window import MainWindow

    i18n.load("zh-Hant")
    window = MainWindow(Config())
    for tab in window.tab_by_mode.values():
        tab.retranslate()
    helper = window.tab_by_mode["prompt-enhancer"]
    assert helper.create_btn.text() == "幫我改寫提示詞"
    assert helper.heading.text() == "改寫後的提示詞"
    window.close()


def test_the_two_chinese_catalogues_are_genuinely_different():
    """A character-converted file would read as a bad machine translation.

    Simplified and Traditional differ in vocabulary, not only in glyphs:
    视频/影片, 文件夹/資料夾, 插件/外掛 are different words.
    """
    hant, hans = catalogue("zh-Hant"), catalogue("zh-Hans")
    assert hant.keys() == hans.keys()
    differing = sum(1 for k in hant if hant[k] != hans[k])
    assert differing > len(hant) * 0.8, "the two files look like the same text"

    assert "影片" in hant["Video"] or hant["Video"] == "影片"
    assert hans["Video"] == "视频"
    assert "資料夾" in hant["Open folder"]
    assert "文件夹" in hans["Open folder"]
