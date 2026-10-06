from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .activity_detector import ActivityDetector
from ..model_cache import configure_model_cache
from .audio import AudioManager
from .chunker import Chunker
from .ctc_aligner import CTCForcedAligner
from .db import Database
from .demucs_isolator import DemucsIsolator
from .embedder import MP3Embedder
from .json_manager import JSONManager
from .json_updater import JSONUpdater
from .lrc_generator import LRCGenerator
from .lrc_reader import blank_intervals, parse_lrc
from .merger import Merger
from .mms_model import MMSModel
from .scanner import Scanner
from .telugu_normalizer import NormalizationConfig, TeluguNormalizer
from .tokenizer import ReferenceTokenizer
from .types import AlignedWord, ChunkAlignment, ChunkSpec, InputPackage, LyricDocument, QualityMetrics
from .utils import (
    atomic_copy,
    atomic_write_bytes,
    interval_overlap_ms,
    json_dump_file,
    merge_intervals,
    now_iso,
    percentile,
    sha256_file,
)
from .validator import Validator
from .word_builder import WordBuilder


class Pipeline:
    def __init__(self, config, db: Database, allow_cpu_fallback: bool = True):
        self.config = config
        self.db = db
        self.allow_cpu_fallback = bool(allow_cpu_fallback)
        self.require_cuda = False
        self.json_manager = JSONManager()
        self.scanner = Scanner(db, self.json_manager)
        self.audio = AudioManager(int(config.get("audio.sample_rate", 16000)))
        normalization_section = dict(config.get("normalization", {}))
        abbreviations = normalization_section.pop("abbreviations", {})
        ncfg = NormalizationConfig(**normalization_section)
        self.normalizer = TeluguNormalizer(ncfg, abbreviations)
        self.validator = Validator()
        self.merger = Merger(int(config.get("alignment.max_global_repair_ms", 1500)))
        self.lrc_generator = LRCGenerator()
        self.embedder = MP3Embedder()
        self.json_updater = JSONUpdater(self.json_manager)
        self.tokenizer = ReferenceTokenizer()
        self.word_builder = WordBuilder()
        self.aligner = CTCForcedAligner()
        self.model: MMSModel | None = None
        configure_model_cache(self.config.path('paths.models_dir'))
        self.run_id: int | None = None

    @property
    def project_version(self) -> str:
        return str(self.config.get("project.pipeline_version", "1.0.0"))

    def start_run(self) -> int:
        env: dict[str, Any] = {"python": os.sys.version}
        for package in ["torch", "transformers", "demucs", "silero-vad", "mutagen", "numpy", "soundfile", "librosa"]:
            try:
                env[package] = importlib.metadata.version(package)
            except Exception:
                env[package] = None
        self.run_id = self.db.create_run({
            "pipeline_version": self.project_version,
            "config_hash": self.config.config_hash,
            "model_name": self.config.get("models.mms_model", "facebook/mms-1b-all"),
            "model_revision": self.config.get("models.mms_revision"),
            "language_code": self.config.get("models.language_iso3", "tel"),
            "normalizer_version": self.normalizer.config.version,
            "environment_json": json.dumps(env, ensure_ascii=False, sort_keys=True, default=str),
        })
        return self.run_id

    def finish_run(self) -> None:
        if self.run_id is not None:
            self.db.finish_run(self.run_id)

    def _song_key(self, song_id: int, mp3_sha256: str) -> str:
        return f"{song_id}_{mp3_sha256[:12]}"

    def _setup_model(self) -> MMSModel:
        if self.model is None:
            self.model = MMSModel(
                model_name=str(self.config.get("models.mms_model", "facebook/mms-1b-all")),
                language=str(self.config.get("models.language_iso3", "tel")),
                device=str(self.config.get("runtime.device", "cpu")),
                fallback_device=str(self.config.get("runtime.fallback_device", "cpu")),
                cache_dir=self.config.path("paths.models_dir"),
                allow_download=bool(self.config.get("models.allow_download", True)),
                revision=self.config.get("models.mms_revision"),
                require_cuda=self.require_cuda,
                allow_cpu_fallback=True,
            )
            self.model.load()
            if bool(self.config.get("runtime.print_device_banner", True)):
                print(f"RUNTIME | MMS device={self.model.device} | requested={self.model.requested_device} | cuda_required={self.require_cuda}")
        return self.model

    def ensure_runtime_ready(self) -> None:
        """Validate runtime policy; CUDA is preferred and CPU is the automatic fallback."""
        requested = str(self.config.get("runtime.device", "cuda")).lower()
        require_cuda = bool(self.config.get("runtime.require_cuda", False))
        allow_fallback = bool(self.config.get("runtime.allow_cpu_fallback", True))
        if requested != "cuda" or not require_cuda or allow_fallback:
            return
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("CUDA_REQUIRED: PyTorch is not installed") from exc
        if not bool(torch.cuda.is_available()):
            raise RuntimeError(
                "CUDA_REQUIRED: this Phase 2 configuration requires NVIDIA CUDA, but the installed PyTorch "
                "build does not expose CUDA. Install with scripts/install_windows_cuda.ps1 and verify with "
                "python main.py --doctor."
            )

    def scan(self) -> tuple[list[InputPackage], list[Any]]:
        original_dir = self.config.path("paths.original_dir")
        original_dir.mkdir(parents=True, exist_ok=True)
        packages = self.scanner.discover(original_dir)
        for issue in self.scanner.issues:
            self.db.log(None, "scanner", issue.severity.upper(), issue.issue, metadata={"basename": issue.basename, "path": issue.path})
        return packages, list(self.scanner.issues)

    def _is_same_identity(self, row: Any, package: InputPackage, final_json: Path) -> bool:
        output_dir = self.config.path("paths.final_dir")
        final_mp3 = output_dir / package.mp3_path.name
        final_lrc = output_dir / package.lrc_path.name
        if row["pipeline_status"] != "finished":
            return False
        if not (final_mp3.exists() and final_lrc.exists() and final_json.exists()):
            return False
        try:
            data = self.json_manager.load(final_json)
            phase2 = data.get("phase2", {})
            inp = phase2.get("input", {}) if isinstance(phase2, dict) else {}
            model = phase2.get("model", {}) if isinstance(phase2, dict) else {}
            outputs = phase2.get("outputs", {}) if isinstance(phase2, dict) else {}
            source_json = self.json_manager.load(package.json_path)
            db_json_hash = row["final_json_sha256"]
            return (
                inp.get("mp3_sha256") == row["original_mp3_sha256"]
                and inp.get("lrc_sha256") == row["original_lrc_sha256"]
                and inp.get("json_sha256") == row["original_json_sha256"]
                # Finished outputs are reusable when their source package identity and persisted
                # output integrity match. Use --force to intentionally rebuild them after a code/model change.
                and outputs.get("mp3_sha256") == sha256_file(final_mp3)
                and outputs.get("lrc_sha256") == sha256_file(final_lrc)
                and (db_json_hash is None or db_json_hash == sha256_file(final_json))
                and self.json_manager.validate_content_hash(data)
                and self.json_manager.non_phase2_equal(source_json, data)
                and final_json.stat().st_size > 0
            )
        except Exception:
            return False

    def _prepare_work(self, song_id: int, mp3_hash: str) -> tuple[Path, Path, Path, Path, Path]:
        work = self.config.path("paths.temp_dir") / self._song_key(song_id, mp3_hash)
        audio_dir = work / "audio"
        chunks_dir = work / "chunks"
        aligned_dir = work / "aligned"
        stage_dir = work / "stage_final"
        for directory in (audio_dir, chunks_dir, aligned_dir, stage_dir):
            directory.mkdir(parents=True, exist_ok=True)
        return work, audio_dir, chunks_dir, aligned_dir, stage_dir

    @staticmethod
    def _chunk_words(document: LyricDocument, line_start: int, line_end: int):
        out = []
        for line in document.lyric_lines:
            if line.line_index < line_start or line.line_index > line_end:
                continue
            out.extend(line.words)
        return out

    def _chunk_from_row(self, row: Any, document: LyricDocument) -> ChunkSpec:
        words = self._chunk_words(document, int(row["line_start_index"]), int(row["line_end_index"]))
        return ChunkSpec(
            chunk_index=int(row["chunk_index"]),
            logical_start_ms=int(row["logical_start_ms"]),
            logical_end_ms=int(row["logical_end_ms"]),
            audio_start_ms=int(row["audio_start_ms"]),
            audio_end_ms=int(row["audio_end_ms"]),
            line_start_index=int(row["line_start_index"]),
            line_end_index=int(row["line_end_index"]),
            text_content=str(row["text_content"]),
            normalized_text=str(row["normalized_text"]),
            words=words,
            audio_path=Path(row["audio_path"]) if row["audio_path"] else None,
            result_json_path=Path(row["result_json_path"]) if row["result_json_path"] else None,
            boundary_reason=str(row["boundary_reason"] or ""),
        )

    def _save_chunk_result(self, path: Path, result: ChunkAlignment) -> None:
        payload = {
            "chunk": {
                "chunk_index": result.chunk.chunk_index,
                "logical_start_ms": result.chunk.logical_start_ms,
                "logical_end_ms": result.chunk.logical_end_ms,
                "audio_start_ms": result.chunk.audio_start_ms,
                "audio_end_ms": result.chunk.audio_end_ms,
                "line_start_index": result.chunk.line_start_index,
                "line_end_index": result.chunk.line_end_index,
                "text_content": result.chunk.text_content,
                "normalized_text": result.chunk.normalized_text,
                "boundary_reason": result.chunk.boundary_reason,
            },
            "status": result.status,
            "error_code": result.error_code,
            "error_remark": result.error_remark,
            "mean_score": result.mean_score,
            "p10_score": result.p10_score,
            "minimum_score": result.minimum_score,
            "frame_count": result.frame_count,
            "token_count": result.token_count,
            "stride_ms": result.stride_ms,
            "input_audio": result.input_audio,
            "device": result.device,
            "words": [asdict(w) for w in result.words],
        }
        json_dump_file(path, payload)

    @staticmethod
    def _load_chunk_result(path: Path, chunk: ChunkSpec) -> ChunkAlignment:
        data = json.loads(path.read_text(encoding="utf-8"))
        words = [AlignedWord(**row) for row in data.get("words", [])]
        return ChunkAlignment(
            chunk=chunk,
            words=words,
            mean_score=data.get("mean_score"),
            p10_score=data.get("p10_score"),
            minimum_score=data.get("minimum_score"),
            frame_count=int(data.get("frame_count", 0)),
            token_count=int(data.get("token_count", 0)),
            stride_ms=float(data.get("stride_ms", 0.0)),
            status=str(data.get("status", "failed")),
            error_code=data.get("error_code"),
            error_remark=data.get("error_remark"),
            input_audio=str(data.get("input_audio", "vocals")),
            device=data.get("device"),
        )

    def _build_and_store_chunks(self, song_id: int, document: LyricDocument, duration_ms: int,
                                source_16k: Path, chunks_dir: Path) -> list[ChunkSpec]:
        chunker = Chunker(
            int(round(float(self.config.get("chunking.min_seconds", 4.0)) * 1000)),
            int(round(float(self.config.get("chunking.target_seconds", 12.0)) * 1000)),
            int(round(float(self.config.get("chunking.max_seconds", 20.0)) * 1000)),
            int(self.config.get("chunking.context_before_ms", 500)),
            int(self.config.get("chunking.context_after_ms", 500)),
            int(self.config.get("chunking.pause_threshold_ms", 1500)),
            int(self.config.get("chunking.large_gap_threshold_ms", 3000)),
        )
        specs = chunker.build(document, duration_ms)
        self.db.clear_song_children(song_id)
        for spec in specs:
            audio_path = chunks_dir / f"chunk_{spec.chunk_index:03d}.wav"
            self.audio.slice_wav(source_16k, audio_path, spec.audio_start_ms, spec.audio_end_ms)
            result_path = chunks_dir / f"chunk_{spec.chunk_index:03d}.json"
            spec.audio_path = audio_path
            spec.result_json_path = result_path
            self.db.add_chunk(song_id, {
                "chunk_index": spec.chunk_index,
                "logical_start_ms": spec.logical_start_ms,
                "logical_end_ms": spec.logical_end_ms,
                "audio_start_ms": spec.audio_start_ms,
                "audio_end_ms": spec.audio_end_ms,
                "line_start_index": spec.line_start_index,
                "line_end_index": spec.line_end_index,
                "text_content": spec.text_content,
                "normalized_text": spec.normalized_text,
                "boundary_reason": spec.boundary_reason,
                "word_count": len(spec.words),
                "audio_path": str(audio_path),
                "result_json_path": str(result_path),
                "status": "pending",
            })
        return specs

    def _stats_for_scores(self, scores: list[float]) -> tuple[float | None, float | None, float | None]:
        if not scores:
            return None, None, None
        return sum(scores) / len(scores), percentile(scores, 10), min(scores)

    def _align_one_chunk(self, song_id: int, chunk_row: Any, chunk: ChunkSpec,
                         vocals_16k: Path, source_16k: Path, debug: bool = False,
                         strict_logical_window: bool = False,
                         strict_boundary_end_ms: int | None = None) -> ChunkAlignment:
        chunk_id = int(chunk_row["id"])
        attempt0 = int(chunk_row["attempt_count"] or 0)
        max_attempts = max(1, int(self.config.get("recovery.max_chunk_attempts", 2)))
        # Global continuity/boundary repair is a separate recovery layer. It must be
        # possible on a later process run even when the chunk's ordinary attempt budget
        # was already consumed before a final-output validation failure.
        repair_extra_attempts = max(0, int(self.config.get("alignment.global_retry_attempts_per_chunk", 2)))
        allowed_attempts = max_attempts + repair_extra_attempts if strict_logical_window else max_attempts
        low_threshold = float(self.config.get("alignment.chunk_mean_score_min", 0.40))
        fallback_original = bool(self.config.get("alignment.fallback_to_original_mix", True))
        base_context = int(self.config.get("alignment.retry_context_extra_ms", 500))
        best: ChunkAlignment | None = None
        last_attempt = attempt0

        model = self._setup_model()
        remaining_attempts = max(0, allowed_attempts - attempt0)
        if remaining_attempts <= 0:
            raise RuntimeError(f"ALIGNMENT_FAILED: chunk {chunk.chunk_index} exhausted retry budget")
        for retry_round in range(remaining_attempts):
            attempt = attempt0 + retry_round + 1
            last_attempt = attempt
            extra = retry_round * base_context
            if strict_logical_window:
                # Preserve some pre-chunk context for onset detection, but when a blank
                # marker is the violated boundary, make the marker a hard audio endpoint.
                effective_start = max(0, chunk.audio_start_ms - extra)
                default_end = chunk.logical_end_ms + int(self.config.get("chunking.context_after_ms", 500)) + extra
                if strict_boundary_end_ms is not None:
                    # Explicit source-LRC blank marker: no post-marker audio may be fed to
                    # the retry. This prevents the CTC path from placing lyric tokens in
                    # an explicitly lyric-free/instrumental interval.
                    default_end = int(strict_boundary_end_ms)
                effective_end = min(
                    self.db.get_song(song_id)["duration_ms"],
                    max(effective_start + 1, default_end),
                )
            else:
                effective_start = max(0, chunk.audio_start_ms - extra)
                effective_end = min(
                    self.db.get_song(song_id)["duration_ms"],
                    chunk.audio_end_ms + extra,
                )
            round_candidates: list[ChunkAlignment] = []

            for input_name, source_audio in [("vocals", vocals_16k), ("original", source_16k)]:
                if input_name == "original" and not fallback_original:
                    continue

                audio_path = (
                    self.config.path("paths.temp_dir")
                    / self._song_key(song_id, self.db.get_song(song_id)["original_mp3_sha256"])
                    / "chunks"
                    / f"chunk_{chunk.chunk_index:03d}_{input_name}_attempt{attempt}.wav"
                )
                try:
                    self.audio.slice_wav(source_audio, audio_path, effective_start, effective_end)
                    self.db.update_chunk(
                        chunk_id,
                        attempt_count=attempt,
                        input_audio=input_name,
                        status="processing",
                        error_code=None,
                        error_remark=None,
                    )
                    token_refs = self.tokenizer.build(chunk.words, model.tokenizer)
                    if not token_refs:
                        words = [
                            AlignedWord(
                                w.line_index, w.word_index, w.original, w.normalized, None, None, None,
                                source="missing", reason="no_supported_tokens", input_supported=w.supported,
                                chunk_id=chunk_id,
                            )
                            for w in chunk.words
                        ]
                        candidate = ChunkAlignment(
                            chunk, words, None, None, None, 0, 0, 0.0,
                            status="low_confidence", input_audio=input_name, device=model.device,
                            error_code="NO_SUPPORTED_TOKENS",
                            error_remark="No reference tokens supported by the tokenizer",
                        )
                        round_candidates.append(candidate)
                        continue

                    emission = model.emissions(audio_path)
                    aligned = self.aligner.align(emission.log_probs, token_refs, model.blank_token_id)
                    built = self.word_builder.build(
                        chunk.words,
                        token_refs,
                        aligned.spans,
                        audio_origin_ms=effective_start,
                        stride_ms=emission.stride_ms,
                        frame_limit_ms=effective_end - effective_start,
                        chunk_id=chunk_id,
                    )
                    scores = [w.score for w in built if w.source == "aligned" and w.score is not None]
                    mean_score, p10_score, min_score = self._stats_for_scores([float(x) for x in scores])
                    anchor_window = int(self.config.get("alignment.anchor_search_ms", 2500)) + extra
                    timed = [
                        w for w in built
                        if w.source == "aligned" and w.start_ms is not None and w.end_ms is not None
                    ]
                    anchor_ok = True
                    if timed:
                        earliest = min(int(w.start_ms) for w in timed)
                        latest = max(int(w.end_ms) for w in timed)
                        if latest < chunk.logical_start_ms - anchor_window or earliest > chunk.logical_end_ms + anchor_window:
                            anchor_ok = False
                    status = "aligned" if mean_score is not None and mean_score >= low_threshold and anchor_ok else "low_confidence"
                    candidate = ChunkAlignment(
                        chunk, built, mean_score, p10_score, min_score,
                        emission.frame_count, len(token_refs), emission.stride_ms,
                        status=status, input_audio=input_name, device=emission.device,
                        error_code=("CHUNK_ANCHOR_OUTSIDE_SEARCH_WINDOW" if not anchor_ok else None),
                        error_remark=(
                            f"Aligned spans fell outside logical chunk by more than {anchor_window} ms"
                            if not anchor_ok else None
                        ),
                    )
                    round_candidates.append(candidate)
                    self.db.log(
                        song_id,
                        "alignment",
                        "WARN" if status == "low_confidence" else "INFO",
                        f"Chunk {chunk.chunk_index} {input_name} attempt {attempt}: {status}",
                        attempt=attempt,
                        metadata={"mean_score": mean_score, "p10_score": p10_score, "extra_context_ms": extra},
                    )
                    if status == "aligned" and input_name == "vocals":
                        break
                except Exception as exc:
                    failed = ChunkAlignment(
                        chunk,
                        [
                            AlignedWord(
                                w.line_index, w.word_index, w.original, w.normalized, None, None, None,
                                source="missing", chunk_id=chunk_id, reason="chunk_alignment_failed",
                                input_supported=w.supported,
                            )
                            for w in chunk.words
                        ],
                        None, None, None, 0, 0, 0.0,
                        status="failed", error_code=str(exc).split(":", 1)[0], error_remark=str(exc),
                        input_audio=input_name,
                    )
                    round_candidates.append(failed)
                    self.db.log(
                        song_id,
                        "alignment",
                        "ERROR",
                        str(exc),
                        attempt=attempt,
                        metadata={"chunk_index": chunk.chunk_index, "input_audio": input_name},
                    )
                    if input_name == "vocals" and fallback_original:
                        continue
                    break

            if not round_candidates:
                continue

            def rank(candidate: ChunkAlignment) -> tuple[int, float, float]:
                score = candidate.mean_score if candidate.mean_score is not None else -1.0
                p10 = candidate.p10_score if candidate.p10_score is not None else -1.0
                return (1 if candidate.status == "aligned" else 0, score, p10)

            selected = max(round_candidates, key=rank)
            if best is None or rank(selected) > rank(best):
                best = selected
            if selected.status == "aligned":
                best = selected
                break
            if retry_round + 1 < max_attempts:
                self.db.log(
                    song_id,
                    "alignment",
                    "WARN",
                    f"Retrying chunk {chunk.chunk_index} after {selected.status}",
                    attempt=attempt,
                    metadata={"next_attempt": attempt + 1, "extra_context_ms": base_context * (retry_round + 1)},
                )
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass

        if best is None:
            raise RuntimeError(f"ALIGNMENT_FAILED: no candidate for chunk {chunk.chunk_index}")

        db_status = "aligned" if best.status == "aligned" else "low_confidence" if best.status == "low_confidence" else "failed"
        result_path = Path(chunk_row["result_json_path"])
        self._save_chunk_result(result_path, best)
        self.db.update_chunk(
            chunk_id,
            status=db_status,
            alignment_score=best.mean_score,
            p10_alignment_score=best.p10_score,
            minimum_alignment_score=best.minimum_score,
            input_audio=best.input_audio,
            device=best.device,
            error_code=best.error_code,
            error_remark=best.error_remark,
            result_json_path=str(result_path),
            attempt_count=last_attempt,
        )
        return best

    def _load_existing_chunk_results(self, song_id: int, document: LyricDocument) -> list[ChunkAlignment]:
        results: list[ChunkAlignment] = []
        for row in self.db.get_chunks(song_id):
            path = Path(row["result_json_path"]) if row["result_json_path"] else None
            if path and path.exists() and row["status"] in ("aligned", "low_confidence", "failed"):
                try:
                    results.append(self._load_chunk_result(path, self._chunk_from_row(row, document)))
                except Exception:
                    continue
        return results

    def _infer_instrumental_sections(self, song_id: int, document: LyricDocument, duration_ms: int,
                                     activity: dict[str, Any]) -> list[dict[str, Any]]:
        minimum = int(self.config.get("instrumental.minimum_gap_ms", 1500))
        intervals = blank_intervals(document, duration_ms, minimum_ms=minimum)
        speech = [(int(a), int(b)) for a, b in activity.get("speech", [])]
        energy = [(int(a), int(b)) for a, b in activity.get("energy", [])]
        rows: list[dict[str, Any]] = []
        for start, end in intervals:
            length = max(1, end - start)
            speech_overlap = sum(interval_overlap_ms((start, end), x) for x in speech) / length
            energy_overlap = sum(interval_overlap_ms((start, end), x) for x in energy) / length
            confidence = max(0.0, min(1.0, 0.70 * (1.0 - min(1.0, speech_overlap)) + 0.30 * (1.0 - min(1.0, energy_overlap))))
            section_type = "candidate_instrumental" if speech_overlap < 0.15 else "lyric_activity_gap"
            rows.append({
                "song_id": song_id,
                "start_ms": start,
                "end_ms": end,
                "section_type": section_type,
                "detector": "lrc_blank_interval+silero_vad+energy",
                "confidence": round(confidence, 5),
                "evidence_json": json.dumps({"speech_overlap_fraction": speech_overlap, "energy_overlap_fraction": energy_overlap}, sort_keys=True),
            })
        return rows

    def _words_from_db(self, song_id: int) -> list[AlignedWord]:
        return [AlignedWord(
            int(row["line_index"]), int(row["word_index"]), str(row["original_word"]), str(row["normalized_word"]),
            int(row["start_ms"]) if row["start_ms"] is not None else None,
            int(row["end_ms"]) if row["end_ms"] is not None else None,
            float(row["alignment_score"]) if row["alignment_score"] is not None else None,
            str(row["source"]),
            int(row["chunk_id"]) if row["chunk_id"] is not None else None,
            str(row["reason"]) if row["reason"] else None,
            True,
        ) for row in self.db.get_words(song_id)]

    def process(self, package: InputPackage, *, force: bool = False, debug: bool = False,
                keep_temp: bool = False, dry_run: bool = False) -> dict[str, Any]:
        started = time.monotonic()
        song_id = self.scanner.inventory(
            package,
            pipeline_version=self.project_version,
            normalizer_version=self.normalizer.config.version,
            config_hash=self.config.config_hash,
            model_name=str(self.config.get("models.mms_model", "facebook/mms-1b-all")),
            model_revision=self.config.get("models.mms_revision"),
        )
        row = self.db.get_song(song_id)
        if row is None:
            raise RuntimeError("SONG_NOT_FOUND after inventory")

        output_dir = self.config.path("paths.final_dir")
        output_dir.mkdir(parents=True, exist_ok=True)
        final_mp3 = output_dir / package.mp3_path.name
        final_lrc = output_dir / package.lrc_path.name
        final_json = output_dir / package.json_path.name

        if not force and self._is_same_identity(row, package, final_json):
            return {"status": "skipped", "quality_status": row["quality_status"], "basename": package.basename}
        if force:
            self.db.reset_for_reprocess(song_id)
            row = self.db.get_song(song_id)

        if dry_run:
            return {"status": "dry_run", "basename": package.basename}

        run_id = self.run_id or self.start_run()
        package_id = uuid.uuid4().hex
        original_json = self.json_manager.load(package.json_path)
        document = parse_lrc(package.lrc_path)
        self.normalizer.apply(document.lines)
        duration_ms = int(row["duration_ms"])

        work_root = self.config.path("paths.temp_dir") / self._song_key(song_id, row["original_mp3_sha256"])
        if force:
            shutil.rmtree(work_root, ignore_errors=True)
        work, audio_dir, chunks_dir, aligned_dir, stage_dir = self._prepare_work(song_id, row["original_mp3_sha256"])
        source_16k = audio_dir / "source_16k.wav"
        vocals_demucs = audio_dir / "vocals_demucs.wav"
        vocals_16k = audio_dir / "vocals_16k.wav"
        activity_json = work / "activity.json"
        source_hashes = {
            "mp3": sha256_file(package.mp3_path),
            "lrc": sha256_file(package.lrc_path),
            "json": sha256_file(package.json_path),
        }

        try:
            current_status = "pending" if force else str(row["pipeline_status"])
            if current_status in {"pending", "scanning", "lyrics_loaded"} or not source_16k.exists():
                self.db.set_song_status(song_id, pipeline_status="lyrics_loaded",
                                        expected_lines=len(document.lyric_lines),
                                        expected_words=sum(len(line.words) for line in document.lyric_lines),
                                        started_at=now_iso(), run_id=run_id, package_id=package_id)
                predecoded = self.config.get("audio.predecoded_source_16k")
                if predecoded and Path(str(predecoded)).exists():
                    shutil.copy2(Path(str(predecoded)), source_16k)
                else:
                    self.audio.decode_to_wav(package.mp3_path, source_16k)

            if current_status in {"pending", "scanning", "lyrics_loaded", "isolating"} or not vocals_demucs.exists() or not vocals_16k.exists():
                self.db.set_song_status(song_id, pipeline_status="isolating")
                precomputed_vocals = self.config.get("demucs.precomputed_vocals_path")
                if precomputed_vocals and Path(str(precomputed_vocals)).exists():
                    shutil.copy2(Path(str(precomputed_vocals)), vocals_demucs)
                    vocal_info = {
                        "status": "reused",
                        "device": self.config.get("demucs.precomputed_vocals_device", "unified_demucs"),
                        "model": self.config.get("models.demucs_model", "htdemucs"),
                        "path": str(vocals_demucs),
                        "source": "unified_shared_demucs",
                    }
                else:
                    isolator = DemucsIsolator(
                        str(self.config.get("models.demucs_model", "htdemucs")),
                        str(self.config.get("runtime.device", "cpu")),
                        str(self.config.get("runtime.fallback_device", "cpu")),
                        self.config.get("demucs.segment_seconds"),
                        require_cuda=self.require_cuda,
                        allow_cpu_fallback=True,
                        retry_segment=self.config.get("demucs.retry_segment_seconds", 8),
                        model_cache_dir=self.config.path("paths.models_dir"),
                    )
                    vocal_info = isolator.isolate(package.mp3_path, vocals_demucs)
                if bool(self.config.get("runtime.print_device_banner", True)):
                    print(f"RUNTIME | Demucs device={vocal_info.get('device')} | model={vocal_info.get('model')}")
                if self.audio.duration_ms_from_wav(vocals_demucs) < int(self.config.get("audio.minimum_duration_ms", 5000)):
                    raise RuntimeError("VOCALS_INVALID: vocal stem too short")
                self.audio.decode_to_wav(vocals_demucs, vocals_16k)
                self.db.set_song_status(song_id, pipeline_status="isolated")
            else:
                vocal_info = {"status": "reused", "model": self.config.get("models.demucs_model", "htdemucs"), "path": str(vocals_16k)}

            if current_status in {"pending", "scanning", "lyrics_loaded", "isolating", "isolated"} or not activity_json.exists():
                detector = ActivityDetector(
                    threshold=float(self.config.get("vad.threshold", 0.5)),
                    min_speech_duration_ms=int(self.config.get("vad.min_speech_duration_ms", 250)),
                    min_silence_duration_ms=int(self.config.get("vad.min_silence_duration_ms", 300)),
                    speech_pad_ms=int(self.config.get("vad.speech_pad_ms", 150)),
                    use_vad=bool(self.config.get("vad.enabled", True)),
                    energy_threshold_db=float(self.config.get("vad.energy_threshold_db", -38.0)),
                )
                activity = detector.detect(vocals_16k)
                json_dump_file(activity_json, activity)
            else:
                activity = json.loads(activity_json.read_text(encoding="utf-8"))

            chunks_rows = self.db.get_chunks(song_id)
            if current_status in {"pending", "scanning", "lyrics_loaded", "isolating", "isolated"} or not chunks_rows:
                self.db.set_song_status(song_id, pipeline_status="chunking")
                specs = self._build_and_store_chunks(song_id, document, duration_ms, source_16k, chunks_dir)
                chunks_rows = self.db.get_chunks(song_id)
                self.db.set_song_status(song_id, pipeline_status="chunked", total_chunks=len(specs))
            else:
                specs = [self._chunk_from_row(r, document) for r in chunks_rows]

            results: list[ChunkAlignment] = []
            self.db.set_song_status(song_id, pipeline_status="aligning")
            for row_chunk in self.db.get_chunks(song_id):
                chunk = self._chunk_from_row(row_chunk, document)
                stored_path = Path(row_chunk["result_json_path"]) if row_chunk["result_json_path"] else None
                if row_chunk["status"] in ("aligned", "low_confidence") and stored_path and stored_path.exists() and not force:
                    try:
                        result = self._load_chunk_result(stored_path, chunk)
                        results.append(result)
                        continue
                    except Exception:
                        pass
                result = self._align_one_chunk(song_id, row_chunk, chunk, vocals_16k, source_16k, debug=debug)
                results.append(result)

            # Validate both global word chronology and explicit blank-marker boundaries.
            # The latter is critical for source LRCs that contain long instrumental gaps:
            # a chunk may have model context past the blank marker, but lyric words must not
            # be allowed to spill into that lyric-free interval.
            max_global_retries = int(self.config.get("alignment.global_retry_max_chunks", 4))
            for _repair_round in range(max_global_retries):
                merged_probe = self.merger.merge(document, results, duration_ms)
                order_violations = self.merger.global_violations(merged_probe)
                boundary_violations = self.merger.boundary_violations(document, merged_probe)
                if boundary_violations:
                    self.db.log(
                        song_id, "alignment", "WARN",
                        f"Detected {len(boundary_violations)} explicit LRC blank-boundary alignment violation(s)",
                        metadata={
                            "violations": [
                                {
                                    "type": str(v.get("type")),
                                    "marker_ms": int(v.get("marker_ms", 0)),
                                    "chunk_id": v.get("chunk_id"),
                                    "line_index": v["word"].line_index if isinstance(v.get("word"), AlignedWord) else None,
                                    "word_index": v["word"].word_index if isinstance(v.get("word"), AlignedWord) else None,
                                    "start_ms": v["word"].start_ms if isinstance(v.get("word"), AlignedWord) else None,
                                    "end_ms": v["word"].end_ms if isinstance(v.get("word"), AlignedWord) else None,
                                }
                                for v in boundary_violations
                            ]
                        },
                    )
                if not order_violations and not boundary_violations:
                    merged_words = merged_probe
                    break

                targets: list[tuple[int, int | None, str]] = []
                seen: set[tuple[int, int | None, str]] = set()
                for violation in order_violations:
                    current = violation["current"]
                    if not isinstance(current, AlignedWord) or current.chunk_id is None:
                        continue
                    key = (int(current.chunk_id), None, "chronology")
                    if key not in seen:
                        seen.add(key)
                        targets.append(key)
                for violation in boundary_violations:
                    word = violation.get("word")
                    cid = violation.get("chunk_id")
                    if not isinstance(word, AlignedWord) or cid is None:
                        continue
                    marker_ms = int(violation["marker_ms"])
                    key = (int(cid), marker_ms, "blank_boundary")
                    if key not in seen:
                        seen.add(key)
                        targets.append(key)

                if not targets:
                    merged_words = merged_probe
                    break

                replaced = False
                for cid, boundary_ms, violation_kind in targets[:1]:
                    row_chunk = self.db.execute("SELECT * FROM chunks WHERE id=?", (cid,)).fetchone()
                    if row_chunk is None:
                        continue
                    chunk = self._chunk_from_row(row_chunk, document)
                    try:
                        new_result = self._align_one_chunk(
                            song_id, row_chunk, chunk, vocals_16k, source_16k, debug=debug,
                            strict_logical_window=True,
                            strict_boundary_end_ms=boundary_ms if violation_kind == "blank_boundary" else None,
                        )
                        for idx, old_result in enumerate(results):
                            if old_result.chunk.chunk_index == chunk.chunk_index:
                                results[idx] = new_result
                                replaced = True
                                break
                    except Exception as retry_exc:
                        self.db.log(
                            song_id, "alignment", "ERROR",
                            f"Global-order retry failed for chunk {chunk.chunk_index}: {retry_exc}",
                            metadata={
                                "chunk_id": cid,
                                "strict_logical_window": True,
                                "violation_kind": violation_kind,
                                "boundary_ms": boundary_ms,
                            },
                        )
                if not replaced:
                    merged_words = merged_probe
                    break
            else:
                merged_words = self.merger.merge(document, results, duration_ms)

            # Final merge after any global-order/boundary retries.
            merged_words = self.merger.merge(document, results, duration_ms)
            # Final render-safe timing normalization. This never sorts words; it only
            # adjusts affected reference-word timestamps and records repair reasons.
            final_timing_warnings = self.merger.finalize_output_timing(document, merged_words, duration_ms)
            if final_timing_warnings:
                self.db.log(
                    song_id,
                    "alignment",
                    "WARN",
                    f"Applied {len(final_timing_warnings)} final timing normalization repair(s)",
                    metadata={"warnings": final_timing_warnings},
                )

            failed_chunk_rows = self.db.execute("SELECT * FROM chunks WHERE song_id=? AND status='failed'", (song_id,)).fetchall()
            low_rows = self.db.execute("SELECT * FROM chunks WHERE song_id=? AND status='low_confidence'", (song_id,)).fetchall()
            self.db.set_song_status(song_id, pipeline_status="aligned",
                                    successful_chunks=sum(1 for r in results if r.status == "aligned"),
                                    low_confidence_chunks=sum(1 for r in results if r.status == "low_confidence"),
                                    failed_chunks=len(failed_chunk_rows))

            merged_words = self.merger.merge(document, results, duration_ms)
            continuity_shifts = []
            for word in merged_words:
                if word.reason and "global_continuity_shift_+" in word.reason:
                    try:
                        marker = word.reason.split("global_continuity_shift_+")[-1].split("ms", 1)[0]
                        continuity_shifts.append(int(marker))
                    except Exception:
                        pass
            failed_audio_ms = sum(int(r["logical_end_ms"]) - int(r["logical_start_ms"]) for r in failed_chunk_rows)
            metrics, _ = self.validator.validate_alignment(
                document, merged_words, duration_ms,
                min_word_score=float(self.config.get("alignment.min_word_score", 0.40)),
                anchor_warn_ms=int(self.config.get("validation.anchor_warn_ms", 2500)),
                failed_audio_ms=failed_audio_ms,
                min_word_duration_ms=int(self.config.get("validation.min_word_duration_ms", 30)),
                max_word_duration_ms=int(self.config.get("validation.max_word_duration_ms", 5000)),
            )
            if any(r.input_audio == "original" for r in results):
                metrics.reasons = sorted(set([*metrics.reasons, "VOCAL_STEM_FALLBACK"]))
            if failed_chunk_rows:
                metrics.reasons = sorted(set([*metrics.reasons, "CHUNK_FAILURE"]))
            repair_markers = [
                w for w in merged_words
                if w.reason and (
                    "global_continuity_shift_+" in w.reason
                    or "final_output_monotonic_shift_+" in w.reason
                    or "final_region_shift_+" in w.reason
                )
            ]
            metrics.continuity_repairs = len(repair_markers)
            metrics.max_continuity_repair_ms = max(continuity_shifts, default=0)
            if final_timing_warnings:
                metrics.reasons = sorted(set([*metrics.reasons, "FINAL_TIMING_REPAIR"]))
            metrics.total_chunks = len(results)
            metrics.successful_chunks = sum(1 for r in results if r.status == "aligned")
            metrics.low_confidence_chunks = sum(1 for r in results if r.status == "low_confidence")
            metrics.failed_chunks = len(failed_chunk_rows)
            quality_status = self.validator.classify(metrics, metrics.failed_chunks)

            self.db.clear_song_children(song_id)
            old_to_new_chunk_id: dict[int, int] = {}
            for result in results:
                row_chunk = self.db.add_chunk(song_id, {
                    "chunk_index": result.chunk.chunk_index,
                    "logical_start_ms": result.chunk.logical_start_ms,
                    "logical_end_ms": result.chunk.logical_end_ms,
                    "audio_start_ms": result.chunk.audio_start_ms,
                    "audio_end_ms": result.chunk.audio_end_ms,
                    "line_start_index": result.chunk.line_start_index,
                    "line_end_index": result.chunk.line_end_index,
                    "text_content": result.chunk.text_content,
                    "normalized_text": result.chunk.normalized_text,
                    "boundary_reason": result.chunk.boundary_reason,
                    "word_count": len(result.chunk.words),
                    "alignment_score": result.mean_score,
                    "p10_alignment_score": result.p10_score,
                    "minimum_alignment_score": result.minimum_score,
                    "status": result.status,
                    "input_audio": result.input_audio,
                    "device": result.device,
                    "audio_path": str(result.chunk.audio_path) if result.chunk.audio_path else None,
                    "result_json_path": str(result.chunk.result_json_path) if result.chunk.result_json_path else None,
                    "attempt_count": 1,
                    "error_code": result.error_code,
                    "error_remark": result.error_remark,
                })
                old_ids = {w.chunk_id for w in result.words if w.chunk_id is not None}
                for old_id in old_ids:
                    old_to_new_chunk_id[int(old_id)] = int(row_chunk)
            self.db.add_words([
                {
                    "song_id": song_id,
                    "chunk_id": old_to_new_chunk_id.get(int(word.chunk_id)) if word.chunk_id is not None else None,
                    "line_index": word.line_index,
                    "word_index": word.word_index,
                    "original_word": word.original,
                    "normalized_word": word.normalized,
                    "start_ms": word.start_ms,
                    "end_ms": word.end_ms,
                    "alignment_score": word.score,
                    "source": word.source,
                    "is_interpolated": 1 if word.source == "interpolated" else 0,
                    "reason": word.reason,
                }
                for word in merged_words
            ])

            section_rows = self._infer_instrumental_sections(song_id, document, duration_ms, activity)
            self.db.add_instrumental_sections(section_rows)
            self.db.set_song_status(
                song_id,
                pipeline_status="merged",
                quality_status=quality_status,
                aligned_lines=metrics.aligned_lines,
                aligned_words=metrics.aligned_words,
                interpolated_words=metrics.interpolated_words,
                missing_words=metrics.missing_words,
                unsupported_words=metrics.unsupported_words,
                successful_chunks=metrics.successful_chunks,
                low_confidence_chunks=metrics.low_confidence_chunks,
                failed_chunks=metrics.failed_chunks,
                mean_alignment_score=metrics.mean_alignment_score,
                p10_alignment_score=metrics.p10_alignment_score,
                minimum_alignment_score=metrics.minimum_alignment_score,
                low_score_word_percent=metrics.low_score_word_percent,
                interpolated_word_percent=metrics.interpolated_word_percent,
                failed_audio_percent=metrics.failed_audio_percent,
                median_anchor_shift_ms=metrics.median_anchor_shift_ms,
                p90_anchor_shift_ms=metrics.p90_anchor_shift_ms,
                max_anchor_shift_ms=metrics.max_anchor_shift_ms,
            )

            # Re-read canonical words from DB to guarantee output is based on the persisted record.
            merged_words = self._words_from_db(song_id)
            alignment_payload = self.json_updater.alignment_payload(document, merged_words)
            outputs: dict[str, Any] = {
                "mp3": f"songs/final/{package.mp3_path.name}",
                "lrc": f"songs/final/{package.lrc_path.name}",
                "json": f"songs/final/{package.json_path.name}",
            }
            model_meta = {
                "name": str(self.config.get("models.mms_model", "facebook/mms-1b-all")),
                "revision": self.config.get("models.mms_revision"),
                "language_iso1": self.config.get("models.language_iso1", "te"),
                "language_iso3": self.config.get("models.language_iso3", "tel"),
                "method": "MMS frame emissions + CTC reference forced alignment",
            }
            input_meta = {
                "mp3_filename": package.mp3_path.name,
                "lrc_filename": package.lrc_path.name,
                "json_filename": package.json_path.name,
                "mp3_sha256": source_hashes["mp3"],
                "lrc_sha256": source_hashes["lrc"],
                "json_sha256": source_hashes["json"],
                "source_mode": "external_lrc_plus_json",
            }
            normalization_meta = {
                "version": self.normalizer.config.version,
                "config": asdict(self.normalizer.config),
                "words_with_normalization_ops": sum(
                    1 for line in document.lyric_lines for word in line.words if word.normalization_ops
                ),
            }
            chunk_meta = [
                {
                    "chunk_index": r.chunk.chunk_index,
                    "logical_start_ms": r.chunk.logical_start_ms,
                    "logical_end_ms": r.chunk.logical_end_ms,
                    "audio_start_ms": r.chunk.audio_start_ms,
                    "audio_end_ms": r.chunk.audio_end_ms,
                    "line_start_index": r.chunk.line_start_index,
                    "line_end_index": r.chunk.line_end_index,
                    "status": r.status,
                    "mean_score": r.mean_score,
                    "p10_score": r.p10_score,
                    "minimum_score": r.minimum_score,
                    "frame_count": r.frame_count,
                    "token_count": r.token_count,
                    "stride_ms": r.stride_ms,
                    "input_audio": r.input_audio,
                    "device": r.device,
                    "error_code": r.error_code,
                    "error_remark": r.error_remark,
                }
                for r in results
            ]

            self.db.set_song_status(song_id, pipeline_status="validating")
            stage_mp3 = stage_dir / package.mp3_path.name
            stage_lrc = stage_dir / package.lrc_path.name
            stage_json = stage_dir / package.json_path.name
            self.lrc_generator.generate(document, merged_words, stage_lrc)
            self.lrc_generator.validate(stage_lrc, document, merged_words)

            before_snapshot = self.embedder.embed(package.mp3_path, stage_mp3, merged_words)
            self.embedder.validate(stage_mp3, before_snapshot, len([w for w in merged_words if w.start_ms is not None]))
            final_mp3_hash = sha256_file(stage_mp3)
            final_lrc_hash = sha256_file(stage_lrc)

            alignment_meta = {
                **alignment_payload,
                "engine_version": "phase2-ctc-v1",
                "frame_time_unit": "milliseconds",
            }
            phase2 = self.json_updater.build_phase2(
                run_id=run_id,
                package_id=package_id,
                input_package=input_meta,
                model=model_meta,
                audio={
                    "duration_ms": duration_ms,
                    "sample_rate": self.audio.sample_rate,
                    "channels": 1,
                },
                vocals=vocal_info,
                lyrics={
                    "source": "external_lrc",
                    "source_sha256": document.source_text_sha256,
                    "line_count": len(document.lines),
                    "lyric_line_count": len(document.lyric_lines),
                    "blank_timing_markers": len(document.blank_markers),
                },
                normalization=normalization_meta,
                alignment=alignment_meta,
                quality=metrics,
                chunks=chunk_meta,
                instrumental_sections=[{k: v for k, v in row.items() if k != "song_id"} for row in section_rows],
                outputs={**outputs, "mp3_sha256": final_mp3_hash, "lrc_sha256": final_lrc_hash},
                pipeline_version=self.project_version,
                schema_version=int(self.config.get("project.phase2_json_schema_version", 1)),
                normalizer_version=self.normalizer.config.version,
                elapsed_seconds=time.monotonic() - started,
            )
            phase2["config_hash"] = self.config.config_hash
            phase2["quality_status"] = quality_status
            final_json_obj = self.json_manager.add_phase2(original_json, phase2)
            self.json_manager.write(stage_json, final_json_obj)

            if not self.json_manager.validate_content_hash(final_json_obj):
                raise RuntimeError("JSON_WRITE_FAILED: final JSON canonical content hash failed")
            if not self.json_manager.non_phase2_equal(original_json, final_json_obj):
                raise RuntimeError("JSON_WRITE_FAILED: non-phase2 JSON data changed")

            # Cross-file consistency: the JSON must point to exactly the staged MP3/LRC
            # that will be promoted, and its canonical word count must match the
            # alignment from which both exports were generated.
            phase2_outputs = final_json_obj.get("phase2", {}).get("outputs", {})
            phase2_alignment = final_json_obj.get("phase2", {}).get("alignment", {})
            if phase2_outputs.get("mp3_sha256") != final_mp3_hash:
                raise RuntimeError("PACKAGE_CONSISTENCY_FAILED: JSON MP3 hash mismatch")
            if phase2_outputs.get("lrc_sha256") != final_lrc_hash:
                raise RuntimeError("PACKAGE_CONSISTENCY_FAILED: JSON LRC hash mismatch")
            if phase2_alignment.get("word_count") != len(merged_words):
                raise RuntimeError("PACKAGE_CONSISTENCY_FAILED: JSON alignment word count mismatch")

            if sha256_file(package.mp3_path) != source_hashes["mp3"]:
                raise RuntimeError("SOURCE_MODIFIED: original MP3 hash changed")
            if sha256_file(package.lrc_path) != source_hashes["lrc"]:
                raise RuntimeError("SOURCE_MODIFIED: original LRC hash changed")
            if sha256_file(package.json_path) != source_hashes["json"]:
                raise RuntimeError("SOURCE_MODIFIED: original JSON hash changed")

            self.db.set_song_status(song_id, pipeline_status="validated", quality_status=quality_status)

            # Stage files are complete and validated. Promote atomically per file.
            output_dir.mkdir(parents=True, exist_ok=True)
            for staged, destination in ((stage_mp3, final_mp3), (stage_lrc, final_lrc), (stage_json, final_json)):
                temp_dest = output_dir / f".{destination.name}.{package_id}.promote"
                atomic_copy(staged, temp_dest)
                os.replace(temp_dest, destination)

            final_hashes = {
                "mp3": sha256_file(final_mp3),
                "lrc": sha256_file(final_lrc),
                "json": sha256_file(final_json),
            }
            self.db.set_song_status(
                song_id, pipeline_status="finished", quality_status=quality_status,
                final_mp3_path=str(final_mp3), final_lrc_path=str(final_lrc), final_json_path=str(final_json),
                final_mp3_sha256=final_hashes["mp3"], final_lrc_sha256=final_hashes["lrc"], final_json_sha256=final_hashes["json"],
                run_id=run_id, package_id=package_id, completed_at=now_iso(),
            )
            self.db.log(song_id, "finalize", "INFO", "Final package promoted", metadata=final_hashes)
            if not keep_temp:
                shutil.rmtree(work, ignore_errors=True)
            return {
                "status": "finished",
                "quality_status": quality_status,
                "basename": package.basename,
                "outputs": {"mp3": str(final_mp3), "lrc": str(final_lrc), "json": str(final_json)},
                "metrics": self.json_updater.quality_payload(metrics),
            }
        except Exception as exc:
            self.db.set_song_status(song_id, pipeline_status="failed", quality_status="failed",
                                    error_code=str(exc).split(":", 1)[0], error_remark=str(exc),
                                    retry_count=int(row["retry_count"] or 0) + 1)
            self.db.log(song_id, "pipeline", "ERROR", str(exc), metadata={"basename": package.basename})
            raise

    def process_all(self, *, force: bool = False, debug: bool = False, keep_temp: bool = False,
                    dry_run: bool = False, basenames: set[str] | None = None, quality: str | None = None,
                    limit: int | None = None) -> dict[str, int]:
        packages, _issues = self.scan()
        if basenames is not None:
            packages = [p for p in packages if p.basename in basenames]
        if quality is not None:
            rows = self.db.songs_by_quality(quality)
            allowed = {str(row["basename"]) for row in rows}
            packages = [p for p in packages if p.basename in allowed]
        if limit is not None:
            packages = packages[:limit]

        counts = {"finished": 0, "skipped": 0, "dry_run": 0, "failed": 0}
        if not dry_run:
            self.ensure_runtime_ready()
        self.start_run()
        batch_started = time.monotonic()
        total = len(packages)
        try:
            for index, package in enumerate(packages, start=1):
                try:
                    result = self.process(package, force=force, debug=debug, keep_temp=keep_temp, dry_run=dry_run)
                    counts[str(result["status"])] = counts.get(str(result["status"]), 0) + 1
                except Exception as exc:
                    counts["failed"] += 1
                    print(f"ERROR {package.basename}: {exc}")
                elapsed = time.monotonic() - batch_started
                rate = index / elapsed if elapsed > 0 else 0.0
                remaining = max(0, total - index)
                print(
                    f"PROGRESS {index}/{total} | finished={counts['finished']} "
                    f"skipped={counts['skipped']} failed={counts['failed']} "
                    f"remaining={remaining} rate={rate:.3f} songs/s"
                )
        finally:
            self.finish_run()
        return counts
