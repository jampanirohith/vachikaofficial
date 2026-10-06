from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from .types import TokenRef, TokenSpan


@dataclass(slots=True)
class CTCAlignmentResult:
    spans: list[TokenSpan]
    score: float
    end_state: int


class CTCForcedAligner:
    """Reference-driven CTC Viterbi alignment with correct repeated-label rules."""

    def align(self, log_probs: np.ndarray, reference: list[TokenRef], blank_id: int) -> CTCAlignmentResult:
        if log_probs.ndim != 2:
            raise ValueError("CTC_ALIGNMENT_FAILED: log_probs must have shape [frames, classes]")
        if not np.isfinite(log_probs).all():
            raise ValueError("CTC_ALIGNMENT_FAILED: emissions contain NaN/INF")
        if not reference:
            raise ValueError("CTC_ALIGNMENT_FAILED: empty reference token sequence")

        t_count, class_count = log_probs.shape
        token_ids = [int(x.token_id) for x in reference]
        if blank_id < 0 or blank_id >= class_count:
            raise ValueError("CTC_ALIGNMENT_FAILED: invalid blank token id")
        if any(x < 0 or x >= class_count for x in token_ids):
            raise ValueError("CTC_ALIGNMENT_FAILED: reference token outside model vocabulary")

        # Expanded sequence: blank, label_0, blank, label_1, ..., blank.
        states = 2 * len(token_ids) + 1
        labels = np.full(states, blank_id, dtype=np.int64)
        labels[1::2] = np.asarray(token_ids, dtype=np.int64)

        # A valid CTC path needs at least one emission frame per reference token.
        if t_count < len(token_ids):
            raise ValueError(
                f"CTC_ALIGNMENT_FAILED: {t_count} frames are insufficient for {len(token_ids)} reference tokens"
            )

        neg_inf = -np.inf
        dp = np.full((states,), neg_inf, dtype=np.float64)
        back = np.full((t_count, states), -1, dtype=np.int32)

        dp[0] = float(log_probs[0, blank_id])
        if states > 1:
            dp[1] = float(log_probs[0, labels[1]])
            back[0, 1] = 0

        for t in range(1, t_count):
            previous = dp.copy()
            for s in range(states):
                best_value = previous[s]
                best_prev = s
                if s > 0 and previous[s - 1] > best_value:
                    best_value = previous[s - 1]
                    best_prev = s - 1
                # CTC skip is forbidden when s and s-2 have the same non-blank label.
                if (
                    s >= 2
                    and labels[s] != blank_id
                    and labels[s] != labels[s - 2]
                    and previous[s - 2] > best_value
                ):
                    best_value = previous[s - 2]
                    best_prev = s - 2
                if math.isfinite(float(best_value)):
                    dp[s] = best_value + float(log_probs[t, labels[s]])
                    back[t, s] = best_prev
                else:
                    dp[s] = neg_inf

        candidates = [states - 1]
        if states >= 2:
            candidates.append(states - 2)
        end_state = max(candidates, key=lambda s: float(dp[s]))
        final_score = float(dp[end_state])
        if not math.isfinite(final_score):
            raise ValueError("CTC_ALIGNMENT_FAILED: no valid CTC path")

        path = np.full(t_count, -1, dtype=np.int32)
        state = end_state
        for t in range(t_count - 1, -1, -1):
            path[t] = state
            if t > 0:
                state = int(back[t, state])
                if state < 0:
                    raise ValueError("CTC_ALIGNMENT_FAILED: broken Viterbi backtrace")

        spans: list[TokenSpan] = []
        for token_pos, token_id in enumerate(token_ids):
            state_id = 2 * token_pos + 1
            frame_ids = np.flatnonzero(path == state_id)
            if len(frame_ids) == 0:
                raise ValueError(f"CTC_ALIGNMENT_FAILED: token {token_pos} received no frames")
            start_frame = int(frame_ids[0])
            end_frame = int(frame_ids[-1] + 1)
            values = log_probs[start_frame:end_frame, token_id]
            mean_log_prob = float(np.mean(values))
            geo_prob = float(np.exp(np.clip(mean_log_prob, -80.0, 0.0)))
            spans.append(TokenSpan(
                token_index=reference[token_pos].token_index,
                token_id=token_id,
                start_frame=start_frame,
                end_frame=end_frame,
                mean_log_prob=mean_log_prob,
                geo_prob=geo_prob,
            ))

        return CTCAlignmentResult(spans=spans, score=final_score, end_state=end_state)
