from __future__ import annotations

import math

from .types import AlignedWord, SourceWord, TokenRef, TokenSpan


class WordBuilder:
    def build(self, words: list[SourceWord], token_refs: list[TokenRef], spans: list[TokenSpan],
              audio_origin_ms: int, stride_ms: float, frame_limit_ms: int,
              chunk_id: int | None = None) -> list[AlignedWord]:
        spans_by_token = {span.token_index: span for span in spans}
        result: list[AlignedWord] = []
        for word in words:
            if word.token_start is None or word.token_end is None or word.token_start == word.token_end:
                result.append(AlignedWord(
                    word.line_index, word.word_index, word.original, word.normalized,
                    None, None, None, source="missing", chunk_id=chunk_id,
                    reason="token_unsupported_or_empty", input_supported=False,
                ))
                continue

            word_spans = [
                spans_by_token[i]
                for i in range(word.token_start, word.token_end)
                if i in spans_by_token
            ]
            if not word_spans:
                result.append(AlignedWord(
                    word.line_index, word.word_index, word.original, word.normalized,
                    None, None, None, source="missing", chunk_id=chunk_id,
                    reason="no_token_span", input_supported=word.supported,
                ))
                continue

            raw_start = audio_origin_ms + int(round(min(s.start_frame for s in word_spans) * stride_ms))
            raw_end = audio_origin_ms + int(round(max(s.end_frame for s in word_spans) * stride_ms))
            limit = audio_origin_ms + frame_limit_ms
            if raw_start >= limit:
                result.append(AlignedWord(
                    word.line_index, word.word_index, word.original, word.normalized,
                    None, None, None, source="missing", chunk_id=chunk_id,
                    reason="token_span_outside_audio_limit", input_supported=word.supported,
                ))
                continue
            start = max(audio_origin_ms, raw_start)
            end = min(raw_end, limit)
            if end <= start:
                result.append(AlignedWord(
                    word.line_index, word.word_index, word.original, word.normalized,
                    None, None, None, source="missing", chunk_id=chunk_id,
                    reason="token_span_collapsed_at_audio_limit", input_supported=word.supported,
                ))
                continue
            scores = [s.geo_prob for s in word_spans if math.isfinite(s.geo_prob)]
            score = float(sum(scores) / len(scores)) if scores else None
            result.append(AlignedWord(
                word.line_index, word.word_index, word.original, word.normalized,
                start, end, score, source="aligned", chunk_id=chunk_id,
                input_supported=word.supported,
            ))
        return result
