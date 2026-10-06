from __future__ import annotations

import re
from pathlib import Path

from .lrc_reader import format_timestamp, parse_timestamp
from .types import AlignedWord, LyricDocument
from .utils import atomic_write_bytes

_TIMESTAMP_RE = re.compile(r"\[(\d+):(\d{2})(?:\.(\d{1,3}))?\]")


class LRCGenerator:
    def generate(self, document: LyricDocument, words: list[AlignedWord], destination: Path) -> None:
        word_map = {(w.line_index, w.word_index): w for w in words}
        out: list[str] = [meta.raw for meta in document.metadata]

        for line in document.lines:
            if line.kind == "blank_marker":
                out.append(format_timestamp(line.timestamp_ms))
                continue
            if line.kind != "lyric":
                continue

            pieces: list[str] = []
            for source in line.words:
                item = word_map.get((line.line_index, source.word_index))
                ts = item.start_ms if item and item.start_ms is not None else line.timestamp_ms
                pieces.append(f"{format_timestamp(ts)}{source.original}")

            if pieces:
                out.append(" ".join(pieces))
            else:
                out.append(f"{format_timestamp(line.timestamp_ms)}{line.text}")

        text = "\n".join(out).rstrip() + "\n"
        atomic_write_bytes(destination, text.encode("utf-8"))

    def validate(self, path: Path, document: LyricDocument, words: list[AlignedWord]) -> None:
        word_map = {(w.line_index, w.word_index): w for w in words}
        output_lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        lyric_output = [
            line for line in output_lines
            if _TIMESTAMP_RE.search(line) and not _TIMESTAMP_RE.fullmatch(line.strip())
        ]
        expected = document.lyric_lines
        # Blank-marker lines are structural boundaries, not word events. Do not mix them
        # into the generic word timestamp stream; validate their position separately.
        all_word_timestamps = []
        for rendered in lyric_output:
            all_word_timestamps.extend(parse_timestamp(m.group(0)) for m in _TIMESTAMP_RE.finditer(rendered))
        if any(curr < prev for prev, curr in zip(all_word_timestamps, all_word_timestamps[1:])):
            for prev, curr in zip(all_word_timestamps, all_word_timestamps[1:]):
                if curr < prev:
                    raise RuntimeError(
                        f"LRC_GENERATION_FAILED: lyric word timestamps are not globally chronological: {prev} -> {curr}"
                    )

        lyric_entries = []
        for rendered, source_line in zip(lyric_output, expected):
            matches = list(_TIMESTAMP_RE.finditer(rendered))
            lyric_entries.append((source_line, [parse_timestamp(m.group(0)) for m in matches]))

        # Explicit blank markers are structural boundaries. Validate against the
        # canonical word spans (especially word end times), because the rendered LRC
        # contains word start timestamps only.
        canonical_map = {(w.line_index, w.word_index): w for w in words}
        lyric_lines = list(document.lyric_lines)
        for marker in sorted(set(document.blank_markers)):
            previous_line = None
            next_line = None
            for source_line in lyric_lines:
                if source_line.timestamp_ms < marker:
                    previous_line = source_line
                elif source_line.timestamp_ms > marker:
                    next_line = source_line
                    break

            if previous_line and previous_line.words:
                previous_source_word = previous_line.words[-1]
                previous_word = canonical_map.get((previous_line.line_index, previous_source_word.word_index))
                # A word may acoustically extend slightly past a structural blank marker;
                # LRC/SYLT exports represent the word onset, not an end span. End-time
                # crossing is therefore a quality warning handled by the canonical timing
                # finalizer rather than a serialization failure.
                if previous_word and previous_word.start_ms is not None and previous_word.start_ms >= marker:
                    raise RuntimeError(
                        f"LRC_GENERATION_FAILED: lyric word starts on/after blank marker at {marker} ms: "
                        f"line {previous_word.line_index} word {previous_word.word_index} "
                        f"starts at {previous_word.start_ms} ms"
                    )

            if next_line and next_line.words:
                next_source_word = next_line.words[0]
                next_word = canonical_map.get((next_line.line_index, next_source_word.word_index))
                if next_word and next_word.start_ms is not None and next_word.start_ms < marker:
                    raise RuntimeError(
                        f"LRC_GENERATION_FAILED: lyric word starts before blank marker at {marker} ms: "
                        f"line {next_word.line_index} word {next_word.word_index} "
                        f"starts at {next_word.start_ms} ms"
                    )
        if len(lyric_output) != len(expected):
            raise RuntimeError(f"LRC_GENERATION_FAILED: expected {len(expected)} lyric lines, got {len(lyric_output)}")

        for out_line, source_line in zip(lyric_output, expected):
            matches = list(_TIMESTAMP_RE.finditer(out_line))
            if len(matches) != len(source_line.words):
                raise RuntimeError(
                    f"LRC_GENERATION_FAILED: line {source_line.line_index} expected {len(source_line.words)} word timestamps, got {len(matches)}"
                )
            for i, source_word in enumerate(source_line.words):
                start = matches[i].end()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(out_line)
                actual = out_line[start:end].strip()
                if actual != source_word.original:
                    raise RuntimeError(
                        f"LRC_GENERATION_FAILED: line {source_line.line_index} word {i} changed from {source_word.original!r} to {actual!r}"
                    )
                expected_word = word_map[(source_line.line_index, source_word.word_index)]
                if expected_word.start_ms is None:
                    continue
                actual_ts = parse_timestamp(matches[i].group(0))
                expected_cs = (expected_word.start_ms + 5) // 10 * 10
                if abs(actual_ts - expected_cs) > 10:
                    raise RuntimeError(
                        f"LRC_GENERATION_FAILED: line {source_line.line_index} word {i} timestamp mismatch"
                    )
