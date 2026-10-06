from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from mutagen.id3 import APIC, ID3, SYLT
from mutagen.mp3 import MP3

from .types import AlignedWord, SourceSnapshot
from .utils import atomic_copy, sha256_file

PHASE2_SYLT_DESC = "Phase2-WordLevel"
PHASE2_LANG = "tel"
PHASE2_SYLT_KEY = f"SYLT:{PHASE2_SYLT_DESC}:{PHASE2_LANG}"


class MP3Embedder:
    def snapshot(self, path: Path) -> SourceSnapshot:
        audio = MP3(path, ID3=ID3)
        tags = audio.tags
        if tags is None:
            return SourceSnapshot({}, [], None, int(round(audio.info.length * 1000)))

        fingerprints: dict[str, str] = {}
        artwork_sha = None
        for key in sorted(tags.keys()):
            frame = tags[key]
            if key == PHASE2_SYLT_KEY:
                continue
            rendered = frame.pprint()
            if isinstance(frame, APIC):
                artwork_sha = hashlib.sha256(frame.data).hexdigest()
                rendered += f"|data_sha256={artwork_sha}"
            fingerprints[key] = hashlib.sha256(rendered.encode("utf-8", errors="replace")).hexdigest()

        return SourceSnapshot(
            frame_fingerprints=fingerprints,
            frame_keys=sorted(fingerprints),
            artwork_sha256=artwork_sha,
            audio_duration_ms=int(round(audio.info.length * 1000)),
        )

    def embed(self, source_mp3: Path, staged_mp3: Path, words: Iterable[AlignedWord]) -> SourceSnapshot:
        before = self.snapshot(source_mp3)
        atomic_copy(source_mp3, staged_mp3)

        audio = MP3(staged_mp3, ID3=ID3)
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags

        if PHASE2_SYLT_KEY in tags:
            del tags[PHASE2_SYLT_KEY]

        sylt_data = [
            (word.original, int(word.start_ms))
            for word in words
            if word.start_ms is not None
        ]
        if not sylt_data:
            raise RuntimeError("SYLT_EMBED_FAILED: no word timestamps available")

        tags.add(SYLT(
            encoding=3,
            lang=PHASE2_LANG,
            format=2,
            type=1,
            desc=PHASE2_SYLT_DESC,
            text=sylt_data,
        ))
        audio.save(v2_version=3)
        return before

    def validate(self, final_mp3: Path, before: SourceSnapshot, expected_word_count: int) -> None:
        after = self.snapshot(final_mp3)
        for key, old_hash in before.frame_fingerprints.items():
            if key not in after.frame_fingerprints:
                raise RuntimeError(f"MP3_VALIDATION_FAILED: metadata frame disappeared: {key}")
            if after.frame_fingerprints[key] != old_hash:
                raise RuntimeError(f"MP3_VALIDATION_FAILED: metadata frame changed: {key}")

        audio = MP3(final_mp3, ID3=ID3)
        duration_ms = int(round(audio.info.length * 1000))
        if duration_ms != before.audio_duration_ms:
            raise RuntimeError(f"MP3_VALIDATION_FAILED: duration changed {before.audio_duration_ms} -> {duration_ms}")

        tags = audio.tags
        if tags is None or PHASE2_SYLT_KEY not in tags:
            raise RuntimeError("MP3_VALIDATION_FAILED: Phase2 SYLT missing")
        frame = tags[PHASE2_SYLT_KEY]
        if len(frame.text) != expected_word_count:
            raise RuntimeError(
                f"MP3_VALIDATION_FAILED: expected {expected_word_count} SYLT entries, got {len(frame.text)}"
            )
        timestamps = [int(ts) for _, ts in frame.text]
        for index, (prev, curr) in enumerate(zip(timestamps, timestamps[1:]), start=1):
            if curr < prev:
                raise RuntimeError(
                    f"MP3_VALIDATION_FAILED: SYLT timestamps are not chronological at index {index}: {prev} -> {curr}"
                )
        if any(ts < 0 or ts > duration_ms for ts in timestamps):
            raise RuntimeError("MP3_VALIDATION_FAILED: SYLT timestamp out of bounds")

    @staticmethod
    def file_hash(path: Path) -> str:
        return sha256_file(path)
