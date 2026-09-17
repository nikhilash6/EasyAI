# EasyAI

A simple desktop front end for ComfyUI, for people who just want to make
something without learning the node graph.

Three tabs — **Image**, **Video** and **Prompt Helper**. Pick a style, type what
you want, choose a shape, press Create. **Ctrl + Enter** runs the tab you are on.

Software Demo Video:
https://www.youtube.com/watch?v=lB-Q8yuMzv8

## Icon

`EasyAI-Icon.png` is the master artwork. `tools/make_icons.py` turns it into the
pack in `assets/icons/`:

```bash
python tools/make_icons.py
```

It writes a nine-resolution `EasyAI.ico` (16/20/24/32/40/48/64/128/256) plus
loose PNGs. Two things it does beyond resizing:

- **Trims the transparent margin.** The master has about 10% padding on each
  side; Windows adds its own, so shipping it just makes the artwork smaller in
  the taskbar. Trimming reclaims a fifth of every icon.
- **Sharpens below 48 pixels**, where a plain downscale goes soft and the
  star's face turns to mush.

Re-run it whenever the master changes.

## Look

Near-black surfaces with a single warm orange accent, following the EasyAI
redesign. Two typefaces: one for words, one reserved for numbers you might read
back — pixel sizes, megapixels, the engine address — so figures line up and do
not jitter as they change.

The design asks for **Manrope** and **JetBrains Mono**. Neither ships with
Windows, so EasyAI uses Segoe UI and Cascadia Mono unless it finds them: drop
the `.ttf` files into `app/ui/fonts/` and they are picked up at startup. Both
are open-licensed and can be shipped with the app. The layout is unaffected
either way.

Shape is a row of chips rather than a dropdown, so every size a workflow can
make is visible at once. Detail is a slider. The engine's address and the card's
VRAM are on screen at all times, because they are the first things to check when
a run fails.

**Double-click `EasyAI.lnk`.** That is the whole install story for a viewer —
it is a shortcut to `EasyAI.bat` carrying the app icon, since a `.bat` cannot
have one of its own.

It finds Python, installs the libraries the first time (once, a couple of
minutes), then opens the app with no console window left behind. If Python is
missing or too old it says so and links the download, rather than flashing a
black box and vanishing. Startup errors appear in a message box, since a
windowless launch has nowhere to print them.

From a terminal, `python EasyAI.py` does the same thing.

## What it does about the "my nodes broke again" problem

Before a workflow can be selected, EasyAI compares it against the ComfyUI that
is actually running and says what is wrong in plain words:

| | |
|---|---|
| ✅ | Ready |
| ⚠ | EasyAI guessed how this workflow works — worth checking |
| ⛔ | Missing add-on: `ComfyUI-GGUF`, or a model file that isn't downloaded |

A blocked workflow is greyed out with the reason and instructions, instead of
failing halfway through with a stack trace.

It also starts ComfyUI for you (no console window), randomises the seed on every
run, and keeps output at a size the model was trained for.

## Adding a workflow

1. Open the workflow in ComfyUI and check it runs.
2. **Workflow → Export (API)** — a plain Save produces a file `/prompt` rejects.
3. Save it into the folder for its type:

```
workflows/image/    workflows/video/    workflows/prompt-enhancer/
```

4. Press **Refresh** in EasyAI.

Optional: put a picture beside it with the same name (`flux.json` → `flux.png`)
and it becomes the thumbnail.

## Naming a workflow

`video_ltx2_5_flf2v - GarionHK` means nothing to a beginner. Rename it to
**First and Last Picture** — select it and press **F2**, double-click it, or
right-click → **Rename…**

The name is stored in the manifest, not the filename, so:

- the exported workflow file is never touched
- re-exporting from ComfyUI keeps the name, even though node numbers change
- clearing the name falls back to the filename

Right-click also offers **Set up…** and **Show the file**.

If you drop in a plain editor file by mistake, EasyAI says so rather than
failing.

