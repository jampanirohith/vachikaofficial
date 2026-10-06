from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class PipelineError(RuntimeError):
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False
    def __post_init__(self):
        RuntimeError.__init__(self, f"{self.code}: {self.message}")

RETRYABLE_CODES = {
    "YTM_MEDIA_FAILED","SPOTIFY_AUTH_FAILED","SPOTIFY_SEARCH_FAILED",
    "LRCLIB_REQUEST_FAILED","YOUTUBE_REFERENCE_AUDIO_FAILED",
    "YOUTUBE_INTERVAL_UNAVAILABLE","VIDEO_DOWNLOAD_FAILED","VIDEO_TRIM_FAILED",
    "VIDEO_RENDER_FAILED","REEL_ASSEMBLY_FAILED","DEMUCS_OOM","MMS_OOM",
    "FINAL_PROMOTION_FAILED",
}
TERMINAL_CODES = {"NO_SYNCED_LRC","DUPLICATE_KEEP_PREVIOUS","YOUTUBE_OFFSET_NO_VALID_CANDIDATE"}
