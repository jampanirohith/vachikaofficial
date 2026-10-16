"""MMS lyric-anchor based YouTube/recording timeline synchronization.

The LRCLIB transcript is the anchor reference. MMS/CTC searches it in the selected
YouTube audio before the main alignment runs; after the acquired reference audio has
word-level timestamps, the corresponding words are compared to derive the offset.
No waveform/energy-envelope fallback is used by this module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from src.common.hashing import sha256_file, stable_json_hash
from src.common.model_cache import configure_model_cache
from src.common.utils import run
from src.phase2core.ctc_aligner import CTCForcedAligner
from src.phase2core.lrc_reader import parse_lrc
from src.phase2core.mms_model import MMSModel
from src.phase2core.telugu_normalizer import NormalizationConfig, TeluguNormalizer
from src.phase2core.tokenizer import ReferenceTokenizer
from src.phase2core.types import SourceWord
from src.phase2core.word_builder import WordBuilder

METHOD_VERSION = "mms-lrclib-dual-audio-offset-v3"


def _normalizer(cfg) -> TeluguNormalizer:
    raw = dict(cfg.get("normalization", {}) or {})
    valid = {k: raw[k] for k in ("version", "keep_latin", "keep_digits", "convert_numbers_to_telugu", "expand_abbreviations", "remove_punctuation") if k in raw}
    normal_config = NormalizationConfig(**valid)
    return TeluguNormalizer(normal_config, raw.get("abbreviations") or {})


def _anchor_words(cfg, lrc_path: Path, max_words: int) -> list[SourceWord]:
    doc = parse_lrc(Path(lrc_path))
    _normalizer(cfg).apply(doc.lines)
    words: list[SourceWord] = []
    for line in doc.lyric_lines:
        for word in line.words:
            if not word.normalized.strip():
                continue
            words.append(word)
            if len(words) >= max_words:
                return words
    return words


def _ensure_16k_mono(cfg, wav_path: Path, output_path: Path) -> Path:
    """Convert yt-dlp's 8 kHz reference WAV once; MMS strictly requires 16 kHz."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = output_path.with_suffix(".manifest.json")
    source_sha = sha256_file(wav_path)
    if output_path.is_file() and output_path.stat().st_size > 10000 and manifest.is_file():
        try:
            item = json.loads(manifest.read_text(encoding="utf-8"))
            if item.get("source_sha256") == source_sha and item.get("output_sha256") == sha256_file(output_path):
                return output_path
        except Exception:
            pass
    tmp = output_path.with_name(f".{output_path.stem}.tmp-{os.getpid()}.wav")
    try:
        ffmpeg = cfg.get("ffmpeg_location") or "ffmpeg"
        run([str(ffmpeg), "-y", "-v", "error", "-i", str(wav_path), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-f", "wav", str(tmp)], timeout=900)
        if not tmp.is_file() or tmp.stat().st_size < 10000:
            raise RuntimeError("MMS_OFFSET_AUDIO_CONVERSION_FAILED: invalid 16 kHz WAV")
        os.replace(tmp, output_path)
    finally:
        tmp.unlink(missing_ok=True)
    manifest.write_text(json.dumps({
        "schema_version": 1, "source_path": str(wav_path), "source_sha256": source_sha,
        "output_path": str(output_path), "output_sha256": sha256_file(output_path),
        "sample_rate": 16000, "channels": 1,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def _model_for(cfg) -> MMSModel:
    root = cfg.path("paths.models_dir") or (cfg.root / "models")
    configure_model_cache(root)
    model = MMSModel(
        model_name=str(cfg.get("models.mms_model", "facebook/mms-1b-all")),
        language=str(cfg.get("models.language_iso3", "tel")),
        device=str(cfg.get("runtime.device", "cuda")),
        fallback_device=str(cfg.get("runtime.fallback_device", "cpu")),
        cache_dir=root,
        allow_download=bool(cfg.get("models.allow_download", True)),
        revision=cfg.get("models.mms_revision"),
        require_cuda=bool(cfg.get("runtime.require_cuda", False)),
        allow_cpu_fallback=bool(cfg.get("runtime.allow_cpu_fallback", True)),
    )
    model.load()
    return model


def _choose_anchor_candidate(candidates: list[dict[str, Any]], min_relative_score: float):
    """Prefer the earliest plausible occurrence without accepting a weak forced fit."""
    if not candidates:
        raise RuntimeError("MMS_OFFSET_ANCHOR_NOT_FOUND: no acceptable MMS lyric-anchor candidates")
    if not 0.0 < float(min_relative_score) <= 1.0:
        raise RuntimeError("MMS_OFFSET_CONFIG_INVALID: anchor_min_relative_score must be in (0, 1]")
    best_score = max(float(c.get("score") or 0.0) for c in candidates)
    score_floor = best_score * float(min_relative_score)
    strong = [c for c in candidates if float(c.get("score") or 0.0) >= score_floor]
    strong.sort(key=lambda c: (int(c.get("phrase_start_ms") or 0), -float(c.get("score") or 0.0)))
    return strong[0], strong, best_score, score_floor


def find_lyric_anchor(cfg, lrc_path: Path, audio_path: Path, work_dir: Path, source_label: str) -> dict[str, Any]:
    """Find the LRCLIB lyric phrase in an audio timeline using MMS/CTC.

    The same transcript and model are applied independently to the acquired YT
    Music reference audio and the selected video's audio. This makes it possible
    to calculate the offset before Demucs or full-song word-LRC generation.
    ``source_label`` is ``reference`` or ``youtube`` and determines only cache
    names/metadata, not the algorithm.
    """
    lrc_path = Path(lrc_path)
    audio_path = Path(audio_path)
    work_dir = Path(work_dir)
    if source_label not in {"reference", "youtube"}:
        raise ValueError("MMS_OFFSET_SOURCE_INVALID: source_label must be 'reference' or 'youtube'")
    out_dir = work_dir / ("reference_audio" if source_label == "reference" else "youtube")
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / "lyric_anchor.json"
    audio_sha = sha256_file(audio_path)
    cache_fp = stable_json_hash({
        "method": METHOD_VERSION,
        "source_label": source_label,
        "lrc_sha256": sha256_file(lrc_path),
        "audio_sha256": audio_sha,
        "model": cfg.get("models.mms_model", "facebook/mms-1b-all"),
        "revision": cfg.get("models.mms_revision"),
        "language": cfg.get("models.language_iso3", "tel"),
        "word_count": int(cfg.get("youtube_offset.anchor_word_count", 10)),
        "window_seconds": int(cfg.get("youtube_offset.search_window_seconds", 28)),
        "stride_seconds": int(cfg.get("youtube_offset.search_stride_seconds", 12)),
        "max_search_seconds": int(cfg.get("youtube_offset.search_max_seconds", 300)),
        "min_confidence": float(cfg.get("youtube_offset.anchor_min_word_confidence", 0.003)),
        "min_relative_score": float(cfg.get("youtube_offset.anchor_min_relative_score", 0.75)),
        "min_matched_words": int(cfg.get("youtube_offset.anchor_min_matched_words", 5)),
        # Candidate selection and normalization affect the inferred anchor;
        # both must be part of cache identity even if LRC/audio bytes stay unchanged.
        "normalization": cfg.get("normalization", {}),
    })
    if result_path.is_file():
        try:
            cached = json.loads(result_path.read_text(encoding="utf-8"))
            if (cached.get("fingerprint") == cache_fp and cached.get("accepted")
                    and cached.get("audio_sha256") == audio_sha
                    and cached.get("source_label") == source_label):
                return cached
        except Exception:
            pass

    max_words = max(5, min(24, int(cfg.get("youtube_offset.anchor_word_count", 10))))
    words = _anchor_words(cfg, lrc_path, max_words)
    minimum_words = int(cfg.get("youtube_offset.anchor_min_matched_words", 5))
    if len(words) < minimum_words:
        raise RuntimeError(f"MMS_OFFSET_ANCHOR_TOO_SHORT: LRCLIB only yielded {len(words)} lyric words for the Telugu MMS anchor")

    model = _model_for(cfg)
    try:
        token_refs = ReferenceTokenizer().build(words, model.tokenizer)
        supported = [w for w in words if w.token_start is not None and w.token_end is not None and w.token_end > w.token_start]
        min_matched = max(3, minimum_words)
        if len(supported) < min_matched or not token_refs:
            raise RuntimeError(f"MMS_OFFSET_ANCHOR_UNSUPPORTED: only {len(supported)} anchor words are supported by the MMS Telugu tokenizer")

        wav16 = _ensure_16k_mono(cfg, audio_path, out_dir / "anchor_audio_16k.wav")
        audio, sr = sf.read(wav16, dtype="float32", always_2d=True)
        if sr != 16000 or not len(audio):
            raise RuntimeError(f"MMS_OFFSET_AUDIO_INVALID: expected non-empty 16 kHz audio, got {sr} Hz")
        signal = np.mean(audio, axis=1, dtype=np.float32)
        duration_ms = int(round(len(signal) * 1000 / sr))
        window_ms = max(12000, int(cfg.get("youtube_offset.search_window_seconds", 28)) * 1000)
        stride_ms = max(3000, int(cfg.get("youtube_offset.search_stride_seconds", 12)) * 1000)
        max_search_ms = min(duration_ms, max(window_ms, int(cfg.get("youtube_offset.search_max_seconds", 300)) * 1000))
        min_confidence = float(cfg.get("youtube_offset.anchor_min_word_confidence", 0.003))
        aligner = CTCForcedAligner()
        word_builder = WordBuilder()
        win_dir = out_dir / "anchor_windows"
        win_dir.mkdir(parents=True, exist_ok=True)
        candidates: list[dict[str, Any]] = []
        starts = list(range(0, max(1, max_search_ms - window_ms + 1), stride_ms))
        last_start = max(0, max_search_ms - window_ms)
        if not starts or starts[-1] != last_start:
            starts.append(last_start)
        for start_ms in sorted(set(starts)):
            end_ms = min(max_search_ms, start_ms + window_ms)
            if end_ms - start_ms < 6000:
                continue
            first = int(start_ms * sr / 1000)
            last = int(end_ms * sr / 1000)
            chunk = signal[first:last]
            chunk_path = win_dir / f"window_{start_ms:09d}_{end_ms:09d}.wav"
            sf.write(chunk_path, chunk, sr, subtype="PCM_16")
            try:
                emission = model.emissions(chunk_path)
                aligned = aligner.align(emission.log_probs, token_refs, model.blank_token_id)
                built = word_builder.build(
                    words, token_refs, aligned.spans, start_ms, emission.stride_ms,
                    end_ms - start_ms, chunk_id=None,
                )
                matched = []
                for item in built:
                    if item.start_ms is None or item.end_ms is None:
                        continue
                    matched.append({
                        "line_index": int(item.line_index), "word_index": int(item.word_index),
                        "original_word": item.original, "normalized_word": item.normalized,
                        "start_ms": int(item.start_ms), "end_ms": int(item.end_ms),
                        "confidence": float(item.score or 0.0), "source": item.source,
                    })
                confidences = [x["confidence"] for x in matched]
                monotonic = all(matched[i]["start_ms"] <= matched[i + 1]["start_ms"] for i in range(len(matched) - 1))
                phrase_span = (matched[-1]["end_ms"] - matched[0]["start_ms"]) if matched else 0
                duration_ok = bool(matched) and phrase_span <= min(window_ms, max(18000, len(matched) * 1800))
                mean_conf = float(np.mean(confidences)) if confidences else 0.0
                coverage = len(matched) / max(1, len(words))
                # Confidence and coverage are kept separate in the metadata so low
                # quality forced alignments are observable rather than silently trusted.
                candidate_score = mean_conf * coverage
                candidate = {
                    "window_start_ms": start_ms, "window_end_ms": end_ms,
                    "phrase_start_ms": matched[0]["start_ms"] if matched else None,
                    "phrase_end_ms": matched[-1]["end_ms"] if matched else None,
                    "mean_word_confidence": mean_conf, "coverage": coverage,
                    "score": candidate_score, "matched_word_count": len(matched),
                    "monotonic": monotonic, "duration_plausible": duration_ok,
                    "words": matched,
                }
                if len(matched) >= min_matched and mean_conf >= min_confidence and monotonic and duration_ok:
                    candidates.append(candidate)
            except (ValueError, RuntimeError):
                # A transcript forced onto an unrelated window should be rejected;
                # keep searching other windows for a coherent match.
                continue
            finally:
                chunk_path.unlink(missing_ok=True)

        if not candidates:
            raise RuntimeError(
                f"MMS_OFFSET_ANCHOR_NOT_FOUND: MMS could not confidently locate the LRCLIB lyric phrase in {source_label} audio. "
                "No energy-envelope fallback is enabled; review the selected source or increase youtube_offset.search_max_seconds."
            )
        # Forced alignment can fit text to unrelated audio, so accepting the first
        # minimally-passing window is not enough. Keep only candidates close to the
        # strongest acoustic/token-confidence score, then choose the earliest of
        # those. This preserves the first-occurrence rule while excluding weak early
        # false positives. The same rule is applied independently to both audios.
        min_relative_score = float(cfg.get("youtube_offset.anchor_min_relative_score", 0.75))
        chosen, strong_candidates, best_score, score_floor = _choose_anchor_candidate(candidates, min_relative_score)
        payload = {
            "schema_version": 1, "method_version": METHOD_VERSION, "fingerprint": cache_fp,
            "accepted": True, "source_label": source_label, "audio_path": str(audio_path),
            "audio_sha256": audio_sha, "lrc_path": str(lrc_path), "lrc_sha256": sha256_file(lrc_path),
            "model": cfg.get("models.mms_model", "facebook/mms-1b-all"),
            "model_revision": cfg.get("models.mms_revision"), "language": cfg.get("models.language_iso3", "tel"),
            "search_duration_ms": max_search_ms, "audio_duration_ms": duration_ms,
            "window_seconds": window_ms / 1000, "stride_seconds": stride_ms / 1000,
            "chosen": chosen, "candidate_count": len(candidates),
            "strong_candidate_count": len(strong_candidates),
            "best_candidate_score": round(best_score, 8),
            "selection_policy": {"name": "earliest_candidate_within_relative_best_score", "minimum_relative_score": min_relative_score, "score_floor": round(score_floor, 8)},
            "candidates_summary": [{k: c[k] for k in ("phrase_start_ms", "phrase_end_ms", "mean_word_confidence", "coverage", "score", "matched_word_count")} for c in candidates[:50]],
        }
        if source_label == "youtube":
            payload["youtube_audio_path"] = str(audio_path)
            payload["youtube_audio_sha256"] = audio_sha
        else:
            payload["reference_audio_path"] = str(audio_path)
            payload["reference_audio_sha256"] = audio_sha
        tmp = result_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, result_path)
        return payload
    finally:
        del model


def find_youtube_lyric_anchor(cfg, lrc_path: Path, reference_wav: Path, work_dir: Path) -> dict[str, Any]:
    """Backward-compatible wrapper for the selected YouTube audio anchor."""
    return find_lyric_anchor(cfg, lrc_path, reference_wav, work_dir, "youtube")


def find_reference_lyric_anchor(cfg, lrc_path: Path, reference_audio: Path, work_dir: Path) -> dict[str, Any]:
    """Locate the same LRCLIB lyric phrase in the acquired YT Music reference audio."""
    return find_lyric_anchor(cfg, lrc_path, reference_audio, work_dir, "reference")


def calculate_offset_from_anchors(youtube_anchor: dict[str, Any], reference_anchor: dict[str, Any], cfg=None) -> dict[str, Any]:
    """Calculate YouTube-minus-reference offset using the same LRCLIB words on both audios."""
    youtube_words = ((youtube_anchor or {}).get("chosen") or {}).get("words") or []
    reference_words = ((reference_anchor or {}).get("chosen") or {}).get("words") or []
    reference_by_key: dict[tuple[int, int], dict[str, Any]] = {}
    for word in reference_words:
        try:
            key = (int(word.get("line_index")), int(word.get("word_index")))
        except (TypeError, ValueError):
            continue
        if word.get("start_ms") is not None and word.get("end_ms") is not None:
            reference_by_key[key] = word
    differences: list[dict[str, Any]] = []
    min_conf = float(cfg.get("youtube_offset.anchor_min_word_confidence", 0.003)) if cfg is not None else 0.003
    for yword in youtube_words:
        try:
            key = (int(yword["line_index"]), int(yword["word_index"]))
        except (KeyError, TypeError, ValueError):
            continue
        rword = reference_by_key.get(key)
        if not rword:
            continue
        yconf = float(yword.get("confidence") or 0.0)
        rconf = float(rword.get("confidence") or 0.0)
        if yconf < min_conf or rconf < min_conf:
            continue
        ys, rs = int(yword["start_ms"]), int(rword["start_ms"])
        differences.append({
            "line_index": key[0], "word_index": key[1],
            "word": str(yword.get("original_word") or rword.get("original_word") or ""),
            "youtube_start_ms": ys, "reference_start_ms": rs, "offset_ms": ys - rs,
            "youtube_confidence": yconf, "reference_confidence": rconf,
            "reference_end_ms": int(rword["end_ms"]), "youtube_end_ms": int(yword["end_ms"]),
        })
    min_matches = max(3, int(cfg.get("youtube_offset.offset_min_matched_words", 5)) if cfg is not None else 5)
    max_mad = max(100, int(cfg.get("youtube_offset.offset_max_mad_ms", 800)) if cfg is not None else 800)
    if len(differences) < min_matches:
        raise RuntimeError(f"MMS_OFFSET_INSUFFICIENT_MATCHES: matched {len(differences)} LRCLIB words across both audio timelines; require at least {min_matches}")
    values = np.asarray([x["offset_ms"] for x in differences], dtype=np.float64)
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    outlier_limit = max(max_mad, 3.0 * mad)
    inliers = [x for x in differences if abs(float(x["offset_ms"]) - median) <= outlier_limit]
    if len(inliers) < min_matches:
        raise RuntimeError(f"MMS_OFFSET_UNSTABLE: only {len(inliers)} of {len(differences)} LRCLIB word offsets agree")
    values2 = np.asarray([x["offset_ms"] for x in inliers], dtype=np.float64)
    final = int(round(float(np.median(values2))))
    final_mad = float(np.median(np.abs(values2 - final)))
    spread = int(round(float(np.percentile(values2, 90) - np.percentile(values2, 10)))) if len(values2) > 1 else 0
    if final_mad > max_mad:
        raise RuntimeError(f"MMS_OFFSET_UNSTABLE: median absolute deviation {final_mad:.1f} ms exceeds {max_mad} ms")
    mean_conf = float(np.mean([min(x["youtube_confidence"], x["reference_confidence"]) for x in inliers]))
    consistency = max(0.0, 1.0 - final_mad / max(1.0, max_mad))
    confidence = max(0.0, min(1.0, mean_conf * 10.0)) * consistency * min(1.0, len(inliers) / max(min_matches, len(youtube_words)))
    return {
        "accepted": True, "candidate_offset_ms": final,
        "candidate_offset_seconds": final / 1000.0,
        "method": "mms_lrclib_dual_audio_word_timestamp_median",
        "method_version": METHOD_VERSION,
        "total_score": round(confidence, 6), "mean_word_confidence": round(mean_conf, 6),
        "matched_word_count": len(inliers), "candidate_word_count": len(youtube_words),
        "median_absolute_deviation_ms": round(final_mad, 3), "p90_p10_spread_ms": spread,
        "matched_words": inliers, "rejection_reasons": [], "energy_match_used": False,
        "reference_anchor_start_ms": ((reference_anchor.get("chosen") or {}).get("phrase_start_ms")),
        "youtube_anchor_start_ms": ((youtube_anchor.get("chosen") or {}).get("phrase_start_ms")),
        "reference_audio_sha256": reference_anchor.get("audio_sha256"),
        "youtube_audio_sha256": youtube_anchor.get("audio_sha256"),
        "lyrics_source": "LRCLIB", "timeline": "YT Music reference audio -> selected YouTube video audio",
    }
