from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from .types import AlignedWord, ChunkAlignment, LyricDocument, SourceWord


class Merger:
    """Merge chunk alignments into one globally chronological reference timeline.

    Chunk audio contains overlap context, so a chunk may place its first word a little
    before the previous chunk's last word. Small cross-chunk backshifts are repaired by
    translating the whole affected chunk while preserving its internal word durations.
    Large or same-chunk violations are left visible for validation/retry rather than
    silently sorting timestamps.
    """

    def __init__(self, max_global_repair_ms: int = 1500):
        self.max_global_repair_ms = max(0, int(max_global_repair_ms))

    @staticmethod
    def _anchor(document: LyricDocument, line_index: int) -> int:
        for line in document.lyric_lines:
            if line.line_index == line_index:
                return line.timestamp_ms
        return 0

    @staticmethod
    def _next_anchor(document: LyricDocument, line_index: int, duration_ms: int) -> int:
        for line in document.lyric_lines:
            if line.line_index > line_index:
                return line.timestamp_ms
        return duration_ms

    @staticmethod
    def _key(word: AlignedWord) -> tuple[int, int]:
        return (word.line_index, word.word_index)

    def merge(self, document: LyricDocument, chunk_results: list[ChunkAlignment], duration_ms: int) -> list[AlignedWord]:
        expected: dict[tuple[int, int], SourceWord] = {
            (line.line_index, word.word_index): word
            for line in document.lyric_lines
            for word in line.words
        }
        candidates: dict[tuple[int, int], list[AlignedWord]] = defaultdict(list)
        for result in chunk_results:
            for word in result.words:
                if word.start_ms is not None and word.end_ms is not None:
                    candidates[self._key(word)].append(word)

        selected: list[AlignedWord] = []
        for key in sorted(expected):
            source = expected[key]
            options = candidates.get(key, [])
            if options:
                anchor = self._anchor(document, source.line_index)
                # Prefer a candidate that is good, close to its LRC anchor, and valid.
                options = sorted(options, key=lambda w: (
                    -(w.score if w.score is not None else -1.0),
                    abs((w.start_ms if w.start_ms is not None else anchor) - anchor),
                ))
                chosen = replace(options[0])
                selected.append(chosen)
            else:
                selected.append(AlignedWord(
                    source.line_index, source.word_index, source.original, source.normalized,
                    None, None, None, source="missing", reason="no_chunk_candidate",
                    input_supported=source.supported,
                ))

        # First interpolate against the entire reference sequence, not just within one line.
        self._interpolate_missing(selected, document, duration_ms)
        self._repair_global_continuity(selected, document, duration_ms)
        return selected

    def _interpolate_missing(self, words: list[AlignedWord], document: LyricDocument, duration_ms: int) -> None:
        """Interpolate unresolved words using nearest timed words in global lyric order."""
        timed_indices = [i for i, item in enumerate(words) if item.start_ms is not None and item.end_ms is not None]
        missing_positions = [i for i, item in enumerate(words) if item.start_ms is None or item.end_ms is None]
        if not missing_positions:
            return

        for pos in missing_positions:
            if words[pos].start_ms is not None:
                continue
            prev_i = next((i for i in reversed(timed_indices) if i < pos), None)
            next_i = next((i for i in timed_indices if i > pos), None)

            prev = words[prev_i] if prev_i is not None else None
            nxt = words[next_i] if next_i is not None else None
            left = (prev.end_ms if prev and prev.end_ms is not None else
                    prev.start_ms if prev and prev.start_ms is not None else
                    self._anchor(document, words[pos].line_index))
            right = (nxt.start_ms if nxt and nxt.start_ms is not None else
                     self._next_anchor(document, words[pos].line_index, duration_ms))

            # Explicit LRC blank markers are hard lyric boundaries. Do not interpolate a
            # missing word across such a marker. This keeps fallback timing inside the
            # same lyric region and prevents the output stage from manufacturing a second
            # chronology/boundary failure.
            line_index = words[pos].line_index
            line = next((x for x in document.lyric_lines if x.line_index == line_index), None)
            if line is not None:
                preceding_markers = [m for m in document.blank_markers if m > line.timestamp_ms]
                if preceding_markers:
                    boundary = preceding_markers[0]
                    next_line = next((x for x in document.lyric_lines if x.line_index > line_index), None)
                    if next_line is None or next_line.timestamp_ms > boundary:
                        right = min(right, boundary)
                previous_line = next((x for x in reversed(document.lyric_lines) if x.line_index < line_index), None)
                if previous_line is not None:
                    preceding = [m for m in document.blank_markers if previous_line.timestamp_ms < m < line.timestamp_ms]
                    if preceding:
                        left = max(left, preceding[-1])

            if right <= left:
                right = min(duration_ms, left + max(100, 120 * (1 + (next_i - pos if next_i is not None else 0))))

            # Find the full consecutive missing block bounded by timed words globally.
            block_start = pos
            while block_start > 0 and (words[block_start - 1].start_ms is None or words[block_start - 1].end_ms is None):
                block_start -= 1
            block_end = pos
            while block_end + 1 < len(words) and (words[block_end + 1].start_ms is None or words[block_end + 1].end_ms is None):
                block_end += 1
            block = words[block_start:block_end + 1]
            step = max(1.0, (right - left) / max(1, len(block)))
            for local, target in enumerate(block):
                start = int(round(left + step * local))
                end = int(round(left + step * (local + 1)))
                start = max(0, min(start, duration_ms - 1))
                end = max(start + 1, min(end, duration_ms))
                target.start_ms = start
                target.end_ms = end
                target.source = "interpolated"
                target.reason = "unsupported_token_interpolated" if not target.input_supported else "alignment_gap_interpolated"

            # Newly filled positions are now timed and should not be re-filled on a later iteration.
            timed_indices.extend(range(block_start, block_end + 1))
            timed_indices.sort()

    @staticmethod
    def global_violations(words: list[AlignedWord]) -> list[dict[str, object]]:
        """Return chronological-order violations in reference word order."""
        timed = [w for w in sorted(words, key=lambda x: (x.line_index, x.word_index))
                 if w.start_ms is not None and w.end_ms is not None]
        violations: list[dict[str, object]] = []
        for prev, curr in zip(timed, timed[1:]):
            if curr.start_ms < prev.start_ms:
                violations.append({
                    "previous": prev,
                    "current": curr,
                    "delta_ms": int(curr.start_ms - prev.start_ms),
                    "same_chunk": prev.chunk_id is not None and prev.chunk_id == curr.chunk_id,
                })
        return violations


    @staticmethod
    def boundary_violations(document: LyricDocument, words: list[AlignedWord]) -> list[dict[str, object]]:
        """Return violations against explicit blank-marker boundaries from the source LRC.

        A blank marker denotes the beginning of a lyric-free/instrumental interval. The
        previous lyric line must finish before that marker, and the following lyric line
        must begin at or after it. This is checked separately from generic chronological
        word-order checks because the marker is a structural event, not a lyric word.
        """
        timed = {
            (w.line_index, w.word_index): w
            for w in words
            if w.start_ms is not None and w.end_ms is not None
        }
        lyric_lines = list(document.lyric_lines)
        violations: list[dict[str, object]] = []
        for marker in sorted(set(document.blank_markers)):
            previous_line = None
            next_line = None
            for line in lyric_lines:
                if line.timestamp_ms < marker:
                    previous_line = line
                elif line.timestamp_ms > marker:
                    next_line = line
                    break
            if previous_line is not None and previous_line.words:
                last_source = previous_line.words[-1]
                last_word = timed.get((previous_line.line_index, last_source.word_index))
                if last_word is not None and last_word.end_ms > marker:
                    violations.append({
                        "type": "lyric_crosses_blank_marker",
                        "marker_ms": int(marker),
                        "chunk_id": last_word.chunk_id,
                        "word": last_word,
                        "side": "previous",
                    })
            if next_line is not None and next_line.words:
                first_source = next_line.words[0]
                first_word = timed.get((next_line.line_index, first_source.word_index))
                if first_word is not None and first_word.start_ms < marker:
                    violations.append({
                        "type": "lyric_starts_before_blank_marker",
                        "marker_ms": int(marker),
                        "chunk_id": first_word.chunk_id,
                        "word": first_word,
                        "side": "next",
                    })
        return violations

    @staticmethod
    def _line_bounds(document: LyricDocument, line_index: int) -> tuple[int, int]:
        """Return the lyric-safe timing region for a source lyric line.

        Explicit blank LRC markers define structural lyric-free boundaries. A lyric line
        may not have its word starts outside the region between the nearest preceding and
        following blank markers. The song edges are used when no marker exists.
        """
        line = next((x for x in document.lyric_lines if x.line_index == line_index), None)
        if line is None:
            return 0, 2**63 - 1
        lower = max((m for m in document.blank_markers if m <= line.timestamp_ms), default=0)
        upper = min((m for m in document.blank_markers if m > line.timestamp_ms), default=2**63 - 1)
        return int(lower), int(upper)

    @staticmethod
    def _append_reason(word: AlignedWord, reason: str) -> None:
        word.reason = f"{word.reason};{reason}" if word.reason else reason

    def _repair_global_continuity(
        self,
        words: list[AlignedWord],
        document: LyricDocument,
        duration_ms: int,
    ) -> None:
        """Repair timestamp regressions without changing reference word order.

        The previous implementation translated entire chunks. That can re-introduce a
        blank-marker violation after a strict retry, and it cannot repair regressions that
        occur within a single chunk. This implementation repairs the individual current
        word forward, preserving its duration and respecting the source LRC region.
        """
        previous: AlignedWord | None = None
        for current in words:
            if current.start_ms is None or current.end_ms is None:
                continue
            start = int(current.start_ms)
            end = int(current.end_ms)
            if previous is not None and previous.start_ms is not None and start < int(previous.start_ms):
                delta = int(previous.start_ms) - start
                if delta <= self.max_global_repair_ms:
                    start += delta
                    end += delta
                    self._append_reason(current, f"global_continuity_shift_+{delta}ms")
                else:
                    self._append_reason(current, f"global_continuity_large_violation_{delta}ms")

            lower, upper = self._line_bounds(document, current.line_index)
            if start < lower:
                delta = lower - start
                start += delta
                end += delta
                self._append_reason(current, f"blank_region_start_shift_+{delta}ms")
            if upper < 2**63 - 1 and start >= upper:
                # This is a serious source/reference conflict, but keep the output valid
                # for downstream renderers and mark the word for review.
                old = start
                start = max(lower, upper - 1)
                duration = max(1, end - old)
                end = min(duration_ms, start + duration)
                self._append_reason(current, f"blank_region_start_clamped_to_{upper}ms")
            if upper < 2**63 - 1 and end > upper:
                end = upper
                self._append_reason(current, f"blank_region_end_clamped_to_{upper}ms")
            start = max(0, min(start, max(0, duration_ms - 1)))
            end = max(start + 1, min(end, duration_ms))
            if end <= start:
                current.start_ms = None
                current.end_ms = None
                current.source = "missing"
                self._append_reason(current, "unrecoverable_timing_span")
                continue
            current.start_ms = start
            current.end_ms = end
            previous = current

    def finalize_output_timing(
        self,
        document: LyricDocument,
        words: list[AlignedWord],
        duration_ms: int,
    ) -> list[dict[str, object]]:
        """Make the final canonical timing render-safe after alignment/retries.

        This is a last-resort normalization stage, not an accuracy claim. It never sorts
        words. It changes only the timing attached to the affected reference word and
        records the reason on that word. Persistent or large repairs are surfaced as
        warnings so a song can be emitted as needs_review/partial instead of being lost
        solely because an output renderer cannot serialize a non-monotonic timeline.
        """
        warnings: list[dict[str, object]] = []

        # Work in reference lyric order; caller supplies words in that order.
        for word in words:
            if word.start_ms is None or word.end_ms is None:
                continue
            lower, upper = self._line_bounds(document, word.line_index)
            original_start = int(word.start_ms)
            original_end = int(word.end_ms)

            if original_start < lower:
                delta = lower - original_start
                word.start_ms += delta
                word.end_ms += delta
                self._append_reason(word, f"final_region_shift_+{delta}ms")
                warnings.append({
                    "type": "region_start_shift",
                    "line_index": word.line_index,
                    "word_index": word.word_index,
                    "delta_ms": delta,
                })

            if upper < 2**63 - 1 and word.start_ms >= upper:
                duration = max(1, int(word.end_ms) - int(word.start_ms))
                word.start_ms = max(lower, upper - 1)
                word.end_ms = min(duration_ms, word.start_ms + duration)
                self._append_reason(word, f"final_region_start_clamped_to_{upper}ms")
                warnings.append({
                    "type": "region_start_clamp",
                    "line_index": word.line_index,
                    "word_index": word.word_index,
                    "boundary_ms": upper,
                })

            if upper < 2**63 - 1 and word.end_ms > upper:
                word.end_ms = upper
                self._append_reason(word, f"final_region_end_clamped_to_{upper}ms")
                warnings.append({
                    "type": "region_end_clamp",
                    "line_index": word.line_index,
                    "word_index": word.word_index,
                    "boundary_ms": upper,
                })

            if word.end_ms <= word.start_ms:
                word.end_ms = min(duration_ms, int(word.start_ms) + 1)
                if word.end_ms <= word.start_ms:
                    word.start_ms = max(0, duration_ms - 1)
                    word.end_ms = duration_ms
                self._append_reason(word, "final_minimum_word_span")

        # Two forward passes are enough for local changes to propagate through the
        # reference sequence without sorting or changing lyric identity.
        for _ in range(2):
            previous: AlignedWord | None = None
            for word in words:
                if word.start_ms is None or word.end_ms is None:
                    continue
                if previous is not None and previous.start_ms is not None and word.start_ms < previous.start_ms:
                    delta = int(previous.start_ms) - int(word.start_ms)
                    word.start_ms += delta
                    word.end_ms += delta
                    lower, upper = self._line_bounds(document, word.line_index)
                    if upper < 2**63 - 1 and word.end_ms > upper:
                        word.end_ms = upper
                    if upper < 2**63 - 1 and word.start_ms >= upper:
                        word.start_ms = max(lower, upper - 1)
                        word.end_ms = max(word.start_ms + 1, min(duration_ms, upper))
                    if word.end_ms <= word.start_ms:
                        word.start_ms = max(lower, min(int(word.start_ms), max(lower, upper - 1))) if upper < 2**63 - 1 else max(0, min(int(word.start_ms), duration_ms - 1))
                        word.end_ms = min(duration_ms, int(word.start_ms) + 1)
                    label = "final_output_monotonic_shift_+%dms" % delta
                    self._append_reason(word, label)
                    warnings.append({
                        "type": "global_monotonic_shift",
                        "line_index": word.line_index,
                        "word_index": word.word_index,
                        "delta_ms": delta,
                    })
                if word.start_ms is not None and word.end_ms is not None:
                    previous = word

        # Final explicit-boundary pass. End-time crossings are clipped because LRC/SYLT
        # exports only need word starts; starting a lyric word on the wrong side of a blank
        # marker is corrected as well.
        for marker in sorted(set(document.blank_markers)):
            previous_line = None
            next_line = None
            for line in document.lyric_lines:
                if line.timestamp_ms < marker:
                    previous_line = line
                elif line.timestamp_ms > marker:
                    next_line = line
                    break
            if previous_line and previous_line.words:
                source = previous_line.words[-1]
                word = next((w for w in words if w.line_index == previous_line.line_index and w.word_index == source.word_index), None)
                if word and word.start_ms is not None and word.start_ms >= marker:
                    word.start_ms = max(0, marker - 1)
                    word.end_ms = marker
                    self._append_reason(word, f"final_previous_line_clamp_before_marker_{marker}ms")
                    warnings.append({"type": "previous_line_start_clamp", "marker_ms": marker, "line_index": word.line_index, "word_index": word.word_index})
                elif word and word.end_ms is not None and word.end_ms > marker:
                    word.end_ms = marker
                    if word.end_ms <= word.start_ms:
                        word.end_ms = min(duration_ms, word.start_ms + 1)
                    self._append_reason(word, f"final_previous_line_end_clamp_at_marker_{marker}ms")
                    warnings.append({"type": "previous_line_end_clamp", "marker_ms": marker, "line_index": word.line_index, "word_index": word.word_index})
            if next_line and next_line.words:
                source = next_line.words[0]
                word = next((w for w in words if w.line_index == next_line.line_index and w.word_index == source.word_index), None)
                if word and word.start_ms is not None and word.start_ms < marker:
                    delta = marker - int(word.start_ms)
                    word.start_ms += delta
                    word.end_ms += delta
                    self._append_reason(word, f"final_next_line_shift_to_marker_+{delta}ms")
                    warnings.append({"type": "next_line_start_shift", "marker_ms": marker, "line_index": word.line_index, "word_index": word.word_index, "delta_ms": delta})

        return warnings
