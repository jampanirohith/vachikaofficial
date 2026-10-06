from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass(slots=True)
class LRCMetadata:
    key: str
    value: str
    raw: str


@dataclass(slots=True)
class LRCLine:
    line_index: int
    timestamp_ms: int
    text: str
    kind: str = "lyric"  # lyric / blank_marker
    raw: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    words: list["SourceWord"] = field(default_factory=list)


@dataclass(slots=True)
class SourceWord:
    line_index: int
    word_index: int
    original: str
    normalized: str
    token_start: int | None = None
    token_end: int | None = None
    supported: bool = True
    normalization_ops: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class LyricDocument:
    lines: list[LRCLine]
    metadata: list[LRCMetadata] = field(default_factory=list)
    blank_markers: list[int] = field(default_factory=list)
    source_text_sha256: str = ""

    @property
    def lyric_lines(self) -> list[LRCLine]:
        return [line for line in self.lines if line.kind == "lyric" and line.text.strip()]


@dataclass(slots=True)
class TokenRef:
    token_index: int
    token_id: int
    line_index: int
    word_index: int


@dataclass(slots=True)
class TokenSpan:
    token_index: int
    token_id: int
    start_frame: int
    end_frame: int
    mean_log_prob: float
    geo_prob: float


@dataclass(slots=True)
class AlignedWord:
    line_index: int
    word_index: int
    original: str
    normalized: str
    start_ms: Optional[int]
    end_ms: Optional[int]
    score: Optional[float]
    source: str = "aligned"  # aligned / interpolated / missing
    chunk_id: Optional[int] = None
    reason: Optional[str] = None
    input_supported: bool = True


@dataclass(slots=True)
class ChunkSpec:
    chunk_index: int
    logical_start_ms: int
    logical_end_ms: int
    audio_start_ms: int
    audio_end_ms: int
    line_start_index: int
    line_end_index: int
    text_content: str
    normalized_text: str
    words: list[SourceWord] = field(default_factory=list)
    audio_path: Optional[Path] = None
    result_json_path: Optional[Path] = None
    boundary_reason: str = "lyric_lines"


@dataclass(slots=True)
class ChunkAlignment:
    chunk: ChunkSpec
    words: list[AlignedWord]
    mean_score: Optional[float]
    p10_score: Optional[float]
    minimum_score: Optional[float]
    frame_count: int
    token_count: int
    stride_ms: float
    status: str = "aligned"
    error_code: Optional[str] = None
    error_remark: Optional[str] = None
    input_audio: str = "vocals"
    device: Optional[str] = None


@dataclass(slots=True)
class QualityMetrics:
    expected_lines: int = 0
    aligned_lines: int = 0
    expected_words: int = 0
    aligned_words: int = 0
    interpolated_words: int = 0
    missing_words: int = 0
    unsupported_words: int = 0
    total_chunks: int = 0
    successful_chunks: int = 0
    low_confidence_chunks: int = 0
    failed_chunks: int = 0
    mean_alignment_score: Optional[float] = None
    p10_alignment_score: Optional[float] = None
    minimum_alignment_score: Optional[float] = None
    low_score_word_percent: float = 0.0
    interpolated_word_percent: float = 0.0
    failed_audio_percent: float = 0.0
    median_anchor_shift_ms: Optional[float] = None
    p90_anchor_shift_ms: Optional[float] = None
    max_anchor_shift_ms: Optional[float] = None
    continuity_repairs: int = 0
    max_continuity_repair_ms: int = 0
    continuity_violation_count: int = 0
    continuity_first_violation: dict[str, Any] | None = None
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class InputPackage:
    basename: str
    mp3_path: Path
    lrc_path: Path
    json_path: Path


@dataclass(slots=True)
class ScanIssue:
    basename: str
    path: str
    issue: str
    severity: str = "error"


@dataclass(slots=True)
class SourceSnapshot:
    frame_fingerprints: dict[str, str]
    frame_keys: list[str]
    artwork_sha256: Optional[str]
    audio_duration_ms: int


@dataclass(slots=True)
class EmissionResult:
    log_probs: Any
    frame_count: int
    class_count: int
    stride_ms: float
    device: str


@dataclass(slots=True)
class ActivityResult:
    speech_intervals: list[tuple[int, int]] = field(default_factory=list)
    energy_intervals: list[tuple[int, int]] = field(default_factory=list)