**Why an editor file is not enough.** ComfyUI does keep a copy of the last
prompt it ran inside the file, and EasyAI will use that copy when it genuinely
belongs to the workflow. In practice it usually doesn't: the copy survives
"save as", so a first-and-last-frame workflow can be carrying the graph of the
plain text-to-video one it was built from. Running that would quietly make the
wrong thing, so the embedded copy is checked against the workflow you can see —
every loader and save node has to line up — and rejected if it doesn't match.
Of the 200-odd workflows on this machine, not one embedded copy was current.
Export the API file; it is the only reliable route.

## How EasyAI knows what to change

An API workflow is just a bag of nodes — nothing marks "this is the prompt". So
each workflow gets a sidecar next to it:

```
workflows/image/flux2.json
workflows/image/flux2.manifest.json
```

It is generated automatically the first time a workflow is loaded, and **never
regenerated**, so anything you correct by hand stays corrected. Press
**Set up…** to edit it in the app.

Only these are ever touched — everything else in your workflow runs exactly as
you built it:

`prompt`, `negative`, `width`, `height`, `ratio`, `seed`, `length`, `batch`,
`output`, and the file slots below.

Three details worth knowing:

- **Linked inputs are left alone.** If width is wired from `GetImageSize`,
  EasyAI will not overwrite it — it disables the shape chooser and says the size
  comes from your picture.
- **One key can drive several nodes.** Flux 2 needs the same width in both
  `EmptyFlux2LatentImage` and `Flux2Scheduler`; setting only one produces a
  mis-scheduled sample. The manifest holds a list, and the editor lets you add
  more targets.
- **Files get one slot each.** See below.

## Workflows that need pictures

Image-to-image, image-to-video and first-frame/last-frame all need the user to
supply pictures, and some need several. EasyAI gives each loader its own
labelled box: up to **10 pictures**, plus 2 sound and 2 video slots
(`image_in`, `image_in_2`, … `audio_in`, `video_in`).

The name on each box is taken from the workflow itself, in this order:

1. **The node's title** — `FIRST FRAME`, `Middle IMG`, `End IMG`, `Load Image3`
2. **The input it feeds** — `start_image` → "Start image", `image5` → "Image 5"
3. **Its position** — "Picture 1" … "Picture 10"

Order is worked out from meaning, not from node numbers. In a real LTX
first/last-frame workflow the LAST frame is node 47 and the FIRST is node 45 —
EasyAI still puts FIRST at the top. Numbered references (`Load Image1`…`5`) sort
numerically regardless of node id.

If a title is wrong or missing, rename the box in **Set up…** and it sticks.

A workflow that loads a picture will not run until one is chosen — the filename
saved inside the workflow points at *your* machine, not the viewer's, so EasyAI
asks for it by name rather than failing inside ComfyUI.

### Files you can leave out

Some inputs are genuinely optional. `MiniMaxH3ReferenceToVideo` takes its
reference images, videos and audio in groups the node marks optional, so a
viewer can attach one picture, or just a sound, and skip the rest. Those boxes
are labelled **(optional)** with a line above them saying so.

EasyAI works this out from the running ComfyUI — an input is optional only when
the node says so, and only when *every* input a loader feeds is optional. A
first-frame picture wired into a required `start_image` stays compulsory. With
no ComfyUI to ask, nothing is treated as optional, because wrongly dropping
something breaks the run while wrongly keeping it costs nothing.

Skipping a file **removes its input from the graph**, and the loader with it.
Merely leaving the value alone would run the workflow against the author's own
filename. Two details that follow from that:

- A loader feeding several inputs takes them all with it. The video loader
  supplies both the footage and its soundtrack, so those go together.
- Numbered groups are closed up afterwards. Attach the first and third picture
  and they arrive as `ref_image_0` and `ref_image_1`, not with a hole at 1 —
  ComfyUI accepts the hole, but the node reads the group by position.

Running with **nothing** attached is refused with a single message. The node
itself allows it (`min: 0`); this is a guardrail, not a limit.

## Prompt enhancer (LTX 2.5)

LTX 2.5 workflows run the prompt through a language model first
(`TextGenerateLTX2Prompt`), turning a short line into the long cinematic
description the model was trained on. It writes far better prompts, but it loads
another multi-gigabyte model and adds time.

