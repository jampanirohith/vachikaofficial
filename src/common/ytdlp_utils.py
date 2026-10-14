"""Shared yt-dlp hooks for bounded media-download operations."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


def deadline_progress_hook(
    downloader_module: Any,
    deadline_monotonic: float,
    label: str,
) -> Callable[[dict[str, Any]], None]:
    """Abort yt-dlp when progress callbacks show that the overall deadline expired.

    The per-socket timeout remains necessary to break a stalled request where yt-dlp
    cannot emit a progress callback. Together, these bound both stalled and long-running
    downloads without monkey-patching urllib/requests internals.
    """
    download_error = getattr(getattr(downloader_module, "utils", None), "DownloadError", RuntimeError)

    def check_deadline(_status: dict[str, Any]) -> None:
        if time.monotonic() >= deadline_monotonic:
            raise download_error(f"YTDLP_DOWNLOAD_TIMEOUT: {label} exceeded its configured deadline")

    return check_deadline
