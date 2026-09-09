"""Builds a text-to-image starter workflow from what this ComfyUI actually has.

Model filenames are read back from /object_info rather than hard-coded, so the
generated file is guaranteed to pass the pre-flight check on this machine. Used
to produce an example workflow and to exercise the aspect-ratio path, which
img2img workflows cannot test (they take their size from the input picture).

Run:  python tests/make_starter_workflow.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.comfy.client import ComfyClient
from app.config import Config


def pick(options: list[str], *needles: str) -> str | None:
    """First option containing every needle, case-insensitively."""
    for option in options:
        low = option.lower()
        if all(n.lower() in low for n in needles):
            return option
    return None


def enum_for(info: dict, class_type: str, field: str) -> list[str]:
    spec = info.get(class_type, {}).get("input", {}).get("required", {}).get(field)
    return list(spec[0]) if isinstance(spec, list) and spec and isinstance(spec[0], list) else []


def node(class_type: str, inputs: dict, title: str | None = None) -> dict:
    out = {"inputs": inputs, "class_type": class_type}
    if title:
        out["_meta"] = {"title": title}
    return out


def main() -> int:
    cfg = Config()
    client = ComfyClient(cfg.server)
    if not client.is_alive():
        print("ComfyUI is not running.")
        return 1
    info = client.object_info()

    unets = enum_for(info, "UnetLoaderGGUF", "unet_name")
    clips = enum_for(info, "CLIPLoaderGGUF", "clip_name")
    vaes = enum_for(info, "VAELoader", "vae_name")

    unet = pick(unets, "klein")
    clip = pick(clips, "qwen3-8b") or pick(clips, "qwen_3_8b") or pick(clips, "qwen")
    vae = pick(vaes, "flux2") or pick(vaes, "flux")

    if not all((unet, clip, vae)):
        print(f"Could not find the models needed.\n  unet={unet}\n  clip={clip}\n  vae={vae}")
        return 1
    print(f"unet : {unet}\nclip : {clip}\nvae  : {vae}")

    graph = {
        "1": node("UnetLoaderGGUF", {"unet_name": unet}),
        "2": node("CLIPLoaderGGUF", {"clip_name": clip, "type": "flux2"}),
        "3": node("VAELoader", {"vae_name": vae}),
        "4": node("PrimitiveStringMultiline",
                  {"value": "a calm portrait of a red fox in soft daylight, photographic"},
                  "Prompt"),
        "5": node("CLIPTextEncode", {"text": ["4", 0], "clip": ["2", 0]}, "Positive Prompt"),
        "6": node("ConditioningZeroOut", {"conditioning": ["5", 0]}, "Negative Prompt"),
        # Both of these take the output size and must agree.
        "7": node("EmptyFlux2LatentImage", {"width": 1024, "height": 1024, "batch_size": 1}),
        "8": node("Flux2Scheduler", {"steps": 4, "width": 1024, "height": 1024}),
        "9": node("KSamplerSelect", {"sampler_name": "euler"}),
        "10": node("RandomNoise", {"noise_seed": 12345}),
        "11": node("CFGGuider", {"cfg": 1, "model": ["1", 0],
                                 "positive": ["5", 0], "negative": ["6", 0]}),
        "12": node("SamplerCustomAdvanced", {"noise": ["10", 0], "guider": ["11", 0],
                                             "sampler": ["9", 0], "sigmas": ["8", 0],
                                             "latent_image": ["7", 0]}),
        "13": node("VAEDecode", {"samples": ["12", 0], "vae": ["3", 0]}),
        "14": node("SaveImage", {"filename_prefix": "EasyAI/flux2klein", "images": ["13", 0]}),
    }

    target = cfg.workflow_dir("image") / "Flux 2 Klein - Text to Image.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2, ensure_ascii=False)

    # A stale manifest would hide a detection change, so drop it.
    manifest = target.with_name(target.stem + ".manifest.json")
    manifest.unlink(missing_ok=True)
    print(f"\nwrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