When EasyAI finds an enhancer node it shows a tick box:
**"Let the AI improve my wording first"** — on by default, remembered between
runs.

Switching it off is real rewiring, not a flag. An API workflow has no concept of
a muted node, so EasyAI points everything that read from the enhancer at
whatever fed the enhancer instead, then deletes the node — so the language model
never loads at all.

Recognised: `TextGenerateLTX2Prompt`, `TextGenerate`, the `ThinkingLLM` and
`AILab` Qwen-VL prompt enhancers, Plush's `Enhancer` / `AdvPromptEnhancer`, and
anything named like a prompt enhancer. Image enhancers (Topaz, HitPaw,
`LTXVEnhanceAVideoKJ`) are deliberately *not* matched. Bind or clear it by hand
under **Set up… → Prompt improver**.

## Prompt Helper

A third tab for workflows that turn a rough idea into a long, detailed prompt.
Put their API files in `workflows/prompt-enhancer/`.

Type *"a fox in the snow"*, press **Improve my prompt**, and the result appears
on the right. From there: **Copy**, or pick a tab and press **Go** to drop it
straight into Image or Video and switch there.

This mode is different from the other four in that the result is *text*, not a
file. ComfyUI reports it in the history as
`{"<node>": {"text": ["..."]}}`, so the workflow needs a **Preview Any** or
**Show Text** node on its output — without one there is nothing to read back,
and EasyAI says so. Every result is also saved as a `.txt` in
`output/prompt-enhancer/`, and the list underneath reopens past ones.

The "Let the AI improve my wording first" switch is hidden here: on this tab
the workflow *is* the enhancer, so turning it off would leave nothing to run.

## Video length

Video tabs get a **Length** box in seconds, 2–15, defaulting to 5 and
remembered between runs. Under it: the frame count it works out to, and above
8 seconds an amber caution that long clips can exhaust graphics memory.

Workflows almost never expose a frame count directly — they calculate one:

```
LTX 2.5     frames = duration × frame_rate + 1     ComfyMathExpression
MiniMax H3  frames = max(5, round(duration × 24)) + …
```

So the number worth showing is the **Duration** primitive feeding that sum, not
the frame count coming out of it. EasyAI walks upstream from the frame-count
input to find it, telling Duration from Frame Rate by their node titles (both
are plain integers in the same expression). A workflow that really does take a
frame count is handled too — the box still reads seconds and converts.

`ImageFromBatch.length` is deliberately ignored; that means "take this many
images from a batch", not "make an N-frame video".

Change the range or the caution threshold with `video_length_min`,
`video_length_max` and `video_length_warn` in `settings.json`.

## Shapes and pixel sizes

Ratios: `1:1  4:3  3:4  3:2  2:3  16:9  9:16  21:9  9:21`

Image models get a solved size — ratio held as exactly as the model's grid
allows, at roughly the megapixel count it was trained for. Video models use a
fixed table instead, because arbitrary sizes cost quality and VRAM.

| ratio | Flux 2 / Krea 2 / Z-Image | LTX 2.5 | MiniMax H3 |
|---|---|---|---|
| 1:1 | 1024 × 1024 | 960 × 960 | 1024 × 1024 |
| 2:3 | 832 × 1248 | 800 × 1216 | 832 × 1248 |
| 3:4 | 864 × 1152 | 864 × 1152 | 864 × 1152 |
| 9:16 | 720 × 1280 | 704 × 1216 | 768 × 1344 |
| 9:21 | 672 × 1568 | 640 × 1536 | 672 × 1568 |

The chooser always shows the real pixel size, so nothing is hidden.

## Detail (megapixels)

**Image** and **Video** both have a second control next to the shape:
**Detail**, from 0.5 to 2.0 megapixels. The shape decides the proportions; this
decides how big. Change it and the shape list re-labels itself, so the pixel
counts on screen always match the file you get.

