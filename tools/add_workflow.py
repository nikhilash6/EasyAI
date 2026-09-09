"""Add an API workflow to EasyAI so Setup can install its models for viewers.

    python tools/add_workflow.py "Flux 3 t2i.json" --group image
    python tools/add_workflow.py "Flux 3 t2i.json" --group image --apply

Without --apply it only reports what would happen. ComfyUI must be running:
the folder each model belongs in, and which classes are add-ons rather than
core, can only be answered by the live server.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from app.comfy import objectinfo                       # noqa: E402
from app.comfy.client import ComfyClient               # noqa: E402
from app.config import Config                          # noqa: E402
from app.modes import MODE_ORDER                       # noqa: E402
from setup import authoring                            # noqa: E402


def human(n: int) -> str:
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} B"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", help="the exported API workflow .json")
    parser.add_argument("--group", required=True, choices=list(MODE_ORDER))
    parser.add_argument("--apply", action="store_true",
                        help="actually write it in (default is a dry run)")
    parser.add_argument("--models", default=r"C:\Models",
                        help="where the model files live on this machine")
    args = parser.parse_args()

    cfg = Config()
    client = ComfyClient(cfg.server)
    caps = objectinfo.fetch(client, refresh=True)
    if not caps.available:
        print("ComfyUI must be running - the folder each model belongs in and "
              "which classes are add-ons can only be answered by the server.")
        print(f"  {caps.error}")
        return 1

    # The generator owns the ComfyUI install scan; borrow its two passes.
    sys.argv = [sys.argv[0]]
    import make_catalog

    try:
        result = authoring.analyse(
            Path(args.workflow), args.group, client, caps,
            models_root=Path(args.models),
            harvested=make_catalog.harvest_urls(),
            owner_of=make_catalog.owner_of,
            core_classes=make_catalog.core_classes)
    except authoring.AuthoringError as e:
        print(f"\n{e}")
        return 1

    print(f"\n{result.name}  [{args.group}]  {result.node_count} nodes")
    print(f"  EasyAI can drive : {', '.join(result.manifest_slots) or 'nothing'}")

    print(f"\n  models ({len(result.models)}, {len(result.new_models)} new)")
    for m in result.models:
        mark = "new " if not m.known else "    "
        folder = f"models/{m.folder}" + ("  (guessed)" if m.folder_guessed else "")
        link = m.url.split("/")[2] if m.url else "NO LINK"
        print(f"    {mark}{m.filename[:44]:<46} {folder:<28} "
              f"{human(m.bytes):>9}  {link}")

    if result.nodes:
        print(f"\n  add-ons ({len(result.nodes)})")
        for n in result.nodes:
            print(f"    {'    ' if n.known else 'new '}{n.name:<34} "
                  f"{n.source or 'UNRESOLVED'}  ({len(n.classes)} classes)")

    for note in result.warnings:
        print(f"\n  ! {note}")
    for problem in result.problems:
        print(f"\n  STOPS THE ADD: {problem}")

    if result.needs_link:
        print(f"\n  {len(result.needs_link)} model(s) have no download link. Add "
              f"them to setup/url_overrides.json, then run this again:")
        for m in result.needs_link:
            print(f"    \"{m.filename}\": \"\",")

    if not args.apply:
        print("\n(dry run - pass --apply to write it in)")
        return 0 if result.can_add else 1

    try:
        for line in authoring.apply(result):
            print(f"  {line}")
    except authoring.AuthoringError as e:
        print(f"\n{e}")
        return 1
    for note in authoring.stage_for_mirror(result):
        print(f"\n  {note}")
    print("\nDone. Now run:  python tools/verify_urls.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
