"""Manual smoke test for the ComfyUI client and launcher.

Run:  python tests/smoke_comfy.py            (probe only, never launches)
      python tests/smoke_comfy.py --launch   (start ComfyUI if it is down)

Checks the plumbing before any UI exists: is the server reachable, does
/object_info parse, how many nodes and models does this install expose.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.comfy.client import ComfyClient
from app.comfy.launcher import ComfyLauncher
from app.config import Config


def main() -> int:
    cfg = Config()
    client = ComfyClient(cfg.server)
    want_launch = "--launch" in sys.argv

    print(f"server        : {cfg.server}")
    print(f"comfyui_dir   : {cfg.get('comfyui_dir')}")

    launcher = ComfyLauncher(
        cfg.get("comfyui_dir"), cfg.get("comfyui_launcher"),
        client, timeout=int(cfg.get("launch_timeout")),
    )
    print(f"launcher path : {launcher.launcher_path}")
    print(f"launchers seen: {launcher.find_launchers()}")
    print(f"validate()    : {launcher.validate() or 'OK'}")

    if not client.is_alive():
        if not want_launch:
            print("\nComfyUI is NOT running. Re-run with --launch to start it.")
            return 1
        result = launcher.ensure_running(auto_launch=True, on_status=lambda m: print("  ", m))
        print(f"launch        : ok={result.ok} {result.message}")
        if not result.ok:
            return 1

    stats = client.system_stats()
    sysinfo = stats.get("system", {})
    print(f"\ncomfyui ver   : {sysinfo.get('comfyui_version')}")
    print(f"python        : {sysinfo.get('python_version', '')[:40]}")
    for dev in stats.get("devices", []):
        vram = dev.get("vram_total", 0) / (1024 ** 3)
        print(f"device        : {dev.get('name')}  ({vram:.1f} GB)")

    info = client.object_info()
    print(f"\nnode types    : {len(info)}")

    # Are the node packs the target workflows need actually present?
    for probe in ("UnetLoaderGGUF", "CLIPTextEncode", "EmptyLatentImage",
                  "LoadImage", "VHS_VideoCombine", "SaveAudio", "LoadAudio",
                  "MiniMaxH3ReferenceToVideo", "ResolutionSelector"):
        print(f"  {'yes' if probe in info else 'NO '}  {probe}")

    # Model lists come from the enum on each loader's input spec.
    for node_name, field in (("UnetLoaderGGUF", "unet_name"),
                             ("UNETLoader", "unet_name"),
                             ("CheckpointLoaderSimple", "ckpt_name")):
        spec = info.get(node_name, {}).get("input", {}).get("required", {}).get(field)
        if spec and isinstance(spec[0], list):
            names = spec[0]
            print(f"\n{node_name}.{field}: {len(names)} entries, e.g.")
            for n in names[:6]:
                print(f"    {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
