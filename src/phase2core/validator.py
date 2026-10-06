from __future__ import annotations

from collections import defaultdict
from statistics import median

from .types import AlignedWord, LyricDocument, QualityMetrics
from .utils import percentile


class Validator:
    def validate_alignment(
        self,
        document: LyricDocument,
        words: list[AlignedWord],
        duration_ms: int,
        min_word_score: float,
        anchor_warn_ms: int,
        failed_audio_ms: int = 0,
        min_word_duration_ms: int = 30,
        max_word_duration_ms: int = 5000,
    ) -> tuple[QualityMetrics, list[str]]:
        metrics = QualityMetrics()
        metrics.expected_lines = len(document.lyric_lines)
        metrics.expected_words = sum(len(line.words) for line in document.lyric_lines)
        metrics.aligned_words = sum(1 for w in words if w.source == "aligned" and w.start_ms is not None)
        metrics.interpolated_words = sum(1 for w in words if w.source == "interpolated" and w.start_ms is not None)
        metrics.missing_words = sum(1 for w in words if w.start_ms is None or w.end_ms is None)
        metrics.unsupported_words = sum(
            1 for line in document.lyric_lines for word in line.words if not word.supported
        )
        metrics.aligned_lines = len({w.line_index for w in words if w.start_ms is not None})
        metrics.failed_audio_percent = 100.0 * max(0, failed_audio_ms) / max(1, duration_ms)

        scores = [float(w.score) for w in words if w.source == "aligned" and w.score is not None]
        metrics.mean_alignment_score = sum(scores) / len(scores) if scores else None
        metrics.p10_alignment_score = percentile(scores, 10)
        metrics.minimum_alignment_score = min(scores) if scores else None
        metrics.low_score_word_percent = 100.0 * sum(x < min_word_score for x in scores) / max(1, len(scores))
        metrics.interpolated_word_percent = 100.0 * metrics.interpolated_words / max(1, metrics.expected_words)

        reasons: list[str] = []
        expected_keys = {(line.line_index, word.word_index) for line in document.lyric_lines for word in line.words}
        actual_keys = {(w.line_index, w.word_index) for w in words}
        if expected_keys != actual_keys:
            reasons.append("REFERENCE_PRESERVATION_FAILED")

        by_line: dict[int, list[AlignedWord]] = defaultdict(list)
        global_timed = [w for w in sorted(words, key=lambda x: (x.line_index, x.word_index)) if w.start_ms is not None and w.end_ms is not None]
        for word in words:
            by_line[word.line_index].append(word)
            if word.start_ms is not None and word.end_ms is not None:
                if not (0 <= word.start_ms < word.end_ms <= duration_ms):
                    reasons.append("TIMESTAMP_INVALID")
                else:
                    word_duration = word.end_ms - word.start_ms
                    if word.source == "aligned" and (word_duration < min_word_duration_ms or word_duration > max_word_duration_ms):
                        reasons.append("SUSPICIOUS_WORD_DURATION")

        for prev, curr in zip(global_timed, global_timed[1:]):
            if curr.start_ms < prev.start_ms:
                metrics.continuity_violation_count += 1
                if metrics.continuity_first_violation is None:
                    metrics.continuity_first_violation = {
                        "previous": {"line_index": prev.line_index, "word_index": prev.word_index, "word": prev.original, "start_ms": prev.start_ms, "end_ms": prev.end_ms},
                        "current": {"line_index": curr.line_index, "word_index": curr.word_index, "word": curr.original, "start_ms": curr.start_ms, "end_ms": curr.end_ms},
                        "delta_ms": int(curr.start_ms - prev.start_ms),
                    }
                reasons.append("GLOBAL_TIMESTAMP_ORDER_INVALID")

        # Explicit blank markers are hard lyric boundaries. A previous lyric word must
        # end by the marker and the next lyric word must start at/after it.
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
                prev_source = previous_line.words[-1]
                prev_word = next((w for w in words if w.line_index == previous_line.line_index and w.word_index == prev_source.word_index), None)
                if prev_word and prev_word.end_ms is not None and prev_word.end_ms > marker:
                    reasons.append("LYRIC_CROSSES_BLANK_MARKER")
            if next_line and next_line.words:
                next_source = next_line.words[0]
                next_word = next((w for w in words if w.line_index == next_line.line_index and w.word_index == next_source.word_index), None)
                if next_word and next_word.start_ms is not None and next_word.start_ms < marker:
                    reasons.append("LYRIC_STARTS_BEFORE_BLANK_MARKER")

        for line_words in by_line.values():
            timed = [w for w in sorted(line_words, key=lambda x: x.word_index) if w.start_ms is not None and w.end_ms is not None]
            for prev, curr in zip(timed, timed[1:]):
                if curr.start_ms < prev.start_ms:
                    reasons.append("WORD_ORDER_INVALID")
                if curr.start_ms < prev.end_ms - 10:
                    reasons.append("WORD_OVERLAP")

        shifts: list[float] = []
        for line in document.lyric_lines:
            first = next((w for w in words if w.line_index == line.line_index and w.start_ms is not None), None)
            if first:
                shifts.append(abs(float(first.start_ms - line.timestamp_ms)))
        if shifts:
            metrics.median_anchor_shift_ms = float(median(shifts))
            metrics.p90_anchor_shift_ms = percentile(shifts, 90)
            metrics.max_anchor_shift_ms = max(shifts)
            if (metrics.p90_anchor_shift_ms or 0) > anchor_warn_ms:
                reasons.append("ANCHOR_DRIFT")

        if metrics.mean_alignment_score is None:
            reasons.append("NO_DIRECT_ALIGNMENT")
        elif metrics.mean_alignment_score < 0.50:
            reasons.append("LOW_MEAN_SCORE")
        if metrics.p10_alignment_score is not None and metrics.p10_alignment_score < min_word_score:
            reasons.append("LOW_P10_SCORE")
        if metrics.low_score_word_percent > 20:
            reasons.append("MANY_LOW_SCORE_WORDS")
        if metrics.interpolated_word_percent > 20:
            reasons.append("HIGH_INTERPOLATION")
        if metrics.missing_words:
            reasons.append("MISSING_WORDS")
        if metrics.failed_audio_percent > 20:
            reasons.append("HIGH_FAILED_AUDIO_PERCENT")

        metrics.reasons = sorted(set(reasons))
        return metrics, metrics.reasons

    @staticmethod
    def classify(metrics: QualityMetrics, failed_chunks: int) -> str:
        fatal = {"TIMESTAMP_INVALID", "WORD_ORDER_INVALID", "WORD_OVERLAP", "GLOBAL_TIMESTAMP_ORDER_INVALID", "REFERENCE_PRESERVATION_FAILED", "LYRIC_CROSSES_BLANK_MARKER", "LYRIC_STARTS_BEFORE_BLANK_MARKER"}
        if metrics.aligned_words == 0 and (failed_chunks > 0 or not metrics.mean_alignment_score):
            return "failed"
        if fatal.intersection(metrics.reasons):
            return "needs_review"
        if metrics.mean_alignment_score is None:
            return "failed"
        if metrics.missing_words or failed_chunks or metrics.interpolated_words > 0:
            return "partial"
        if {"ANCHOR_DRIFT", "LOW_MEAN_SCORE", "LOW_P10_SCORE", "MANY_LOW_SCORE_WORDS", "HIGH_FAILED_AUDIO_PERCENT", "SUSPICIOUS_WORD_DURATION", "VOCAL_STEM_FALLBACK"}.intersection(metrics.reasons):
            return "needs_review"
        return "good"
