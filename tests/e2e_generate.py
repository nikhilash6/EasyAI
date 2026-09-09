"""End-to-end generation against the live ComfyUI, without any UI.

Runs the real code path - load workflow, autodetect manifest, pre-flight check,
patch, upload, queue, follow websocket, download - and reports what came back.

Run:  python tests/e2e_generate.py <mode> [workflow name fragment]
e.g.  python tests/e2e_generate.py image
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.comfy import objectinfo
from app.comfy.client import ComfyClient
from app.config import Config
from app.modes import MODES
from app.workflows.loader import scan
from app.workflows.patch import GenerationRequest, apply as patch_apply


def make_test_image(path: Path, size=(768, 1152)) -> Path:
    """A simple gradient, so img2img workflows have something to chew on."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", size, "#20304a")
    draw = ImageDraw.Draw(img)
    for y in range(size[1]):
        shade = int(40 + 120 * y / size[1])
        draw.line([(0, y), (size[0], y)], fill=(shade, shade // 2 + 30, 120))
    draw.ellipse([size[0] * 0.25, size[1] * 0.2, size[0] * 0.75, size[1] * 0.55],
                 fill=(220, 200, 175))
    img.save(path)
    return path


def main() -> int:
    mode_key = sys.argv[1] if len(sys.argv) > 1 else "image"
    needle = sys.argv[2].lower() if len(sys.argv) > 2 else ""
    mode = MODES[mode_key]

    cfg = Config()
    client = ComfyClient(cfg.server, http_timeout=120)
    if not client.is_alive():
        print("ComfyUI is not running.")
        return 1

    workflows = scan(cfg.get("workflow_dir"), mode_key)
    if needle:
        workflows = [w for w in workflows if needle in w.name.lower()]
    if not workflows:
        print(f"No {mode_key} workflows found in {cfg.workflow_dir(mode_key)}")
        return 1

    caps = objectinfo.fetch(client)
    objectinfo.check_all(workflows, caps)

    print(f"--- {mode_key} workflows ---")
    for w in workflows:
        print(f"  {w.status_icon()} {w.name}: {w.status_text().splitlines()[0]}")

    wf = next((w for w in workflows if w.ready), None)
    if wf is None:
        print("\nNothing is ready to run.")
        return 1

    m = wf.manifest
    print(f"\nrunning   : {wf.name}")
    print(f"engine    : {m.engine}")
    for key in ("prompt", "negative", "image_in", "audio_in", "video_in",
                "width", "height", "ratio", "seed", "length", "output"):
        targets = m.all(key)
        if targets:
            print(f"  {key:<9}: {[f'{b.node}.{b.input}' for b in targets]}")

    request = GenerationRequest(prompt="a calm portrait of a fox, soft daylight, photographic")
    if mode.uses_ratio and m.can_set_ratio:
        request.ratio = "2:3"

    # Fill every file slot the workflow asks for - first/last frame workflows
    # want more than one.
    for index, slot in enumerate(m.file_slots("image"), start=1):
        test_img = make_test_image(
            Path(cfg.get("output_dir")) / f"_e2e_input_{index}.png")
        print(f"input {index}   : {m.label_for(slot)} <- {test_img.name}")
        request.files[slot] = test_img
        request.uploaded[slot] = client.upload_file(test_img)
    for slot in m.file_slots("audio") + m.file_slots("video"):
        print(f"!! this workflow also needs a {m.label_for(slot)}; supply one by hand")

    # The app reads this from the running ComfyUI; do the same here or a
    # ratio-chooser node gets left alone and the run silently uses its default.
    ratio_options = None
    if m.has("ratio") and not m.can_set_size:
        binding = m.get("ratio")
        class_type = str((wf.graph.get(binding.node) or {}).get("class_type") or "")
        ratio_options = caps.enum_options(class_type, binding.input)
        print(f"ratio opts: {ratio_options}")

    graph, report = patch_apply(wf.graph, m, request, ratio_options=ratio_options)
    print(f"patched   : applied={report.applied} seed={report.seed} "
          f"size={report.width}x{report.height}")
    for s in report.skipped:
        print(f"  skipped: {s}")

    started = time.time()
    prompt_id, client_id = client.queue(graph)
    print(f"queued    : {prompt_id}")

    previews = [0]

    def on_progress(pct, msg):
        print(f"\r  {msg:<40}", end="", flush=True)

    def on_preview(_data):
        previews[0] += 1

    finished = client.listen(client_id, prompt_id, on_progress=on_progress,
                             on_preview=on_preview, timeout=1800)
    print(f"\nfinished  : ws_ok={finished} live_previews={previews[0]}")

    results = client.wait_for_results(prompt_id, want=mode.output_keys,
                                      finished=finished, timeout=600)
    if not results:
        print("No output files came back.")
        return 1

    out_dir = cfg.output_dir(mode_key)
    for r in results:
        saved = client.save_result(r, out_dir, stem=f"e2e_{mode_key}")
        size = saved.stat().st_size
        line = f"saved     : {saved}  ({size/1024:.0f} KB, from '{r.kind}')"
        if saved.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            from PIL import Image
            with Image.open(saved) as im:
                line += f"  {im.width}x{im.height}"
                if report.width:
                    ok = (im.width, im.height) == (report.width, report.height)
                    line += "  MATCHES requested size" if ok else \
                            f"  !! expected {report.width}x{report.height}"
        print(line)

    print(f"elapsed   : {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
