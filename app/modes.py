"""The generation modes and what each one needs from the UI.

Keeping this in one table means a tab is mostly a declaration, not code, and the
loader / gallery / job runner can all ask the same question ("does this mode use
aspect ratios?") without a chain of isinstance checks.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.i18n import N


@dataclass(frozen=True)
class Mode:
    key: str
    label: str
    icon: str
    #: Does the output have pixel dimensions the user should control?
    uses_ratio: bool
    #: File extensions this mode's results can have, for gallery playback.
    result_kinds: tuple[str, ...]
    #: Placeholder shown in the empty prompt box.
    prompt_hint: str
    #: Rows in ComfyUI's history "outputs" dict that hold this mode's results.
    output_keys: tuple[str, ...] = ("images", "gifs", "audio", "video")
    #: Loader bindings this mode may expose when the manifest has them.
    loaders: tuple[str, ...] = field(default=("image_in",))
    #: True when the result is text rather than a file, which changes both how
    #: the result is collected and what the right-hand panel shows.
    produces_text: bool = False
    #: True when the user may choose the output's total pixel count.
    uses_megapixels: bool = False


MODES: dict[str, Mode] = {
    "image": Mode(
        key="image",
        label=N("Image"),
        icon="\U0001F5BC",  # framed picture
        uses_ratio=True,
        result_kinds=("png", "jpg", "jpeg", "webp"),
        prompt_hint=N("Describe the picture you want to create..."),
        output_keys=("images",),
        loaders=("image_in",),
        uses_megapixels=True,
    ),
    "video": Mode(
        key="video",
        label=N("Video"),
        icon="\U0001F3AC",  # clapper board
        uses_ratio=True,
        result_kinds=("mp4", "webm", "gif", "mkv"),
        prompt_hint=N("Describe the video you want to create..."),
        uses_megapixels=True,
        output_keys=("gifs", "videos", "images"),
        loaders=("image_in", "audio_in", "video_in"),
    ),
    "prompt-enhancer": Mode(
        key="prompt-enhancer",
        label=N("Prompt Helper"),
        icon="\U00002728",  # sparkles
        uses_ratio=False,
        result_kinds=("txt",),
        prompt_hint=N("Type a rough idea, like: a fox in the snow"),
        # ComfyUI returns preview text as {"<node>": {"text": ["..."]}}.
        output_keys=("text", "string"),
        loaders=("image_in",),
        produces_text=True,
    ),
}

MODE_ORDER = ("image", "video", "prompt-enhancer")


def get_mode(key: str) -> Mode:
    return MODES[key]
