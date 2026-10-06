from __future__ import annotations

from typing import Any

from .json_manager import JSONManager
from .types import AlignedWord, ChunkAlignment, LyricDocument, QualityMetrics


class JSONUpdater:
    def __init__(self, manager: JSONManager):
        self.manager = manager

    @staticmethod
    def _word_payload(word: AlignedWord) -> dict[str, Any]:
        return {
            "word_index": word.word_index,
            "original": word.original,
            "normalized": word.normalized,
            "start_ms": word.start_ms,
            "end_ms": word.end_ms,
            "score": word.score,
            "source": word.source,
            "chunk_id": word.chunk_id,
            "reason": word.reason,
            "input_supported": word.input_supported,
        }

    def alignment_payload(self, document: LyricDocument, words: list[AlignedWord]) -> dict[str, Any]:
        word_map = {(w.line_index, w.word_index): w for w in words}
        lines: list[dict[str, Any]] = []
        for line in document.lyric_lines:
            items = [
                self._word_payload(word_map[(line.line_index, src.word_index)])
                for src in line.words
                if (line.line_index, src.word_index) in word_map
            ]
            first = next((x for x in items if x["start_ms"] is not None), None)
            lines.append({
                "line_index": line.line_index,
                "source_lrc_start_ms": line.timestamp_ms,
                "aligned_start_ms": first["start_ms"] if first else None,
                "original_text": line.text,
                "normalized_text": " ".join(w.normalized for w in line.words if w.normalized),
                "words": items,
            })
        return {
            "method": "ctc_forced_alignment",
            "time_unit": "milliseconds",
            "line_count": len(lines),
            "word_count": len(words),
            "lines": lines,
        }

    @staticmethod
    def quality_payload(quality: QualityMetrics) -> dict[str, Any]:
        return {
            "expected_lines": quality.expected_lines,
            "aligned_lines": quality.aligned_lines,
            "expected_words": quality.expected_words,
            "aligned_words": quality.aligned_words,
            "interpolated_words": quality.interpolated_words,
            "missing_words": quality.missing_words,
            "unsupported_words": quality.unsupported_words,
            "total_chunks": quality.total_chunks,
            "successful_chunks": quality.successful_chunks,
            "low_confidence_chunks": quality.low_confidence_chunks,
            "failed_chunks": quality.failed_chunks,
            "mean_alignment_score": quality.mean_alignment_score,
            "p10_alignment_score": quality.p10_alignment_score,
            "minimum_alignment_score": quality.minimum_alignment_score,
            "low_score_word_percent": quality.low_score_word_percent,
            "interpolated_word_percent": quality.interpolated_word_percent,
            "failed_audio_percent": quality.failed_audio_percent,
            "median_anchor_shift_ms": quality.median_anchor_shift_ms,
            "p90_anchor_shift_ms": quality.p90_anchor_shift_ms,
            "max_anchor_shift_ms": quality.max_anchor_shift_ms,
            "continuity_repairs": quality.continuity_repairs,
            "max_continuity_repair_ms": quality.max_continuity_repair_ms,
            "continuity_violation_count": quality.continuity_violation_count,
            "continuity_first_violation": quality.continuity_first_violation,
            "reasons": quality.reasons,
        }

    def build_phase2(
        self,
        *,
        run_id: int,
        package_id: str,
        input_package: dict[str, Any],
        model: dict[str, Any],
        audio: dict[str, Any],
        vocals: dict[str, Any],
        lyrics: dict[str, Any],
        normalization: dict[str, Any],
        alignment: dict[str, Any],
        quality: QualityMetrics,
        chunks: list[dict[str, Any]],
        instrumental_sections: list[dict[str, Any]],
        outputs: dict[str, Any],
        pipeline_version: str,
        schema_version: int,
        normalizer_version: str,
        elapsed_seconds: float,
    ) -> dict[str, Any]:
        return {
            "schema_version": schema_version,
            "pipeline_version": pipeline_version,
            "run_id": run_id,
            "package_id": package_id,
            "status": "finished",
            "quality_status": quality.reasons and ("needs_review" if any(r in quality.reasons for r in ("FINAL_TIMING_REPAIR", "GLOBAL_TIMESTAMP_ORDER_INVALID", "LYRIC_CROSSES_BLANK_MARKER")) else None) or None,
            "input": input_package,
            "model": model,
            "audio": audio,
            "vocal_isolation": vocals,
            "lyrics": lyrics,
            "normalization": normalization,
            "alignment": alignment,
            "quality": self.quality_payload(quality),
            "chunks": chunks,
            "instrumental_sections": instrumental_sections,
            "outputs": outputs,
            "runtime": {"elapsed_seconds": round(elapsed_seconds, 3)},
            "hash_convention": {
                "final_json_sha256": "canonical JSON content hash with this field excluded; actual file byte hash is stored in phase2.db"
            },
        }
