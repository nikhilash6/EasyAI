# EasyAI — Manual Outline

## Overview

EasyAI is a Windows desktop front end for ComfyUI (Stable-Diffusion-style node
graphs), built for people who want to generate images, video and prompts
without learning the ComfyUI node editor. It is a PySide6 (Qt) application.

Three user-facing tabs — **Image**, **Video**, **Prompt Helper** — each built
from the same "pick a style, type what you want, choose a shape, press
Create" screen. EasyAI reads exported ComfyUI API workflow files, writes a
sidecar "manifest" file next to each one describing which nodes to control,
and talks to a running (or self-started) ComfyUI server over HTTP + a
websocket to queue jobs, show live previews, and save results.

Entry point: `EasyAI.py` (`python EasyAI.py`, or double-click `EasyAI.lnk` /
`EasyAI.bat`). Version string: `1.0.0` (`app/__init__.py`).

Tech stack: Python 3.10+, PySide6 (Qt) for the GUI, `requests` +
`websocket-client` for talking to ComfyUI, `Pillow` for image handling. No
separate packaging/build step is required to run from source; `Build EXE.bat`
exists in the repo for producing a PyInstaller build (not explored in depth
here — see Needs Confirmation).

There are **two other launchable programs** in this same repository, sharing
the `app/` package and theme:

- **EasyAI Setup** (`EasyAISetup.py` / `EasyAISetup.bat`) — a separate,
  single-screen installer that downloads and installs the specific ComfyUI
  version, add-ons and models EasyAI's shipped workflows expect. It never
  reads or writes EasyAI's own `settings.json`. It is documented here as a
  companion screen (see **Screens → EasyAI Setup**) because a first-time user
  with no ComfyUI installed would plausibly run it before EasyAI itself.
