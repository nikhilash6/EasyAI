"""Manual check of format detection and manifest autodetect against real files.

Run:  python tests/smoke_workflows.py "E:\\ComfyUI Workflow"

Reads only - never writes a manifest next to the user's own workflow files.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.workflows.loader import Format, classify, extract_graph
from app.workflows.manifest import MANIFEST_SUFFIX, autodetect


def main() -> int:
    roots = sys.argv[1:] or [r"E:\ComfyUI Workflow"]
    counts: Counter[str] = Counter()
    convertible: list[tuple[Path, dict]] = []

    for root in roots:
        folder = Path(root)
        if not folder.is_dir():
            print(f"!! not a folder: {folder}")
            continue
        files = [p for p in folder.rglob("*.json") if not p.name.endswith(MANIFEST_SUFFIX)]
        print(f"\n=== {folder}  ({len(files)} json files)")
        for path in files:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception:
                counts["unreadable"] += 1
                continue
            fmt = classify(raw)
            counts[fmt.value] += 1
            if fmt is Format.UI_WITH_API:
                convertible.append((path, extract_graph(raw, fmt)))
            elif fmt is Format.API:
                convertible.append((path, raw))

    print("\n--- format tally ---")
    for key, n in counts.most_common():
        print(f"  {key:<12} {n}")

    print(f"\n--- autodetect on {len(convertible)} usable graphs ---")
    for path, graph in convertible[:12]:
        m = autodetect(graph, name=path.stem)
        found = {k: f"{v.node}.{v.input}" for k, v in m.bindings.items() if v}
        print(f"\n{path.name}")
        print(f"   engine   : {m.engine}")
        print(f"   nodes    : {len(graph)}")
        print(f"   bindings : {found}")
        if m.ambiguous:
            print(f"   ambiguous: {m.ambiguous}")

    # Coverage: how often does detection find the things that matter?
    if convertible:
        hit = Counter()
        for _, graph in convertible:
            m = autodetect(graph)
            for key in ("prompt", "seed", "width", "output", "image_in", "ratio"):
                if m.has(key):
                    hit[key] += 1
        print(f"\n--- detection coverage over {len(convertible)} graphs ---")
        for key, n in hit.most_common():
            print(f"  {key:<10} {n}/{len(convertible)}  ({n / len(convertible):.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
