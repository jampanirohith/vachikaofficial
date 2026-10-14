"""Path helpers for portable manifests and configurable output directories."""
from __future__ import annotations

from pathlib import Path


def display_path(path: str | Path, project_root: str | Path) -> str:
    """Serialize a path relative to ``project_root`` when possible.

    Config permits absolute output directories outside the project. Calling
    ``Path.relative_to`` unconditionally raises for those valid configurations, so
    external paths are preserved as absolute paths instead of crashing a pipeline
    stage. Project-local paths are normalized to forward slashes for JSON/DB data.
    """
    candidate = Path(path).expanduser().resolve()
    root = Path(project_root).expanduser().resolve()
    try:
        return candidate.relative_to(root).as_posix()
    except ValueError:
        return str(candidate)
