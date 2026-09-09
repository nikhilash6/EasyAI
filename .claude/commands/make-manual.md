---
description: Generate the user manual for this project end-to-end using the manual-* subagent pipeline, ending in a PDF
---

Run the full user-manual pipeline for this project, in order. Don't skip a stage or reorder them, and pass each stage's output forward by the file paths already defined in the agents (docs/manual/...):

1. Dispatch the `manual-explorer` subagent to read this project's codebase and produce docs/manual/outline.md.
2. Once that's done, dispatch the `manual-screenshot` subagent to launch the app and capture screenshots per the outline.
3. Once that's done, dispatch the `manual-writer` subagent to draft docs/manual/manual.md from the outline and screenshots.
4. Once that's done, dispatch the `manual-formatter` subagent to produce docs/manual/EasyAI-Manual.docx and, if possible, docs/manual/EasyAI-Manual.pdf.

After all four finish, report the final PDF path if it exists (fall back to the docx path if PDF conversion wasn't available), and flag anything any stage marked as "Needs Confirmation" or reported as failed.
