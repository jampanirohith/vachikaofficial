from __future__ import annotations

import re
from pathlib import Path

from .types import LRCLine, LRCMetadata, LyricDocument
from .utils import sha256_text

TIMESTAMP_RE = re.compile(r"\[(\d+):(\d{2})(?:\.(\d{1,3}))?\]")
META_RE = re.compile(r"^\[([A-Za-z][A-Za-z0-9_-]{1,20}):(.*)\]\s*$")


def _fraction_to_ms(fraction: str | None) -> int:
    if not fraction:
        return 0
    if len(fraction) == 1:
        return int(fraction) * 100
    if len(fraction) == 2:
        return int(fraction) * 10
    return int(fraction[:3])


def parse_timestamp(value: str) -> int:
    match = TIMESTAMP_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Invalid LRC timestamp: {value}")
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    if seconds >= 60:
        raise ValueError(f"Invalid LRC seconds field: {value}")
    return minutes * 60_000 + seconds * 1_000 + _fraction_to_ms(match.group(3))


def format_timestamp(ms: int, centiseconds: bool = True) -> str:
    ms = max(0, int(ms))
    minutes, rem = divmod(ms, 60_000)
    seconds, rem_ms = divmod(rem, 1_000)
    if not centiseconds:
        return f"[{minutes:02d}:{seconds:02d}.{rem_ms:03d}]"
    cs = int(round(rem_ms / 10.0))
    if cs >= 100:
        seconds += 1
        cs -= 100
    if seconds >= 60:
        minutes += 1
        seconds -= 60
    return f"[{minutes:02d}:{seconds:02d}.{cs:02d}]"


def parse_lrc(path: Path) -> LyricDocument:
    text = path.read_text(encoding="utf-8-sig")
    lines: list[LRCLine] = []
    metadata: list[LRCMetadata] = []
    blank_markers: list[int] = []
    lyric_index = 0

    for raw_line in text.splitlines():
        meta = META_RE.match(raw_line)
        if meta and not TIMESTAMP_RE.search(raw_line):
            metadata.append(LRCMetadata(meta.group(1), meta.group(2), raw_line))
            continue

        matches = list(TIMESTAMP_RE.finditer(raw_line))
        if not matches:
            continue

        text_content = TIMESTAMP_RE.sub("", raw_line).strip()
        kind = "lyric" if text_content else "blank_marker"
        for match in matches:
            ts = parse_timestamp(match.group(0))
            if kind == "blank_marker":
                blank_markers.append(ts)
                line_index = -1
            else:
                line_index = lyric_index
                lyric_index += 1
            lines.append(
                LRCLine(
                    line_index=line_index,
                    timestamp_ms=ts,
                    text=text_content,
                    kind=kind,
                    raw=raw_line,
                )
            )

    lines.sort(key=lambda line: (line.timestamp_ms, 0 if line.kind == "lyric" else 1, line.line_index))
    lyric_idx = 0
    blank_idx = lyric_index
    for line in lines:
        if line.kind == "lyric":
            line.line_index = lyric_idx
            lyric_idx += 1
        else:
            line.line_index = blank_idx
            blank_idx += 1

    document = LyricDocument(
        lines=lines,
        metadata=metadata,
        blank_markers=sorted(set(blank_markers)),
        source_text_sha256=sha256_text(text),
    )
    if not document.lyric_lines:
        raise ValueError("LRC_PARSE_FAILED: no lyric lines found")
    return document


def blank_intervals(document: LyricDocument, duration_ms: int, minimum_ms: int = 1000) -> list[tuple[int, int]]:
    starts = [line.timestamp_ms for line in document.lyric_lines]
    intervals: list[tuple[int, int]] = []
    for marker in sorted(document.blank_markers):
        following = next((t for t in starts if t > marker), duration_ms)
        if following - marker >= minimum_ms:
            intervals.append((marker, min(following, duration_ms)))
    return intervals