- **EasyAI Studio** (`studio/` — no top-level launcher script was found in the
  files read) — described in its own source comment as "Publishing tools" /
  "a workbench you come back to for one task at a time": a catalogue/language/
  links editor. This looks like a maintainer/author tool, not something an
  end user (the README's "viewer") would ever open. See **Needs Confirmation**
  for whether it belongs in the end-user manual at all.

The core, primary subject of this manual should be **EasyAI** itself
(`EasyAI.py` + `app/`), matching the scope of `README.md`.

## Requirements & Installation

- **Python**: 3.10 or newer (checked explicitly by `EasyAI.bat` and refused
  with an on-screen message otherwise).
- **OS**: Windows-first. `EasyAI.bat` / `EasyAI.lnk` are Windows launchers;
  non-Windows code paths exist (`sys.platform == "darwin"` / else branches in
  `app/ui/widgets.py`) but are not the documented install path.
- **Python packages** (`requirements.txt`):
  - `PySide6>=6.6`
  - `requests>=2.31`
  - `websocket-client>=1.7`
  - `Pillow>=10.0`
  - `py7zr>=1.0` (EasyAISetup only — unpacks the ComfyUI portable `.7z` when
    7-Zip is not already installed)
- **External dependency**: a working **ComfyUI** install (the "AI engine").
  EasyAI does not bundle ComfyUI itself — either point Settings at an
  existing install, or run **EasyAI Setup** to download one.
- **GPU/CPU**: not asserted anywhere in the EasyAI codebase read (no explicit
  CUDA/VRAM check beyond reading `/system_stats` for the VRAM display). VRAM
  is clearly assumed to matter — see the video-length and megapixel warning
  thresholds — but no minimum spec is stated in code or README. Flag under
  Needs Confirmation.
- **Install steps** (from README/`EasyAI.bat`):
  1. Install Python 3.10+, ticking "Add python.exe to PATH".
  2. Double-click `EasyAI.lnk` (shortcut to `EasyAI.bat`) or run
     `python EasyAI.py` from a terminal.
  3. First launch: `EasyAI.bat` detects missing libraries
     (`PySide6, requests, websocket, PIL`) and runs
     `pip install -r requirements.txt` once, showing a console window only
     for that step; afterwards it always launches windowless (`pyw`/`pythonw`).
  4. On the very first run of the app itself (`first_run_done` not yet set in
     settings), EasyAI shows a welcome message box and opens **Settings**
     automatically so the user can confirm/correct the ComfyUI folder before
     doing anything else.
- **Tests** (developer-facing, not part of the end-user flow):
  `python -m pytest tests/ -q` (552 unit tests, no ComfyUI needed); separate
  smoke/E2E scripts require a running ComfyUI and are run by hand.

## Screens

Order below follows what a first-time user encounters: launch → first-run
welcome → Settings → startup splash → main window/tabs → per-tab generate
screen → workflow setup/rename → menu-driven dialogs. EasyAI Setup is listed
last as an adjacent, separately-launched program.

### First-run Welcome (message box)

- Purpose: One-time notice, before any generation is possible, telling the
  user EasyAI has guessed a ComfyUI folder and is about to open Settings so
  they can confirm it.
- Controls: Single **OK** button (standard `QMessageBox.information`).
- Notes: Shown only when `settings.json` has no `first_run_done` key /
  it is `False`. EasyAI guesses the ComfyUI directory from a short list of
  common paths (`app/comfy/launcher.py: default_comfyui_dir()`):
  `C:\AI ComfyUI - New Version\ComfyUI_windows_portable`,
  `C:\AI ComfyUI Sage\ComfyUI-Easy-Install`, `C:\ComfyUI_windows_portable`,
  `~\ComfyUI_windows_portable` — first one that exists on disk, else the
  first candidate as a fallback guess. Immediately followed by opening the
  Settings dialog; `first_run_done` is set `True` and saved regardless of
  whether the user changes anything in Settings.

### Settings

- Purpose: Configure where ComfyUI lives and how it's started, where
  workflows/results are stored, generation defaults, and the app language.
  Self-tests the ComfyUI connection live.
- Reached via: `File → Settings…` (`Ctrl+,`), or automatically on first run.
- Controls (grouped in three boxes):
  - **The AI engine (ComfyUI)**
    - "ComfyUI folder" — read-only text field + **Browse…** (folder picker).
      Default: `C:\AI ComfyUI - New Version\ComfyUI_windows_portable`.
    - "Start file" — editable combo box, auto-populated with every `.bat`
      found in the chosen folder, ranked so `*nvidia*`/`start comfyui*` sort
      first and `*cpu*`/`*update*`/`*install*` sort last/are treated as not a
      run script. Default: `run_nvidia_gpu.bat`.
    - "Address" — text field for `host:port` of the ComfyUI server. Default:
      `127.0.0.1:8188`.
    - "Start ComfyUI automatically when EasyAI opens" — checkbox. Default: on.
    - "Close ComfyUI when EasyAI closes" — checkbox, tooltip explains it only
      ever closes a ComfyUI **EasyAI itself started**, never one the user
      already had running. Default: on.
    - Live status line + **Test now** button — re-probes the folder/launcher
      and pings the server as soon as the folder, start file, or address
      changes; shows in green ("ComfyUI is answering at …") or amber/warning
      text otherwise.
  - **Folders**
    - "Workflows" — path + Browse. Default: `<app root>/workflows`.
    - "Results" — path + Browse. Default: `<app root>/output`.
  - **Creating**
    - "Shape to start with" — dropdown of ratio strings (`1:1 4:3 3:4 3:2
      2:3 16:9 9:16 21:9 9:21`). Default: `2:3`.
    - "Give up after" — spin box, 60–14400 seconds, step 60. Default: 1800
      (job timeout).
    - "Wait for the engine up to" — spin box, 30–1800 seconds, step 30.
      Default: 300 (launch timeout).
    - "Language" — dropdown of installed language catalogues (see
      Settings Reference / Language below).
  - **Save** / **Cancel** buttons (standard dialog box).
- Notes: Saving applies the new language immediately (no restart) by
  retranslating the whole main window. If the ComfyUI folder, start file,
  server address, or workflow folder changed, the main window's client cache
  is cleared, a new launcher is built (adopting any ComfyUI process the old
  launcher had started, so it can still be stopped later), folders are
  re-created, and the engine is re-checked. Closing Settings also always
  reloads all three tabs' workflow lists.

### Startup Splash ("Starting the AI engine…")

- Purpose: Modal progress dialog shown while EasyAI looks for/starts ComfyUI,
  so a beginner never sees a frozen window.
- Controls: Indeterminate progress bar, status text line, and a
  **Carry on without it** button.
- Notes: Skipped entirely if ComfyUI is already answering when EasyAI starts.
  Otherwise a background worker polls `/system_stats`, and if
  "Start ComfyUI automatically" is on, launches the configured `.bat` with no
  console window and polls until it responds or the launch timeout is hit.
  "Carry on without it" cancels the wait and opens the main window anyway
  (nothing can be generated until the engine connects). If the engine still
  fails to start, a warning dialog explains the app is still usable for
  browsing but not for creating.

### Main Window

- Purpose: The application shell — three generation tabs, an engine status
  strip, and a status bar.
- Controls:
  - Menu bar: **File**, **AI engine**, **Help** (see below).
  - Engine status strip (above the tabs): a text "pill" reading
    `engine live · 127.0.0.1:8188` or `engine down · …`, and a workflow
    count readout (e.g. "6 workflows loaded").
  - Tab bar: 🖼 **Image**, 🎬 **Video**, ✨ **Prompt Helper** (icons + labels).
  - Status bar (bottom): VRAM readout (`VRAM x.x / y GB`, only while the
    engine is alive and a device is reported), a coloured dot + "AI engine:
    running/not running" text, and a transient status message area.
- Keyboard shortcuts:
  - `Ctrl+Enter` / `Ctrl+Return` — run the Create button of whichever tab is
    in front (only if it is enabled/visible).
  - `Ctrl+,` — Settings.
  - `F5` — "AI engine → Check it is running".
  - `Ctrl+R` — "AI engine → Reload workflows".
- Menu contents:
  - **File**: Open the workflows folder · Open the results folder ·
    Settings… (`Ctrl+,`) · Quit (`Ctrl+Q`/platform Quit shortcut).
  - **AI engine**: Check it is running (`F5`) · Start it now · Reload
    workflows (`Ctrl+R`).
  - **Help**: How do I add a workflow? · About EasyAI.
- Notes: Window geometry is remembered between sessions
  (`window_geometry` setting). On close, running jobs are cancelled, the
  engine-start worker is stopped, and — if "Close ComfyUI when EasyAI
  closes" is on — any ComfyUI process EasyAI itself started is force-killed
  (`taskkill /F /T`) via a Windows Job Object safety net that also kills it
  if EasyAI crashes or is killed from Task Manager.

### Image tab / Video tab / Prompt Helper tab (the "Generate" screen)

These three tabs share one implementation (`GenerateTab` in
`app/ui/tab_base.py`); each subclass only tweaks labels/visibility. Documented
once, with per-tab differences called out.

- Purpose: Pick a workflow ("style"), describe what to make, adjust
  size/length/count, and generate a result.
- Layout: three columns — workflow list (left) · prompt + inputs + options +
  Create (middle) · live preview + results gallery (right; Prompt Helper
  replaces the picture preview with a text result box + history list).
- Controls (left column — "Choose a style"):
  - Scrollable list of workflow cards, each showing a coloured status dot
    (green/amber/red) and a short status line (`READY · <ENGINE>`,
    `CHECK SET UP`, `MISSING ADD-ON`, `MISSING MODEL`, `NOT CHECKED`,
    `NEEDS RE-EXPORT`).
  - **Refresh** button — rescans the workflow folder and re-runs the
    pre-flight check.
  - **Set up…** button — opens the Manifest Editor for the selected workflow.
  - Right-click context menu on a workflow: **Rename…** (`F2`), **Set up…**,
    **Show the file** (opens Explorer with the file selected).
  - Double-click a workflow → same as Rename.
  - Empty-state message (when no workflows exist yet) telling the user which
    folder to drop exported API `.json` files into, then press Refresh.
- Controls (middle column):
  - Prompt label + live character counter, and a multi-line prompt text box
    (hint text differs per mode — Image: "Describe the picture you want to
    create…", Video: "Describe the video you want to create…", Prompt
    Helper: "Type a rough idea, like: a fox in the snow"). Disabled with an
    explanatory placeholder if the workflow takes no prompt at all.
  - Drop zones (0–14, dynamic): one labelled click-or-drag file box per input
    the selected workflow's manifest exposes — up to 10 picture slots plus
    2 audio and 2 video slots. Each shows a thumbnail/icon, filename, and a
    **Remove** button once a file is chosen; label text and "(optional)"
    suffix come from the manifest. A hint line "Attach only the ones you
    want — leave the rest empty." appears only when some slots are optional.
  - **Shape** picker (`RatioPicker`) — row of ratio chips (`1:1 4:3 3:4 3:2
    2:3 16:9 9:16 21:9 9:21`, filtered to whatever the workflow's own
    size-chooser node supports, if any), with the real pixel size shown
    below in monospace and as each chip's tooltip. Hidden on the Prompt
    Helper tab. Greyed out (with a reason tooltip) when the workflow takes
    its size from the supplied picture instead.
  - **Detail** picker (`MegapixelPicker`) — slider, 0.5–2.0 megapixels
    (workflow's own built-in value can extend the floor below 0.5), shown
    only for workflows that expose a `megapixels` binding or plain
    width/height the app can recompute. Live value label (e.g. "1.0 MP")
    and a hint/warning line ("1.0 is the normal size" / "smaller and
    quicker" / "larger, more detail" / an amber over-threshold warning).
  - **Length** picker (`DurationPicker`, Video only) — spin box in seconds
    (2–15 by default, configurable), showing the computed frame count
    underneath and an amber caution above the warn threshold (default 8s)
    about graphics-memory exhaustion.
  - **How many** spin box (batch count), 1–16, shown only when the workflow's
    manifest exposes a batch/count binding.
  - **"Let the AI improve my wording first"** switch — shown only when the
    workflow has a detected prompt-enhancer node (and never on Prompt
    Helper, where the whole workflow is the enhancer). Defaults on,
    remembered between runs (`use_prompt_enhancer`).
  - **"Repeat the same result, so I can compare changes"** switch (seed
    lock) — shown only when the workflow exposes a seed binding. When
    checked, reuses a seed remembered per-tab instead of randomising each
    run.
  - **Create <Mode>** button (primary/accent styled) — "Create Image",
    "Create Video", or, on Prompt Helper, retitled **"Improve my prompt"**.
    Video tab's button carries a tooltip warning that video takes much
    longer.
  - **Stop** button (danger styled) — replaces Create while a job is
    running; cancels the current job via ComfyUI's interrupt endpoint.
  - "CTRL + ENTER to run" hint label.
  - Progress bar (0–100%) and a status/hint line under it, shown while a job
    runs and updated with live progress messages; after completion shows
    elapsed time, output size, file count, seed, and any notes (e.g. a
    shape the workflow could not honour) in amber.
- Controls (right column):
  - **Image/Video tabs**: live preview pane (shows ComfyUI's in-progress
    preview frames, then the finished picture; for non-image results shows
    a "Saved <name> — double-click to play it" message) above a **Results**
    gallery (thumbnail list of everything generated this session, newest
    first, with an **Open folder** button; double-click opens the file in
    the OS default viewer/player).
  - **Prompt Helper tab** (replaces preview+gallery): "The improved prompt"
    heading, a read-only result text box, **Copy** button, a "Send to
    <Image/Video>" dropdown + **Go** button (drops the text into the chosen
    tab's prompt box and switches to it), and a history list of every past
    result (`output/prompt-enhancer/*.txt`) that reloads a past result when
    clicked.
- Notes / first-run & default-state behaviour:
  - Selecting a workflow whose manifest is "ambiguous" (auto-detected with
    low confidence) shows "EasyAI had to guess how this workflow works.
    Press 'Set up…' to check it." in the status line.
  - A workflow missing a required custom node or model file is greyed out
    and unselectable for Create; its status text names the exact add-on
    (mapped from a lookup table) or model filename to install.
  - Pressing Create with the engine not running shows a warning dialog
    directing the user to Settings → Start engine (or to start ComfyUI
    themselves).
  - Pressing Create with a required file input empty shows an "Almost
    there" info dialog listing what's missing rather than sending a broken
    job to ComfyUI.
  - Seeds are randomised on every run unless the "repeat the same result"
    switch is on; seeds are remembered separately per tab.
  - LTX 2.5-style prompt-enhancer nodes, when switched off, are actually
    removed from the graph copy sent to ComfyUI (rewired around, then
    deleted) so the language model never loads — not just muted.

### Set up… (Manifest Editor)

- Purpose: Correct EasyAI's automatic guesses about which ComfyUI nodes/
  inputs a workflow's prompt, shape, seed, length, batch, enhancer, output
  name, and file slots map to, without hand-editing JSON.
- Reached via: **Set up…** button on a tab, or right-click → **Set up…** on a
  workflow, or automatically suggested when a workflow is flagged
  "CHECK SET UP".
- Controls:
  - Explanatory blurb, plus (if applicable) an amber warning line naming
    which bindings were guessed ("EasyAI had to guess these: …").
  - "This workflow" group: **Name shown in the list** text field, and
    **Model family** dropdown ("Work it out automatically" or one of the
    named engines from `app/ratios.py`) — decides which pixel-size table is
    used per shape.
  - One row per binding key (Prompt, Things to avoid, Width, Height, Shape
    chooser, Total pixels, Seed, Prompt improver, Length, How many, Output
    name, plus one row per detected/possible file slot, labelled
    "Picture N" / "Sound N" / "Video N" with an editable display-name field
    for file slots). Each row is a **node dropdown** (every node id + class
    type + title in the graph) followed by an **input dropdown** (only the
    real input names of the chosen node), plus a **"+ also set another
    node"** link to add more target nodes for the same key (needed e.g.
    when width must be written to two different nodes at once).
  - Dialog buttons: **Save**, **Cancel**, **Reset** (relabelled "Guess
    again" — re-runs autodetection and discards manual edits in the open
    dialog, without saving until Save is pressed).
- Notes: Saving marks the manifest as no longer a guess (`autodetected =
  False`, `ambiguous` cleared) and writes it to a `.manifest.json` file next
  to the workflow. The manifest itself is written the first time a workflow
  is scanned and is **never auto-regenerated** afterward, so manual
  corrections persist across re-exports of the underlying workflow file.

### Rename dialog

- Purpose: Give a workflow a friendly display name.
- Reached via: `F2` on the selected workflow, double-click a workflow, or
  right-click → **Rename…**.
- Controls: Standard text-input dialog (`QInputDialog.getText`) pre-filled
  with the current name, OK/Cancel.
- Notes: The name is stored in the workflow's manifest, not in the filename
  — re-exporting the same workflow from ComfyUI keeps the custom name;
  clearing the name back to blank falls back to the filename.

### Help → How do I add a workflow?

- Purpose: In-app quick reference for the workflow-authoring steps described
  in the README (Export (API), save into the right folder, press Refresh).
- Controls: Info message box, OK to dismiss.
- Notes: Names the current workflow folder path live from settings.

### Help → About EasyAI

- Purpose: Version/credits and a reminder of where workflows and results are
  stored.
- Controls: Standard About dialog, OK to dismiss.
- Notes: Shows `app.__version__` ("1.0.0") and the current workflow/output
  folder paths.

### EasyAI Setup (separate program: `EasyAISetup.py` / `EasyAISetup.bat`)

- Purpose: One-screen installer that downloads a pinned ComfyUI version plus
  the specific custom-node add-ons and model files EasyAI's bundled
  workflows need, into a folder of the user's choosing.
- Controls (single page, top to bottom):
  - Title + **language** dropdown (top-right, so a user in the wrong
    language can still find it).
  - Explanatory blurb naming the ComfyUI version being installed.
  - "WHERE TO PUT IT" — folder path field (default `~/EasyAI-ComfyUI`) +
    **Browse…**, and a free-disk-space readout.
  - "WHAT TO INCLUDE" — one **group card** per install group (a toggle
    switch styled like the main app's Switch control), each showing model
    count / add-on count / "to copy in by hand" count and its own download
    size; a running total size label; amber warning lines for insufficient
    disk space, models with no automatic download link, and models needing
    an account/licence; a green "no account needed" note when applicable.
  - **HuggingFace key** and **Civitai key** password-style fields (used only
    for the current run, never saved to disk), needed only for models
    behind a login.
  - **Install** button (primary), **Stop** button (shown mid-install),
    progress bar, current-file/speed label, and a scrolling log text box.
- Notes: Clicking Install shows a confirmation dialog stating the exact
  download size and destination before starting. Downloads are resumable —
  stopping and re-running "picks up where it left off." This program never
  reads or writes EasyAI's own `settings.json`; it is entirely separate
  state. Whether/how this screen should be included in the EasyAI user
  manual is flagged under Needs Confirmation.

## Settings Reference

All keys live in `settings.json` at the app root (or next to the built
`.exe`), managed by `app/config.py`. Defaults shown; unknown keys are
preserved and missing keys fall back silently, so upgrades never wipe an
existing file.

| Key | Default | Controls |
|---|---|---|
| `comfyui_dir` | `C:\AI ComfyUI - New Version\ComfyUI_windows_portable` | Folder where ComfyUI is installed. |
| `comfyui_launcher` | `run_nvidia_gpu.bat` | Which `.bat` inside that folder starts ComfyUI. |
| `comfyui_server` | `127.0.0.1:8188` | Address EasyAI talks to ComfyUI on. |
| `auto_launch` | `true` | Whether EasyAI starts ComfyUI itself when nothing answers. |
| `launch_timeout` | `300` (seconds) | How long to wait for ComfyUI to come up before giving up. |
| `job_timeout` | `1800` (seconds) | How long a single generation may run before EasyAI gives up on it. |
| `stop_engine_on_exit` | `true` | If EasyAI started ComfyUI, close it again when EasyAI exits. |
| `workflow_dir` | `<root>/workflows` | Folder holding the per-mode workflow subfolders. |
| `output_dir` | `<root>/output` | Folder results are saved into (per-mode subfolders). |
| `default_ratio` | `2:3` | Shape chip pre-selected for a freshly picked workflow. |
| `lock_seed` | `false` | Whether "repeat the same result" starts ticked. |
| `locked_seeds` | `{}` | Last/locked seed per mode key, so locking one tab doesn't reuse another tab's seed. |
| `batch_count` | `1` | Default value of the "How many" spin box. |
| `use_prompt_enhancer` | `true` | Default state of "Let the AI improve my wording first". |
| `image_megapixels` | `1.0` | Remembered Detail value for the Image tab, used when a workflow states no budget of its own. |
| `video_megapixels` | `0.5` | Same, for Video. |
| `image_megapixels_warn` | `1.5` | Detail value above which the Image tab shows a memory-cost warning. |
| `video_megapixels_warn` | `1.0` | Same, for Video (lower, because video pays the cost per frame). |
| `video_length_default` | `5` (seconds) | Length box's starting value. |
| `video_length_min` / `video_length_max` | `2` / `15` | Range of the Length spin box. |
| `video_length_warn` | `8` (seconds) | Above this, the Length control shows a memory-exhaustion caution. |
| `language` | `en` | Not read directly by the dialog (language is tracked by `app/i18n.py`'s own remembered choice) but present as a default key. |
| `theme` | `dark` | Present as a default key; no theme-selection control was found in Settings — see Needs Confirmation. |
| `window_geometry` | `""` | Serialized (hex) Qt window geometry, restored on next launch. |
| `first_run_done` | `false` | Whether the first-run welcome/Settings flow has already been shown. |

Per-workflow **manifest** files (`<workflow>.manifest.json`, one per
workflow, auto-generated once and never overwritten by re-scans) separately
store: display name, model family/engine override, and node/input bindings
for prompt, negative, width, height, ratio, megapixels, seed, enhancer,
length, batch, output, and each file slot — plus custom display labels for
file slots and an `ambiguous`/`autodetected` flag pair used to drive the
"CHECK SET UP" status and the manifest editor's warning banner.

## Known Quirks / Troubleshooting Signals

- **Workflow status dots/labels** in the left-hand list are the primary
  troubleshooting surface: `READY · <ENGINE>` (green), `CHECK SET UP` /
  `NOT CHECKED` (amber — manifest guessed or engine not yet queried),
  `MISSING ADD-ON` / `MISSING MODEL` (red — a required custom node or model
  file is absent from the connected ComfyUI), `NEEDS RE-EXPORT` (red — the
  file is not a valid ComfyUI API export).
- **Engine pill / status bar dot** (`engine live` vs `engine down`, plus a
  red/green dot and "AI engine: running/not running" text) is the first
  thing to check per the README's own framing.
- **VRAM readout** in the status bar (`VRAM used / total GB`) only appears
  once the engine is alive and reports a device; it's meant to explain
  out-of-memory failures.
- **"ComfyUI closed straight away."** — shown if the launched `.bat`
  process exits immediately (bad install), with a suggestion to run it by
  hand to see the real error.
- **"The AI engine did not start within N seconds."** — launch-timeout
  failure, with the same by-hand suggestion.
- **"EasyAI cannot reach ComfyUI"** warning when Create is pressed with no
  live connection.
- **"Almost there"** dialog when required files are missing at Create time.
- **"The workflow finished but produced no text."** (Prompt Helper) — the
  workflow needs a Preview Any / Show Text node on its output; otherwise
  there's nothing to read back.
- **"The AI engine finished but produced no files."** — no Save node, or
  the run was stopped.
- A **plain "Save"** in ComfyUI's editor is explicitly *not* sufficient —
  only **Workflow → Export (API)** produces a file EasyAI/`/prompt` accepts.
  A workflow's embedded "last run" prompt is deliberately *not* trusted
  unless every loader/save node in it matches the visible graph, because in
  practice the embedded copy is very often stale (README: "Of the 200-odd
  workflows on this machine, not one embedded copy was current").
  A plain non-API save produces a "NEEDS RE-EXPORT" status rather than a
  silent failure.
  - **Needs Confirmation**: the exact user-facing error text for this case
    was not located in the files read (likely in `app/workflows/loader.py`,
    not opened in full during this pass).
- **Optional file inputs**: a file slot is only treated as skippable when
  the connected ComfyUI's node metadata marks *every* input a loader feeds
  as optional; with no ComfyUI to ask, nothing is treated as optional (safer
  default). Leaving all files empty on such a workflow is still refused with
  a message, even though the underlying node technically allows it.
- **Detail (megapixels) floor**: a workflow authored below the normal
  0.5–2.0 range keeps its own lower floor rather than being silently
  rounded up, to avoid changing a result the workflow's author tuned or
  exhausting VRAM.
- **Output size vs. requested size**: some engines (documented for LTX)
  floor both dimensions to a multiple of 64 after the chosen size is
  computed, so the actual file can differ slightly from the shape chip's
  label; EasyAI's `app/ratios.py` accounts for this per engine
  (`output_grid`).
- **Prompt-enhancer toggle** is real graph surgery (rewire + delete the
  enhancer node), not a flag — turning it off changes what gets sent to
  ComfyUI, not just what happens client-side.
- **First run guess** of the ComfyUI folder may be wrong; the welcome
  dialog exists specifically so the user checks/corrects it via "Test now"
  before relying on it.
- **`stop_engine_on_exit`** only ever closes a ComfyUI process EasyAI itself
  launched; a ComfyUI the user started manually, or one running on another
  machine, is never touched.

## Needs Confirmation

- Whether **EasyAI Setup** (`EasyAISetup.py`) should be included as an
  in-scope screen for this manual, since the main README does not mention it
  at all, even though it is clearly a real, user-facing installer that a
  first-time user without ComfyUI would need.
- Whether **EasyAI Studio** (`studio/`) is intended for end users at all —
  its own source comments describe it as "Publishing tools" for the
  maintainer, but no launcher script for it was located during this pass to
  confirm it's fully out of scope; worth a maintainer check before excluding
  it outright.
- **Language catalogues**: `app/i18n.py` lists `en`, `zh-Hant`, `zh-Hans` as
  known language codes, with catalogue files present for the latter two
  (`lang/zh-Hans.json`, `lang/zh-Hant.json`). The Settings dialog's Language
  dropdown was confirmed; whether any other in-app entry point (e.g. a
  first-run language prompt) exists was not verified.
- **Theme**: `settings.json` has a `theme` default of `"dark"` and
  `app/ui/theme.py` exists, but no dark/light toggle control was found in
  the Settings dialog read in full — worth confirming whether theme
  switching is genuinely user-facing anywhere, or purely internal/fixed.
- **GPU/CPU minimum requirements**: no explicit minimum VRAM, GPU vendor, or
  "CPU-only mode" statement was found in README or the settings/launcher
  code beyond the presence of a `run_nvidia_gpu.bat`-style default launcher
  name and VRAM-based warnings in the UI. A manual should confirm actual
  minimum hardware with the developer rather than inferring it.
- **Packaging/build**: `Build EXE.bat` and `app/paths.py`'s handling of a
  PyInstaller "frozen" build were noted but not explored — worth confirming
  whether an end user is ever expected to run a built `.exe` rather than
  `EasyAI.py` from source, since that would change the Requirements &
  Installation section.
- **Exact wording of the "not a valid API export" / re-export-needed error
  message** shown to the user — referenced by the `NEEDS RE-EXPORT` status
  label in `app/ui/widgets.py`, but the message text itself lives in
  `app/workflows/loader.py`, which was not read in full during this pass.
- **Screenshot targets**: this outline was produced from source/README
  reading only; no screenshots have been captured yet (that is the next
  pipeline stage).