**It starts at whatever the workflow itself was built with**, not at a fixed
default — several video workflows here sit at 0.4 or 0.5, and quietly pushing
them to 1.0 would change a result their author tuned and could exhaust the
card. A workflow built below 0.5 keeps its own floor rather than being rounded
up. Only when a workflow states no budget does the remembered setting apply.

The caution threshold differs by tab: 1.5 for pictures, **1.0 for video**,
because video pays the pixel cost on every single frame — 2.0 megapixels across
15 seconds is a very different job from one still image.

It reaches the workflow two ways. A workflow with a `megapixels` input
(`ResolutionSelector`, `ImageScaleToTotalPixels`) is handed the number and does
its own arithmetic; one holding plain width and height gets recalculated values.
A workflow with *both* — Flux 2 keeps the latent size on the chooser and the
scheduler on literals — gets both, set to the same numbers, because if they
disagree the sampler is scheduled for a different picture than it draws.

**A megapixel means 1024 x 1024 to these nodes**, not 1,000,000, so 1:1 at 1.0
is exactly 1024x1024. That was measured against the live node rather than
assumed; `tests/test_megapixels.py` pins fourteen of its answers.

### What the pipeline does afterwards

Choosing the size is not always the end of it. **LTX floors both sides to a
multiple of 64** after the fact: ask for 1376x768 and the file comes out
1344x768; 960x544 becomes 960x512. MiniMax and the image models pass the
chosen size straight through.

That is per engine (`output_grid` in `app/ratios.py`), measured by generating
files and reading their real dimensions back, so the number on screen is the
number in the file. Five such measurements are pinned in the tests.

Some workflows pick their size through a node instead (`ResolutionSelector`).
Those accept a fixed list of labels — `2:3 (Portrait Photo)`,
`9:16 (Portrait Widescreen)` — and the descriptive half differs per entry, so
EasyAI reads the real list from ComfyUI rather than inventing a label. Shapes
the node does not offer are removed from the chooser instead of silently doing
nothing. `ResolutionSelector`, for instance, has no 9:21.

Such a node also decides the pixels itself, from its own megapixel target and
rounding step. EasyAI reads those two numbers out of the workflow and repeats
the calculation, so the size on screen is the size of the file you get — one of
these workflows is set to 0.9 MP and another to 0.5, and the table above would
have been wrong for both.

Adding a model family is one entry in `app/ratios.py`. The workflow's family is
detected from its node types and model filenames, and can be overridden in the
Set up… dialog.

## Settings

**File → Settings**, or `Ctrl+,`. The ComfyUI section tests itself — pick a
folder and it immediately reports whether it found a start file and whether a
server is answering.

Defaults point at `C:\AI ComfyUI - New Version\ComfyUI_windows_portable`.
Turn off *Start ComfyUI automatically* if you run it yourself or on another PC.

## Layout

```
EasyAI.bat             double-click launcher (finds Python, installs deps)
EasyAI.py              entry point
app/
  config.py            settings, with defaults merged over what's on disk
  modes.py             the modes and what each needs
  ratios.py            ratio table, resolution solver, video ladders
  jobs.py              QThread workers (engine startup, one generation)
  comfy/
    client.py          HTTP + websocket: upload, queue, progress, download
    launcher.py        find/start ComfyUI, wait for /system_stats
    objectinfo.py      the pre-flight check
  workflows/
    loader.py          find files, check format, pair with a manifest
    manifest.py        the sidecar: schema and autodetection
    patch.py           write the user's choices into a copy of the graph
  ui/                  PySide6 windows, tabs and widgets
workflows/             ← put exported API workflows here (5 folders, one per tab)
output/                results, one folder per mode
tests/
```

## Tests

```bash
python -m pytest tests/ -q
```

552 unit tests, no ComfyUI needed. The scripts that do need a running server are
separate and are run by hand:

```bash
python tests/smoke_comfy.py
python tests/smoke_workflows.py "E:\ComfyUI Workflow"
python tests/make_starter_workflow.py
python tests/e2e_generate.py image
```

## Requirements

Python 3.10+, and a working ComfyUI.

```bash
pip install -r requirements.txt
```
