"""Workflow discovery, node mapping and graph patching."""

from app.workflows.loader import Format, Workflow, classify, load_workflow, scan, scan_all
from app.workflows.manifest import Binding, Manifest, autodetect, manifest_path_for
from app.workflows.patch import GenerationRequest, PatchReport, apply, missing_inputs

__all__ = [
    "Format", "Workflow", "classify", "load_workflow", "scan", "scan_all",
    "Binding", "Manifest", "autodetect", "manifest_path_for",
    "GenerationRequest", "PatchReport", "apply", "missing_inputs",
]
