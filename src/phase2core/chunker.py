from __future__ import annotations

from .types import ChunkSpec, LRCLine, LyricDocument


class Chunker:
    """Creates non-overlapping reference chunks with overlapping model-audio context."""

    def __init__(self, min_ms: int, target_ms: int, max_ms: int, context_before_ms: int,
                 context_after_ms: int, pause_threshold_ms: int, large_gap_threshold_ms: int):
        self.min_ms = int(min_ms)
        self.target_ms = int(target_ms)
        self.max_ms = int(max_ms)
        self.context_before_ms = int(context_before_ms)
        self.context_after_ms = int(context_after_ms)
        self.pause_threshold_ms = int(pause_threshold_ms)
        self.large_gap_threshold_ms = int(large_gap_threshold_ms)

    @staticmethod
    def _first_blank_between(markers: list[int], start_ms: int, end_ms: int) -> int | None:
        for marker in markers:
            if start_ms < marker < end_ms:
                return marker
        return None

    def _logical_end(self, last: LRCLine, next_line: LRCLine | None, document: LyricDocument,
                     duration_ms: int) -> tuple[int, str]:
        next_start = next_line.timestamp_ms if next_line else duration_ms
        marker = self._first_blank_between(document.blank_markers, last.timestamp_ms, next_start)
        if marker is not None:
            return min(duration_ms, max(last.timestamp_ms + 1, marker)), "explicit_lrc_blank_marker"
        natural_end = max(last.timestamp_ms + 500, next_start)
        return min(duration_ms, natural_end), "lyric_or_pause_boundary"

    def build(self, document: LyricDocument, duration_ms: int) -> list[ChunkSpec]:
        lines = document.lyric_lines
        if not lines:
            return []

        chunks: list[ChunkSpec] = []
        current: list[LRCLine] = []
        line_pos = {id(line): i for i, line in enumerate(lines)}

        def next_line_for(last: LRCLine) -> LRCLine | None:
            pos = line_pos[id(last)]
            return lines[pos + 1] if pos + 1 < len(lines) else None

        def make_chunk(group: list[LRCLine], boundary_reason: str) -> ChunkSpec:
            first, last = group[0], group[-1]
            next_line = next_line_for(last)
            logical_end, end_reason = self._logical_end(last, next_line, document, duration_ms)
            return ChunkSpec(
                chunk_index=len(chunks),
                logical_start_ms=max(0, first.timestamp_ms),
                logical_end_ms=max(first.timestamp_ms + 1, logical_end),
                audio_start_ms=max(0, first.timestamp_ms - self.context_before_ms),
                audio_end_ms=min(duration_ms, logical_end + self.context_after_ms),
                line_start_index=first.line_index,
                line_end_index=last.line_index,
                text_content="\n".join(x.text for x in group),
                normalized_text=" ".join(w.normalized for x in group for w in x.words if w.normalized),
                words=[w for x in group for w in x.words],
                boundary_reason=f"{boundary_reason};end={end_reason}",
            )

        for line in lines:
            if not current:
                current = [line]
                continue

            first = current[0]
            previous = current[-1]
            span = line.timestamp_ms - first.timestamp_ms
            gap = line.timestamp_ms - previous.timestamp_ms
            explicit_blank = self._first_blank_between(document.blank_markers, previous.timestamp_ms, line.timestamp_ms)

            split = False
            reason = ""
            if explicit_blank is not None:
                split = True
                reason = "explicit_lrc_blank_marker"
            elif span >= self.max_ms:
                split = True
                reason = "max_duration"
            elif span >= self.target_ms and gap >= self.pause_threshold_ms:
                split = True
                reason = "target_duration_plus_pause"
            elif span >= self.target_ms and gap >= self.large_gap_threshold_ms:
                split = True
                reason = "large_gap_after_target"

            if split:
                chunks.append(make_chunk(current, reason))
                current = [line]
            else:
                current.append(line)

        if current:
            chunks.append(make_chunk(current, "end_of_lyrics"))

        # Merge only undersized adjacent chunks when they are not separated by an explicit blank interval.
        merged: list[ChunkSpec] = []
        for chunk in chunks:
            if merged and chunk.logical_end_ms - chunk.logical_start_ms < self.min_ms:
                prev = merged[-1]
                has_blank = any(
                    prev.logical_end_ms <= marker <= chunk.logical_start_ms
                    for marker in document.blank_markers
                )
                if not has_blank:
                    prev.logical_end_ms = chunk.logical_end_ms
                    prev.audio_end_ms = chunk.audio_end_ms
                    prev.line_end_index = chunk.line_end_index
                    prev.text_content = (prev.text_content + "\n" + chunk.text_content).strip()
                    prev.normalized_text = (prev.normalized_text + " " + chunk.normalized_text).strip()
                    prev.words.extend(chunk.words)
                    prev.boundary_reason += ";merged_short_chunk"
                    continue
            merged.append(chunk)

        for i, chunk in enumerate(merged):
            chunk.chunk_index = i
        return merged
