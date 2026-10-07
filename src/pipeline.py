from __future__ import annotations

import copy
import json
import logging
import os
import re
import shutil
import time
from dataclasses import replace
from pathlib import Path

from .acquisition import AcquisitionError, acquire, normalized_from_acquisition
from .audio_unified import prepare_source, prepare_full_source
from .db_reels import ReelsDB
from .db_songs import SongsDB
from .eightd import render as render_8d
from .hashing import sha256_file, stable_json_hash
from .hook import prompt_or_existing
from .hook import fmt_ms
from .utils import parse_timecode
from .json_record import build as build_json, write as write_json
from .lrclib import LRCLIBClient, LyricsResult, contains_telugu_script
from .metadata import NormalizedMetadata
from .mp3_metadata import embed
from .reel import build_reel
from .spotify import SpotifyClient, SpotifyError, apply_spotify_metadata
from .utils import atomic_write_json, duration_ms, safe_name
from .youtube_sync import find_offset
from .youtube_video import download_audio, select_video
from .demucs import DemucsRunner
from .gpu import cuda_available
from .alignment import run_alignment


class Pipeline:
    """Single-song orchestrator for the Spotify-first unified pipeline."""

    def __init__(self, cfg, playlist_db, songs_db: SongsDB, reels_db: ReelsDB, logger=None):
        self.cfg = cfg
        self.playlist_db = playlist_db
        self.songs_db = songs_db
        self.reels_db = reels_db
        self.logger = logger or logging.getLogger("unified")
        for p in (
            cfg.db_dir,
            cfg.work_dir,
            cfg.final_dir,
            cfg.skipped_dir,
            cfg.reels_dir,
            cfg.fonts_dir,
            cfg.path("paths.logs_dir"),
        ):
            p.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _song_key(entry: dict) -> str:
        track_id = str(entry.get("spotify_track_id") or "unavailable").strip()
        return f"{int(entry['playlist_serial'])}_{track_id}"

    @staticmethod
    def _basename(serial: int, metadata: NormalizedMetadata) -> str:
        title = safe_name(metadata.title or "Untitled")
        artist = safe_name(metadata.artist or "Unknown Artist")
        value = f"{int(serial):03d}_{title} - {artist}".strip().rstrip(" ._")
        return value[:180] or f"{int(serial):03d}_untitled"

    def _stage(self, key, name, dep, artifact=None):
        return self.songs_db.stage_start(key, name, dep, artifact)

    def _finish(self, run_id, status, artifact=None, sha=None, reused=0, err=None):
        self.songs_db.stage_finish(run_id, status, artifact, sha, reused, error_message=err)

    def _metadata_dict(self, metadata):
        return {k: copy.deepcopy(getattr(metadata, k)) for k in metadata.__dataclass_fields__ if k != "raw"}

    @staticmethod
    def _words_from_p2(p2j):
        alignment = p2j.get("phase2", {}).get("alignment", {}) or {}
        words = []
        for line in alignment.get("lines", []) or []:
            for word in line.get("words", []) or []:
                words.append(
                    {
                        "line_index": line.get("line_index"),
                        "word_index": word.get("word_index"),
                        "original_word": word.get("original"),
                        "normalized_word": word.get("normalized"),
                        "start_ms": word.get("start_ms"),
                        "end_ms": word.get("end_ms"),
                        "score": word.get("score"),
                        "source": word.get("source"),
                        "interpolated": word.get("source") == "interpolated",
                        "review_reason": word.get("reason"),
                    }
                )
        return words

    def _cached_stage(self, key, stage, fingerprint, artifact_paths):
        row = self.songs_db.latest_stage(key, stage, fingerprint)
        if not row:
            return None
        for item in artifact_paths:
            if item and (not Path(item).exists() or Path(item).stat().st_size == 0):
                return None
        return row

    def _record_reused(self, key, stage, fingerprint, artifact, sha):
        rid = self._stage(key, stage, fingerprint, artifact)
        self._finish(rid, "completed", artifact, sha, reused=1)
        return rid

    def _spotify_result_from_state(self, state_path: Path):
        state = json.loads(state_path.read_text(encoding="utf-8"))
        return state

    def _load_spotify_track(self, key: str, entry: dict, work: Path, state: dict):
        spotify_track_id = str(entry.get("spotify_track_id") or "").strip()
        if not spotify_track_id:
            raise SpotifyError("SPOTIFY_TRACK_UNAVAILABLE: playlist entry contains no Spotify track ID")

        metadata_dir = work / "metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        track_json = metadata_dir / "spotify_full.json"
        artwork_path = metadata_dir / "spotify_artwork.jpg"
        fp = stable_json_hash({
            "spotify_track_id": spotify_track_id,
            "spotify_metadata_engine": "spotify-web-api-full-track-album-artists-v2",
            "market": self.cfg.get("spotify.market", "IN"),
        })
        cached = self._cached_stage(key, "spotify_metadata", fp, [track_json, artwork_path]) if track_json.exists() else None
        if state.get("spotify_metadata", {}).get("fingerprint") == fp and cached:
            data = json.loads(track_json.read_text(encoding="utf-8"))
            result = SpotifyClient.from_json(data)
            self._record_reused(key, "spotify_metadata", fp, track_json, sha256_file(track_json))
            return result, {"fingerprint": fp, "path": track_json, "artwork_path": artwork_path, "reused": True}

        stage = self._stage(key, "spotify_metadata", fp, track_json)
        client = SpotifyClient(self.cfg.get("spotify", {}) or {}, project_root=self.cfg.root)
        if not client.configured:
            self._finish(stage, "error", err="SPOTIFY_AUTH_REQUIRED")
            raise SpotifyError(
                "SPOTIFY_AUTH_REQUIRED: configure spotify_auth.json or Spotify credentials before processing songs"
            )
        try:
            result = client.get_full_track(spotify_track_id)
            artwork = None
            if result.album_artwork_url:
                ext = ".jpg"
                if result.album_artwork_url.lower().split("?", 1)[0].endswith(".png"):
                    ext = ".png"
                elif result.album_artwork_url.lower().split("?", 1)[0].endswith(".webp"):
                    ext = ".webp"
                artwork_path = metadata_dir / f"spotify_artwork{ext}"
                artwork = client.download_artwork(result, artwork_path)
            payload = SpotifyClient.to_json(result)
            track_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            self._finish(stage, "completed", track_json, sha256_file(track_json))
            self.songs_db.upsert_metadata(
                key,
                {
                    "spotify_isrc": result.isrc,
                    "spotify_track_id": result.track_id,
                    "spotify_album_id": result.album_id,
                    "spotify_track_url": result.track_url,
                    "spotify_track_uri": result.uri,
                    "spotify_album_url": result.album_url,
                    "spotify_artist_ids_json": json.dumps(result.artist_ids, ensure_ascii=False),
                    "spotify_album_images_json": json.dumps([x.__dict__ for x in result.album_images], ensure_ascii=False),
                    "spotify_raw_track_json": json.dumps(result.raw, ensure_ascii=False, default=str),
                    "spotify_raw_album_json": json.dumps(result.album_raw, ensure_ascii=False, default=str),
                    "spotify_raw_artists_json": json.dumps(result.artist_details, ensure_ascii=False, default=str),
                    "spotify_artwork_path": str(artwork_path) if artwork else None,
                    "spotify_artwork_sha256": sha256_file(artwork_path) if artwork else None,
                    "spotify_artwork_mime": artwork.get("mime") if artwork else None,
                    "spotify_lookup_status": "matched",
                    "normalized_title": result.track_name,
                    "normalized_artist": result.artist_string,
                    "normalized_album": result.album_name,
                    "album_artist": result.album_artist_string,
                    "release_date": result.album_release_date,
                    "release_date_precision": result.album_release_precision,
                    "track_number": result.track_number,
                    "disc_number": result.disc_number,
                    "album_total_tracks": result.album_total_tracks,
                    "album_type": result.album_type,
                    "explicit": int(result.explicit) if result.explicit is not None else None,
                    "genre": ", ".join(result.artist_genres) if result.artist_genres else None,
                    "publisher": result.album_label,
                    "copyright_text": " | ".join(result.album_copyrights) if result.album_copyrights else None,
                    "ytm_source_video_id": entry.get("ytm_video_id"),
                    "ytm_source_url": None,
                    "ytm_source_duration_ms": None,
                },
            )
            return result, {
                "fingerprint": fp,
                "path": track_json,
                "artwork_path": artwork_path if artwork else None,
                "artwork": artwork,
                "reused": False,
            }
        except Exception as exc:
            self._finish(stage, "error", err=str(exc))
            raise

    def _terminal_duplicate(self, key, entry, metadata, duplicate):
        print("\nDUPLICATE ISRC DETECTED")
        print("ISRC:", duplicate.get("canonical_isrc"))
        print("Current:", metadata.title, "-", metadata.artist)
        print("Previous:", duplicate.get("title"), "-", duplicate.get("artist"))
        print("1 = keep previous / 2 = keep current")
        while True:
            answer = input("> ").strip().lower()
            if answer in {"1", "keep_previous", "p"}:
                return "keep_previous"
            if answer in {"2", "keep_current", "c"}:
                return "keep_current"

    def _record_skip_no_lrc(self, key, entry, metadata, acq, info, spotify_result, spotify_state, name, reason):
        self.cfg.skipped_dir.mkdir(parents=True, exist_ok=True)
        final_mp3 = self.cfg.skipped_dir / f"{name}.mp3"
        final_json = self.cfg.skipped_dir / f"{name}.json"
        artwork_info = spotify_state.get("artwork") if isinstance(spotify_state, dict) else None
        embed(acq["mp3"], final_mp3, metadata, artwork_info, [], {"SPOTIFY_ISRC": spotify_result.isrc if spotify_result else None})
        payload = build_json(
            info,
            playlist={
                "provider": "spotify",
                "playlist_id": entry.get("playlist_id"),
                "playlist_position": entry.get("playlist_position"),
                "playlist_serial": entry.get("playlist_serial"),
                "added_at": entry.get("added_at"),
            },
            spotify={"track": SpotifyClient.to_json(spotify_result) if spotify_result else None, "artwork": spotify_state.get("artwork")},
            metadata=self._metadata_dict(metadata),
            ytmusic_audio={
                "video_id": acq.get("ytm_video_id"),
                "url": acq.get("ytm_url"),
                "query": acq.get("ytm_query"),
                "candidate": acq.get("ytm_candidate"),
            },
            lyrics={"status": "SKIPPED_NO_SYNCED_LRC", "reason": reason},
            status="SKIPPED_NO_SYNCED_LRC",
            provenance={"source_mp3_sha256": sha256_file(acq["mp3"]), "spotify_isrc": metadata.isrc},
        )
        payload.setdefault("unified_pipeline", {}).setdefault("outputs", {})["skipped_mp3"] = str(final_mp3.relative_to(self.cfg.root))
        write_json(final_json, payload)
        self.songs_db.update_song(
            key,
            pipeline_status="SKIPPED_NO_SYNCED_LRC",
            quality_status="skipped",
            terminal=1,
            terminal_reason="SKIPPED_NO_SYNCED_LRC",
        )
        self.playlist_db.set_status(entry["playlist_serial"], "skipped", terminal=1, reason="SKIPPED_NO_SYNCED_LRC")
        return "skipped"

    def _hook_queue_path(self):
        return self.cfg.hook_queue_file

    def _load_hook_queue(self):
        path = self._hook_queue_path()
        if not path.exists():
            return {"version": 1, "updated_at": time.time(), "entries": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                data = {"version": 1, "updated_at": time.time(), "entries": data}
            if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
                raise ValueError("invalid hook queue structure")
            return data
        except Exception as exc:
            raise RuntimeError(f"HOOK_QUEUE_INVALID: {path}: {exc}") from exc

    def _save_hook_queue(self, data):
        path = self._hook_queue_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data["version"] = 1
        data["updated_at"] = time.time()
        atomic_write_json(path, data)

    def _upsert_hook_queue_entry(self, entry):
        data = self._load_hook_queue()
        key = str(entry["song_key"])
        replaced = False
        for i, old in enumerate(data["entries"]):
            if str(old.get("song_key")) == key:
                merged = dict(old)
                merged.update(entry)
                data["entries"][i] = merged
                replaced = True
                break
        if not replaced:
            data["entries"].append(dict(entry))
        self._save_hook_queue(data)

    def _hook_queue_entry(self, song_key):
        data = self._load_hook_queue()
        for item in data["entries"]:
            if str(item.get("song_key")) == str(song_key) and item.get("status") != "completed":
                return item
        return None

    def _mark_hook_queue_completed(self, song_key, reel_path, reel_json_path):
        data = self._load_hook_queue()
        changed = False
        for item in data["entries"]:
            if str(item.get("song_key")) == str(song_key):
                item["status"] = "completed"
                item["reel_path"] = str(Path(reel_path).relative_to(self.cfg.root))
                item["reel_json_path"] = str(Path(reel_json_path).relative_to(self.cfg.root))
                item["completed_at"] = time.time()
                changed = True
        if changed:
            self._save_hook_queue(data)

    def _hook_queue_sources_exist(self, queued):
        """Return whether a persisted hook-queue entry still has its local source package.

        hook_queue.json is intentionally trackable while songs/final artifacts are generated
        and ignored. A fresh checkout can therefore contain a valid historical queue entry
        whose generated source files are not present locally. Such an entry must not crash the
        pipeline; it is stale and the normal expensive pipeline should rebuild the package.
        """
        required = (
            queued.get("song_path"),
            queued.get("lrc_path"),
            queued.get("wordlevel_lrc_path"),
            queued.get("eightd_path"),
            queued.get("json_path"),
        )
        for rel in required:
            if not rel:
                return False
            p = Path(str(rel))
            if not p.is_absolute():
                p = self.cfg.root / p
            if not p.exists() or p.stat().st_size == 0:
                return False
        return True

    def _mark_hook_queue_stale(self, song_key, reason):
        data = self._load_hook_queue()
        changed = False
        for item in data["entries"]:
            if str(item.get("song_key")) == str(song_key) and item.get("status") != "completed":
                item["status"] = "stale"
                item["stale_reason"] = str(reason)
                item["stale_at"] = time.time()
                changed = True
        if changed:
            self._save_hook_queue(data)

    def _finalize_from_hook_queue(self, entry, queued):
        """Build only the Reel from a persisted pre-hook package; never reacquire media."""
        key = str(queued["song_key"])
        name = str(queued["song_name"])
        work = Path(queued["work_dir"])
        if not work.is_absolute():
            work = self.cfg.root / work
        final_mp3 = self.cfg.root / str(queued["song_path"])
        final_lrc = self.cfg.root / str(queued["lrc_path"])
        final_wlrc = self.cfg.root / str(queued.get("wordlevel_lrc_path") or f"songs/final/{name}_wordlevel.lrc")
        final_8d = self.cfg.root / str(queued["eightd_path"])
        final_json = self.cfg.root / str(queued["json_path"])
        for p in (final_mp3, final_lrc, final_wlrc, final_8d, final_json):
            if not p.exists() or p.stat().st_size == 0:
                raise RuntimeError(f"HOOK_QUEUE_SOURCE_MISSING: {p}")

        hs = queued.get("hook_start_ms")
        he = queued.get("hook_end_ms")
        # Human-editable queue fields are authoritative when millisecond fields are absent.
        if hs is None and queued.get("hook_start_time") not in (None, ""):
            hs = parse_timecode(str(queued["hook_start_time"]))
        if he is None and queued.get("hook_end_time") not in (None, ""):
            he = parse_timecode(str(queued["hook_end_time"]))
        if hs is None or he is None:
            return "hook_pending"
        from .hook import validate as validate_hook
        duration = int(queued.get("duration_ms") or duration_ms(final_mp3))
        hs, he = int(hs), int(he)
        validate_hook(hs, he, duration)

        video_url = str(queued.get("youtube_video_url") or "").strip()
        video_id = str(queued.get("youtube_video_id") or "").strip()
        if not video_url:
            raise RuntimeError("HOOK_QUEUE_MISSING_VIDEO_URL")
        offset_ms = int(queued.get("youtube_offset_ms") or 0)

        reel_key = f"{key}_reel_v1"
        self.reels_db.ensure(reel_key, key)
        song_record = {"song_key": key, "basename": name, "duration_ms": duration, "eight_d_path": str(final_8d)}
        rf, rj, rmeta = build_reel(self.cfg, work, song_record, hs, he, video_url, offset_ms, final_wlrc, [], self.logger)
        rv = json.loads((work / "validation" / "reel_validation.json").read_text(encoding="utf-8"))

        reel_final = self.cfg.reels_dir / f"{name}_reel.mp4"
        reel_json_final = self.cfg.reels_dir / f"{name}_reel.json"
        reel_final.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rf, reel_final)
        shutil.copy2(rj, reel_json_final)

        final_json_obj = json.loads(final_json.read_text(encoding="utf-8"))
        up = final_json_obj.setdefault("unified_pipeline", {})
        up["status"] = "FINALIZED"
        up["hook"] = {"start_ms": hs, "end_ms": he, "duration_ms": he - hs, "input_method": "hook_queue_json"}
        up["reel"] = rmeta
        up["outputs"] = {
            "mp3": str(final_mp3.relative_to(self.cfg.root)),
            "lrc": str(final_lrc.relative_to(self.cfg.root)),
            "wordlevel_lrc": str(final_wlrc.relative_to(self.cfg.root)),
            "json": str(final_json.relative_to(self.cfg.root)),
            "8d_mp3": str(final_8d.relative_to(self.cfg.root)),
            "reel": str(reel_final.relative_to(self.cfg.root)),
            "reel_json": str(reel_json_final.relative_to(self.cfg.root)),
        }
        up["hashes"] = {
            "mp3": sha256_file(final_mp3), "lrc": sha256_file(final_lrc),
            "wordlevel_lrc": sha256_file(final_wlrc), "8d_mp3": sha256_file(final_8d),
            "reel": sha256_file(reel_final), "reel_json": sha256_file(reel_json_final),
        }
        write_json(final_json, final_json_obj)
        self._validate_package(final_mp3, final_lrc, final_wlrc, final_8d, final_json, duration, reel_final, reel_json_final)

        self.reels_db.upsert("reel_hooks", "reel_key", reel_key, {"start_ms": hs, "end_ms": he, "duration_ms": he-hs, "input_method": "hook_queue_json"})
        self.reels_db.upsert("reel_youtube_sync", "reel_key", reel_key, {
            "video_id": video_id, "video_url": video_url, "video_offset_ms": offset_ms,
            "mapped_start_ms": hs + offset_ms, "mapped_end_ms": he + offset_ms,
            "guard_before_ms": int(self.cfg.get("video_match.guard_before_ms", 1500)),
            "guard_after_ms": int(self.cfg.get("video_match.guard_after_ms", 1500)), "status": "validated",
        })
        self.reels_db.upsert("reel_audio", "reel_key", reel_key, {
            "source_8d_path": str(final_8d), "source_8d_sha256": sha256_file(final_8d),
            "crop_start_ms": hs, "crop_end_ms": he, "crop_duration_ms": he-hs,
            "output_path": str(work / "audio" / "hook_8d.mp3"),
            "output_sha256": sha256_file(work / "audio" / "hook_8d.mp3"), "status": "validated",
        })
        self.reels_db.upsert("reel_video", "reel_key", reel_key, {
            "video_source_id": video_id, "source_url": video_url, "source_start_ms": hs + offset_ms,
            "source_end_ms": he + offset_ms, "requested_duration_ms": he-hs,
            "actual_duration_ms": rv.get("probe", {}).get("duration_ms"), "width": 1080, "height": 1920,
            "panel_height_px": int(1920 * 0.8), "panel_height_percent": 80.0,
            "encoder": rv.get("probe", {}).get("video_codec"), "output_path": str(reel_final), "status": "validated",
        })
        self.reels_db.upsert("reel_lyrics", "reel_key", reel_key, {
            "wordlevel_lrc_path": str(final_wlrc), "wordlevel_lrc_sha256": sha256_file(final_wlrc),
            "font_name": "Baloo Tammudu 2 ExtraBold", "font_path": str(self.cfg.get("lyrics.font_path") or (self.cfg.fonts_dir / self.cfg.get("lyrics.font_filename", "BalooTammudu2-ExtraBold.ttf"))),
            "placement": "center", "line_behavior": "one_line_at_a_time", "word_highlight_enabled": 1, "status": "validated",
        })
        self.reels_db.upsert("reel_validation", "reel_key", reel_key, {
            "overall": int(bool(rv["overall"])), "failed_checks_json": json.dumps(rv.get("failed_checks", []), ensure_ascii=False),
            "output_exists": int(rv["checks"].get("exists", False)), "output_duration_matches": int(rv["checks"].get("duration_match", False)),
            "resolution_valid": int(rv["checks"].get("resolution_valid", False)), "aspect_ratio_valid": int(rv["checks"].get("aspect_ratio_valid", False)),
            "audio_stereo": int(rv["checks"].get("audio_stereo", False)), "has_video": int(rv["checks"].get("has_video", False)),
            "status": "validated" if rv["overall"] else "failed",
        })
        self.reels_db.update(reel_key, pipeline_status="FINALIZED", terminal=1,
            final_reel_path=str(reel_final.relative_to(self.cfg.root)), final_reel_json_path=str(reel_json_final.relative_to(self.cfg.root)),
            final_reel_sha256=sha256_file(reel_final), output_duration_ms=duration_ms(reel_final), output_width=1080, output_height=1920,
            quality_status="passed", youtube_video_id=video_id, youtube_offset_ms=offset_ms,
            youtube_mapped_start_ms=hs+offset_ms, youtube_mapped_end_ms=he+offset_ms,
            eight_d_source_path=str(final_8d.relative_to(self.cfg.root)), eight_d_source_sha256=sha256_file(final_8d))
        self.songs_db.update_song(key, pipeline_status="FINALIZED", quality_status="passed", terminal=1,
            final_mp3_path=str(final_mp3.relative_to(self.cfg.root)), final_lrc_path=str(final_lrc.relative_to(self.cfg.root)),
            final_wordlevel_lrc_path=str(final_wlrc.relative_to(self.cfg.root)), final_json_path=str(final_json.relative_to(self.cfg.root)),
            final_8d_mp3_path=str(final_8d.relative_to(self.cfg.root)), ytm_video_id=queued.get("ytmusic_audio_video_id"), ytm_url=queued.get("ytmusic_audio_url"))
        self.playlist_db.set_status(int(queued["playlist_serial"]), "completed", terminal=1, reason="FINALIZED")
        self.playlist_db.audit(entry.get("playlist_id"), "song_finalized", f"{name} finalized from hook queue", serial=int(queued["playlist_serial"]))
        self._mark_hook_queue_completed(key, reel_final, reel_json_final)
        return "completed_from_hook_queue"

    def process_entry(self, entry: dict, force: bool = False):
        serial = int(entry["playlist_serial"])
        key = self._song_key(entry)
        work_base = self.cfg.work_dir / key
        work_base.mkdir(parents=True, exist_ok=True)
        queued = self._hook_queue_entry(key)
        if queued:
            # hook_queue.json is intentionally tracked, while songs/final artifacts are
            # generated and ignored. On a fresh checkout or after cleanup, the queue can
            # legitimately outlive its generated source package. Treat that entry as stale
            # and rebuild the normal pipeline instead of crashing with HOOK_QUEUE_SOURCE_MISSING.
            if not self._hook_queue_sources_exist(queued):
                self.logger.info(
                    "Song serial=%s has a stale hook queue entry because its generated source package is missing; rebuilding.",
                    serial,
                )
                self._mark_hook_queue_stale(key, "SOURCE_PACKAGE_MISSING")
                queued = None
            else:
                result = self._finalize_from_hook_queue(entry, queued)
                if result == "hook_pending":
                    self.logger.info("Song serial=%s is waiting for hook times in %s", serial, self._hook_queue_path())
                return result
        state_path = work_base / "state.json"
        state = {}
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                state = {}
        state.update({"song_key": key, "playlist_serial": serial, "spotify_track_id": entry.get("spotify_track_id"), "playlist_position": entry.get("playlist_position"), "started_at": state.get("started_at") or time.time()})
        atomic_write_json(state_path, state)

        if not entry.get("spotify_track_id"):
            self.playlist_db.set_status(serial, "skipped", terminal=1, reason=entry.get("terminal_reason") or "NON_TRACK_ITEM")
            return "skipped"

        active_stage_id = None
        try:
            # SongsDB stage_runs/audit_events have a foreign key to songs(song_key).
            # Create the parent row before the first stage can be started. This must happen
            # even when Spotify metadata acquisition subsequently fails, so the original
            # processing error can be recorded without causing a second FK failure.
            self.playlist_db.set_status(serial, "processing", terminal=0, reason="PROCESSING")
            entry_title = str(entry.get("title") or "Untitled").strip() or "Untitled"
            entry_artist = str(entry.get("artist") or "Unknown Artist").strip() or "Unknown Artist"
            entry_album = str(entry.get("album") or "").strip() or None
            bootstrap_name = f"{int(serial):03d}_{safe_name(entry_title)} - {safe_name(entry_artist)}".rstrip(" ._")[:180]
            self.songs_db.ensure_song({
                "song_key": key,
                "playlist_serial": serial,
                "spotify_track_id": entry.get("spotify_track_id"),
                "basename": bootstrap_name or f"{int(serial):03d}_untitled",
                "title": entry_title,
                "artist": entry_artist,
                "album": entry_album,
                "duration_ms": entry.get("duration_ms"),
                "pipeline_status": "PROCESSING",
                "terminal": 0,
            })
            spotify_result, spotify_state = self._load_spotify_track(key, entry, work_base, state)
            state["spotify_metadata"] = {
                "fingerprint": spotify_state["fingerprint"],
                "path": str(spotify_state["path"]),
                "artwork_path": str(spotify_state["artwork_path"]) if spotify_state.get("artwork_path") else None,
                "artwork_sha256": sha256_file(spotify_state["artwork_path"]) if spotify_state.get("artwork_path") else None,
            }
            atomic_write_json(state_path, state)

            # Base metadata starts with the frozen playlist entry and is then replaced/overlaid
            # by complete Spotify catalog metadata. Spotify is the authoritative catalog source.
            source_stub = NormalizedMetadata(
                title=str(entry.get("title") or spotify_result.track_name or "Untitled"),
                title_original=str(entry.get("title") or spotify_result.track_name or "Untitled"),
                primary_artist=str(entry.get("artist") or spotify_result.artist_string or "Unknown Artist").split(",")[0].strip(),
                artist=str(entry.get("artist") or spotify_result.artist_string or "Unknown Artist"),
                artists=[x.strip() for x in str(entry.get("artist") or spotify_result.artist_string or "Unknown Artist").split(",") if x.strip()],
                album=str(entry.get("album") or spotify_result.album_name or "") or None,
                album_artist=None,
                isrc=spotify_result.isrc,
                track_number=None,
                disc_number=None,
                release_date=None,
                release_date_source=None,
                upload_date=None, upload_timestamp=None, release_timestamp=None,
                modified_date=None, modified_timestamp=None,
                description=None, genre=None, composer=None, publisher=None, copyright=None, license=None, comment=None,
                language="tel", bpm=None, compilation=None, encoder=None,
                duration=int(round(spotify_result.duration_ms / 1000)), source_duration=int(round(spotify_result.duration_ms / 1000)),
                source_ext="mp3", source_container="mp3", source_codec="mp3", source_format_id=None, source_format_note=None,
                source_bitrate=None, source_sample_rate=None, source_channels=None, source_filesize=None, source_filesize_approx=None,
                source_language=None, source_video_id=None, source_webpage_url=None, source_original_url=None, source_display_id=None,
                source_webpage_url_basename=None, source_webpage_url_domain=None, source_extractor="Spotify+YTMusic", source_extractor_key="Spotify+YTMusic",
                source_channel=None, source_channel_id=None, source_channel_url=None, source_channel_follower_count=None, source_channel_is_verified=None,
                source_uploader=None, source_uploader_id=None, source_uploader_url=None, source_views=None, source_location=None, source_availability=None,
                source_age_limit=None, source_live_status=None, source_media_type="audio", source_thumbnail=None, source_thumbnails=[], source_categories=[], source_tags=[],
                source_playlist=None, source_playlist_id=entry.get("playlist_id"), source_playlist_count=None, source_playlist_index=entry.get("playlist_position"),
                source_playlist_uploader=None, source_playlist_uploader_id=None, source_playlist_channel=None, source_playlist_channel_id=None, source_playlist_webpage_url=None,
                raw={},
            )
            md = apply_spotify_metadata(source_stub, spotify_result)
            name = self._basename(serial, md)
            self.songs_db.ensure_song({
                "song_key": key,
                "playlist_serial": serial,
                "spotify_track_id": spotify_result.track_id,
                "canonical_isrc": spotify_result.isrc,
                "basename": name,
                "title": md.title,
                "artist": md.artist,
                "album": md.album,
                "duration_ms": spotify_result.duration_ms,
                "pipeline_status": "SPOTIFY_METADATA_READY",
            })
            self.songs_db.upsert_metadata(key, {"normalized_title": md.title, "normalized_artist": md.artist, "normalized_album": md.album, "album_artist": md.album_artist, "release_date": md.release_date, "track_number": _parse_int(md.track_number), "disc_number": _parse_int(md.disc_number), "spotify_isrc": spotify_result.isrc, "spotify_track_id": spotify_result.track_id, "spotify_album_id": spotify_result.album_id})

            duplicate = self.songs_db.isrc_match(md.isrc, exclude=key) if self.cfg.get("duplicate_detection.enabled", True) else None
            duplicate_decision = None
            if duplicate:
                self.songs_db.record_isrc_duplicate(key, duplicate["song_key"], md.isrc, None)
                duplicate_decision = self._terminal_duplicate(entry, md, duplicate)
                self.songs_db.record_isrc_duplicate(key, duplicate["song_key"], md.isrc, duplicate_decision)
                if duplicate_decision == "keep_previous":
                    self.playlist_db.set_status(serial, "duplicate", terminal=1, reason="DUPLICATE_KEEP_PREVIOUS")
                    self.songs_db.update_song(key, pipeline_status="DUPLICATE_KEEP_PREVIOUS", terminal=1, terminal_reason="DUPLICATE_KEEP_PREVIOUS")
                    return "duplicate"

            # -------- YT Music audio source --------
            ytm_fp = stable_json_hash({
                "spotify_track_id": spotify_result.track_id,
                "spotify_duration_ms": spotify_result.duration_ms,
                "query": f"{spotify_result.track_name} {spotify_result.album_name}".strip() if spotify_result.album_name else spotify_result.track_name,
                "search_limit": int(self.cfg.get("ytmusic.search_limit", 10)),
                "duration_tolerance_ms": int(self.cfg.get("ytmusic.duration_tolerance_ms", 2000)),
                "engine": "ytmusicapi-direct-stream-v1",
            })
            ytm_mp3 = work_base / "acquisition" / "master.mp3"
            ytm_info = work_base / "acquisition" / "master.info.json"
            if state.get("ytmusic_audio", {}).get("fingerprint") == ytm_fp and ytm_mp3.exists() and ytm_info.exists():
                acq = {
                    "mp3": ytm_mp3,
                    "info_json": ytm_info,
                    "duration_ms": duration_ms(ytm_mp3),
                    "ytm_video_id": state["ytmusic_audio"]["video_id"],
                    "ytm_url": state["ytmusic_audio"]["url"],
                    "ytm_title": state["ytmusic_audio"].get("title"),
                    "ytm_query": state["ytmusic_audio"].get("query"),
                    "ytm_candidate": state["ytmusic_audio"].get("candidate"),
                    "ytm_candidates": state["ytmusic_audio"].get("candidates", []),
                    "ytm_raw": state["ytmusic_audio"].get("raw", {}),
                }
                self._record_reused(key, "ytmusic_audio", ytm_fp, ytm_mp3, sha256_file(ytm_mp3))
            else:
                active_stage_id = self._stage(key, "ytmusic_audio", ytm_fp, ytm_mp3)
                acq = acquire(self.cfg, work_base / "acquisition", spotify_result)
                self._finish(active_stage_id, "completed", acq["mp3"], sha256_file(acq["mp3"]))
                active_stage_id = None
                state["ytmusic_audio"] = {
                    "fingerprint": ytm_fp,
                    "video_id": acq["ytm_video_id"],
                    "url": acq["ytm_url"],
                    "title": acq.get("ytm_title"),
                    "query": acq.get("ytm_query"),
                    "candidate": acq.get("ytm_candidate"),
                    "candidates": acq.get("ytm_candidates", []),
                    "raw": acq.get("ytm_raw", {}),
                }
                atomic_write_json(state_path, state)

            src_hash = sha256_file(acq["mp3"])
            work = work_base / f"source_{src_hash[:16]}"
            work.mkdir(parents=True, exist_ok=True)
            full_source, full_audio_meta = prepare_full_source(Path(acq["mp3"]), work)
            source16, source16_meta = prepare_source(full_source, work)
            source_info = json.loads(Path(acq["info_json"]).read_text(encoding="utf-8"))
            source_md = normalized_from_acquisition(acq, playlist_item=entry)
            md = apply_spotify_metadata(source_md, spotify_result)
            name = self._basename(serial, md)
            self.songs_db.update_song(key, source_mp3_path=str(acq["mp3"].relative_to(self.cfg.root)), source_json_path=str(acq["info_json"].relative_to(self.cfg.root)), duration_ms=int(acq["duration_ms"]), title=md.title, artist=md.artist, album=md.album, source_mp3_sha256=src_hash, source_json_sha256=sha256_file(acq["info_json"]), pipeline_status="SOURCE_READY")
            self.songs_db.upsert_source(key, {
                "ytm_search_query": acq.get("ytm_query"),
                "ytm_selected_result_index": (acq.get("ytm_candidate") or {}).get("index"),
                "ytm_selected_result_json": json.dumps(acq.get("ytm_candidate") or {}, ensure_ascii=False, default=str),
                "ytm_player_metadata_json": json.dumps((acq.get("ytm_raw") or {}).get("ytmusic_player_metadata") or {}, ensure_ascii=False, default=str),
                "ytm_stream_format_json": json.dumps(((source_info.get("stream") or {})), ensure_ascii=False),
                "ytm_source_info_json": json.dumps(source_info, ensure_ascii=False, default=str),
                "ytm_audio_path": str(acq["mp3"]),
                "ytm_audio_sha256": src_hash,
            })

            # -------- Synced line LRC --------
            lyrics_cfg = self.cfg.get("lyrics", {}) or {}
            lrc_client = LRCLIBClient(lyrics_cfg)
            lyrics_fp = stable_json_hash({
                "title": md.title,
                "artist": md.artist,
                "album": md.album,
                "duration_ms": int(acq["duration_ms"]),
                "provider": "lrclib",
                "endpoint": "https://lrclib.net/api/get + /api/search fallback",
                "lyrics_strategy_version": "lrclib-get-then-search-v2-telugu-script-gate-v1",
                "required_script": "Telugu",
                "telugu_script_gate": "at_least_one_U+0C00_U+0C7F_codepoint_in_complete_lrc",
            })
            lrc = work / "lyrics" / "source.lrc"
            lrc.parent.mkdir(parents=True, exist_ok=True)
            if state.get("lyrics", {}).get("fingerprint") == lyrics_fp and lrc.exists():
                lres = LyricsResult(
                    lyrics_id=None, track_name=md.title, artist_name=md.artist, album_name=md.album,
                    duration=int(round(acq["duration_ms"] / 1000)), instrumental=False,
                    synced_lyrics=lrc.read_text(encoding="utf-8"), query_track=md.title,
                    query_artist=md.artist, query_album=md.album or "", duration_delta_seconds=0.0,
                    match_method="cached_lrclib_api_get", raw={},
                )
                self._record_reused(key, "lyrics", lyrics_fp, lrc, sha256_file(lrc))
            else:
                active_stage_id = self._stage(key, "lyrics", lyrics_fp, lrc)
                lres = lrc_client.get_synced(title=md.title, artist=md.artist, album=md.album, duration_seconds=int(round(acq["duration_ms"] / 1000)))
                if not lres:
                    self._finish(active_stage_id, "completed")
                    active_stage_id = None
                    self.songs_db.upsert_lyric_source(key, {"provider": "lrclib", "synced": 0, "status": "skipped", "reason": "No synced LRC from /api/get or /api/search"})
                    self.songs_db.update_song(key, pipeline_status="SKIPPED_NO_SYNCED_LRC", quality_status="skipped", terminal=1, terminal_reason="SKIPPED_NO_SYNCED_LRC")
                    # Use current YTM source but embed Spotify catalog metadata/artwork in the skipped MP3.
                    artwork = None
                    art_path = spotify_state.get("artwork_path")
                    if art_path:
                        artwork = {"path": art_path, "mime": "image/jpeg" if str(art_path).lower().endswith(".jpg") else "image/png"}
                    self._record_skip_no_lrc(key, entry, md, acq, source_info, spotify_result, {"artwork": artwork}, name, "No synchronized lyrics returned by LRCLIB /api/get or /api/search")
                    return "skipped"
                lrc.write_text(lres.synced_lyrics, encoding="utf-8")
                self._finish(active_stage_id, "completed", lrc, sha256_file(lrc))
                active_stage_id = None
            # -------- Telugu-script LRC gate --------
            # LRCLIB's "synced" flag does not mean the lyrics are written in
            # Telugu script. Romanized/Latin-only lyrics can otherwise reach the
            # Telugu MMS/CTC aligner and fail later with misleading timing errors.
            # Apply this gate after selection (including cached selections) and
            # before any alignment, visual search, or downstream processing.
            selected_lrc_text = lrc.read_text(encoding="utf-8")
            if not contains_telugu_script(selected_lrc_text):
                reason = "SKIPPED_NO_TELUGU_SCRIPT_LRC"
                self.logger.warning(
                    "Skipping serial=%s | title=%s | selected LRC contains no Telugu-script characters",
                    serial, md.title,
                )
                self._finish(active_stage_id, "completed", lrc, sha256_file(lrc)) if active_stage_id is not None else None
                active_stage_id = None
                self.songs_db.upsert_lyric_source(key, {
                    "provider": "lrclib", "synced": 1, "raw_lrc_path": str(lrc),
                    "raw_lrc_sha256": sha256_file(lrc),
                    "line_count": sum(1 for x in selected_lrc_text.splitlines() if x.strip()),
                    "blank_marker_count": sum(1 for x in selected_lrc_text.splitlines() if re.search(r"^\[\s*\]\s*$", x)),
                    "status": "skipped", "reason": reason, "script_required": "Telugu",
                })
                self.songs_db.update_song(
                    key, pipeline_status=reason, quality_status="skipped", terminal=1, terminal_reason=reason
                )
                artwork = None
                art_path = spotify_state.get("artwork_path")
                if art_path:
                    artwork = {"path": art_path, "mime": "image/jpeg" if str(art_path).lower().endswith(".jpg") else "image/png"}
                self._record_skip_no_lrc(
                    key, entry, md, acq, source_info, spotify_result, {"artwork": artwork}, name,
                    "Selected synchronized LRC contains no Telugu-script characters (U+0C00-U+0C7F)"
                )
                return "skipped"

            self.songs_db.upsert_lyric_source(key, {
                "provider": "lrclib", "synced": 1, "raw_lrc_path": str(lrc), "raw_lrc_sha256": sha256_file(lrc),
                "line_count": sum(1 for x in lres.synced_lyrics.splitlines() if x.strip()),
                "blank_marker_count": sum(1 for x in lres.synced_lyrics.splitlines() if re.search(r"^\[\s*\]\s*$", x)),
                "status": "synced", "fetched_at": "__CURRENT_TIMESTAMP__",
            })
            state["lyrics"] = {"fingerprint": lyrics_fp, "path": str(lrc), "sha256": sha256_file(lrc)}
            atomic_write_json(state_path, state)


            # -------- Resolve YouTube visual source ONCE + global 30 s offset --------
            # The visual search is intentionally performed exactly once per song.
            # After this point we reuse the persisted exact URL; hook selection must
            # never trigger another YouTube search.
            visual_fp = stable_json_hash({
                "title": md.title,
                "album": md.album,
                "search_limit": int(self.cfg.get("youtube_video_search.results_to_fetch", 20)),
                "selection_rule": "top_result_non_lyrics_title_title_album_video_song_hd_no_duration",
            })
            selected_json = work / "youtube" / "selected_video.json"
            if state.get("youtube", {}).get("fingerprint") == visual_fp and selected_json.exists():
                ystate = state["youtube"]
                video = type("CachedVideo", (), {
                    "video_id": ystate["video_id"],
                    "video_url": ystate["video_url"],
                    "title": ystate.get("title"),
                })()
                query = ystate.get("query")
                self._record_reused(key, "youtube_selection", visual_fp, selected_json, sha256_file(selected_json))
            else:
                active_stage_id = self._stage(key, "youtube_selection", visual_fp, selected_json)
                query, video = select_video(self.cfg, md.title, md.album, spotify_result.duration_ms, work, md.artist)
                self.logger.info("YOUTUBE VISUAL SELECTED ONCE | query=%s | video_id=%s | url=%s", query, video.video_id, video.video_url)
                self._finish(active_stage_id, "completed", selected_json, sha256_file(selected_json))
                active_stage_id = None
                state["youtube"] = {"fingerprint": visual_fp, "query": query, "video_id": video.video_id, "video_url": video.video_url, "title": video.title}
                atomic_write_json(state_path, state)
            ref_fp = stable_json_hash({
                "source_sha256": src_hash,
                "visual_video_id": video.video_id,
                "reference_duration_ms": 30000,
                "max_offset_ms": 30000,
                "scan_step_ms": int(self.cfg.get("youtube_offset.scan_step_ms", 250)),
                "method_version": self.cfg.get("youtube_offset.method_version", "unified-offset-v1"),
            })
            # Download/reference-sync the exact selected URL. This is NOT a new search.
            # Never rebuild a YouTube query here and never select a different video for the hook.
            selected_video_url = str(video.video_url)
            if not selected_video_url.startswith("https://www.youtube.com/watch?v="):
                raise RuntimeError(f"YOUTUBE_SELECTED_URL_INVALID: {selected_video_url}")
            ref_wav = work / "youtube" / "reference_audio.wav"
            if state.get("youtube_offset", {}).get("fingerprint") == ref_fp and ref_wav.exists():
                offset = state["youtube_offset"]["result"]
                candidates = state["youtube_offset"].get("candidates", [])
                self._record_reused(key, "youtube_offset", ref_fp, ref_wav, sha256_file(ref_wav))
            else:
                active_stage_id = self._stage(key, "youtube_offset", ref_fp, ref_wav)
                ref_wav = download_audio(self.cfg, video.video_id, work, source_url=selected_video_url)
                offset, candidates = find_offset(source16, ref_wav)
                self._finish(active_stage_id, "completed", ref_wav, sha256_file(ref_wav))
                active_stage_id = None
                state["youtube_offset"] = {"fingerprint": ref_fp, "result": offset, "candidates": candidates, "reference_audio": str(ref_wav)}
                atomic_write_json(state_path, state)
            self.songs_db.add_youtube_candidates(key, candidates)
            self.songs_db.upsert_youtube(key, {
                "video_id": video.video_id,
                "video_url": video.video_url,
                "video_title": video.title,
                "selection_query": query,
                "selection_rule": "top_result_non_lyrics_title_title_album_video_song_hd_no_duration",
                "audio_path": str(ref_wav),
                "audio_sha256": sha256_file(ref_wav),
                "offset_ms": offset["candidate_offset_ms"],
                "offset_seconds": offset["candidate_offset_ms"] / 1000,
                "match_score": offset["total_score"],
                "waveform_score": offset["waveform_score"],
                "energy_score": offset["energy_score"],
                "peak_valley_score": offset["peak_valley_score"],
                "transition_score": offset["transition_score"],
                "reference_duration_ms": 30000,
                "max_accepted_offset_ms": 30000,
                "method_version": self.cfg.get("youtube_offset.method_version", "unified-offset-v1"),
            })

            shutil.copy2(acq["mp3"], work / "source.mp3")
            shutil.copy2(acq["info_json"], work / "source.json")

            # -------- Shared Demucs --------
            dem_fp = stable_json_hash({
                "source_mp3_sha256": src_hash,
                "full_source_wav_sha256": full_audio_meta["sha256"],
                "model": self.cfg.get("models.demucs_model", self.cfg.get("demucs.model", "htdemucs")),
                "requested_device": self.cfg.get("runtime.device", "cuda"),
                "cuda_available": cuda_available(),
                "segment_seconds": self.cfg.get("demucs.segment_seconds", 8),
            })
            dem_stage = self._stage(key, "demucs", dem_fp)
            dem = DemucsRunner(self.cfg.data, work, self.logger, model_cache_dir=self.cfg.path("paths.models_dir")).run(full_source)
            dem_run_id = self.songs_db.upsert_demucs(key, {
                "run_id": dem.get("run_id"), "model_name": dem["model"], "model_revision": None, "device": dem["device"],
                "input_sha256": sha256_file(full_source), "duration_ms": acq["duration_ms"], "vocals_path": dem["vocals"],
                "drums_path": dem["drums"], "bass_path": dem["bass"], "other_path": dem["other"],
                "vocals_sha256": sha256_file(dem["vocals"]), "drums_sha256": sha256_file(dem["drums"]), "bass_sha256": sha256_file(dem["bass"]),
                "other_sha256": sha256_file(dem["other"]), "status": "completed",
            })
            self._finish(dem_stage, "completed", dem["other"], sha256_file(dem["other"]))
            active_stage_id = None

            # -------- Word-level alignment --------
            align_fp = stable_json_hash({
                "source_mp3_sha256": src_hash,
                "lrc_sha256": sha256_file(lrc),
                "demucs_stems": {k: sha256_file(dem[k]) for k in ("vocals", "drums", "bass", "other")},
                "full_source_wav_sha256": full_audio_meta["sha256"],
                "mms_model": self.cfg.get("models.mms_model", "facebook/mms-1b-all"),
                "mms_revision": self.cfg.get("models.mms_revision"),
                "runtime_device": self.cfg.get("runtime.device", "cuda"),
                "cuda_available": cuda_available(),
                "config_hash": self.cfg.config_hash,
            })
            aligned_json = work / "alignment" / "final" / "master.json"
            aligned_lrc = work / "alignment" / "final" / "master.lrc"
            aligned_mp3 = work / "alignment" / "final" / "master.mp3"
            cached_align = self._cached_stage(key, "alignment", align_fp, [aligned_json, aligned_lrc, aligned_mp3])
            if state.get("alignment", {}).get("fingerprint") == align_fp and cached_align:
                aligned = {"json": aligned_json, "lrc": aligned_lrc, "mp3": aligned_mp3}
                p2j = json.loads(aligned_json.read_text(encoding="utf-8"))
                words = self._words_from_p2(p2j)
                alignment_id = state["alignment"].get("run_id")
                if not alignment_id:
                    alignment_id = self.songs_db.upsert_alignment(key, {
                        "input_mp3_sha256": src_hash, "input_lrc_sha256": sha256_file(lrc), "demucs_run_id": dem_run_id,
                        "model_name": self.cfg.get("models.mms_model", "facebook/mms-1b-all"), "model_revision": self.cfg.get("models.mms_revision"),
                        "language": "tel", "normalizer_version": "1.1.0", "alignment_method": "MMS frame emissions + CTC reference forced alignment",
                        "config_hash": self.cfg.config_hash, "quality_status": p2j.get("phase2", {}).get("quality_status"), "status": "completed",
                        "canonical_timeline_path": str(work / "alignment" / "canonical_timeline.json"), "wordlevel_lrc_path": str(aligned_lrc),
                    })
                phase2_alignment = p2j.get("phase2", {}).get("alignment", {}) or {}
                self.songs_db.set_lyrics_words(key, alignment_id, phase2_alignment.get("lines", []) or [], words)
                self._record_reused(key, "alignment", align_fp, aligned_lrc, sha256_file(aligned_lrc))
            else:
                active_stage_id = self._stage(key, "alignment", align_fp, aligned_lrc)
                aligned = run_alignment(self.cfg, work, Path(acq["mp3"]), lrc, Path(acq["info_json"]), source16, Path(dem["vocals"]), self.logger)
                p2j = json.loads(aligned["json"].read_text(encoding="utf-8"))
                words = self._words_from_p2(p2j)
                phase2_alignment = p2j.get("phase2", {}).get("alignment", {}) or {}
                alignment_id = self.songs_db.upsert_alignment(key, {
                    "input_mp3_sha256": src_hash, "input_lrc_sha256": sha256_file(lrc), "demucs_run_id": dem_run_id,
                    "model_name": self.cfg.get("models.mms_model", "facebook/mms-1b-all"), "model_revision": self.cfg.get("models.mms_revision"),
                    "language": "tel", "normalizer_version": "1.1.0", "alignment_method": "MMS frame emissions + CTC reference forced alignment",
                    "config_hash": self.cfg.config_hash, "quality_status": p2j.get("phase2", {}).get("quality_status"), "status": "completed",
                    "canonical_timeline_path": str(work / "alignment" / "canonical_timeline.json"), "wordlevel_lrc_path": str(aligned["lrc"]),
                })
                self.songs_db.set_lyrics_words(key, alignment_id, phase2_alignment.get("lines", []) or [], words)
                self._finish(active_stage_id, "completed", aligned["lrc"], sha256_file(aligned["lrc"]))
                active_stage_id = None
            word_lrc = Path(aligned["lrc"])
            state["alignment"] = {"fingerprint": align_fp, "run_id": alignment_id, "wordlevel_lrc": str(word_lrc), "json": str(aligned["json"]), "mp3": str(aligned["mp3"]), "word_count": len(words)}
            atomic_write_json(state_path, state)

            # -------- Whole-song 8D --------
            eightd_fp = stable_json_hash({
                "source_mp3_sha256": src_hash,
                "demucs_stems": {k: sha256_file(dem[k]) for k in ("vocals", "drums", "bass", "other")},
                "wordlevel_lrc_sha256": sha256_file(word_lrc),
                "lrc_sha256": sha256_file(lrc), "alignment_run_id": alignment_id,
                "config_hash": self.cfg.config_hash, "engine_version": "unified-8d-v2",
            })
            eightd_path = work / "audio" / "8d" / "SongName_8D.mp3"
            eightd_manifest = work / "audio" / "8d" / "manifest.json"
            cached_8d = self._cached_stage(key, "audio_8d", eightd_fp, [eightd_path, eightd_manifest])
            if state.get("8d", {}).get("fingerprint") == eightd_fp and cached_8d:
                eightd = Path(state["8d"]["path"])
                manifest = json.loads(eightd_manifest.read_text(encoding="utf-8"))
                self._record_reused(key, "audio_8d", eightd_fp, eightd, sha256_file(eightd))
            else:
                active_stage_id = self._stage(key, "audio_8d", eightd_fp, eightd_path)
                eightd_cfg = dict(self.cfg.data)
                eightd_cfg["_config_hash"] = self.cfg.config_hash
                eightd, manifest = render_8d(eightd_cfg, work, dem, acq["duration_ms"], words, Path(acq["mp3"]))
                self._finish(active_stage_id, "completed", eightd, sha256_file(eightd))
                active_stage_id = None
            state["8d"] = {"fingerprint": eightd_fp, "path": str(eightd), "sha256": sha256_file(eightd), "manifest": manifest}
            atomic_write_json(state_path, state)
            self.songs_db.update_song(key, pipeline_status="AUDIO_8D_READY", final_8d_mp3_path=str(eightd))
            self.songs_db.upsert_8d(key, {
                "input_mp3_sha256": src_hash, "demucs_run_id": dem_run_id, "alignment_run_id": alignment_id,
                "config_hash": self.cfg.config_hash, "engine_version": manifest["engine_version"], "duration_ms": acq["duration_ms"],
                "device": dem["device"], "output_path": str(eightd), "output_sha256": sha256_file(eightd), "status": "completed",
                "validation_status": "passed", "manifest_path": str(eightd_manifest),
            })

            # -------- Pre-hook final package promotion --------
            # Persist song + whole-song 8D + synced LRC + word-level LRC + JSON before hook selection.
            artwork = None
            art_path = spotify_state.get("artwork_path")
            if art_path:
                mime = "image/png" if str(art_path).lower().endswith(".png") else "image/webp" if str(art_path).lower().endswith(".webp") else "image/jpeg"
                artwork = {"path": art_path, "mime": mime}
            final_dir = self.cfg.final_dir
            final_dir.mkdir(parents=True, exist_ok=True)
            final_lrc = final_dir / f"{name}.lrc"
            final_wlrc = final_dir / f"{name}_wordlevel.lrc"
            final_mp3 = final_dir / f"{name}.mp3"
            final_8d = final_dir / f"{name}_8D.mp3"
            final_json = final_dir / f"{name}.json"
            embed(aligned["mp3"], final_mp3, md, artwork, words, {"SPOTIFY_TRACK_ID": spotify_result.track_id, "SPOTIFY_ALBUM_ID": spotify_result.album_id, "SPOTIFY_ISRC": spotify_result.isrc, "SPOTIFY_TRACK_URL": spotify_result.track_url, "SPOTIFY_TRACK_URI": spotify_result.uri, "SPOTIFY_ALBUM_URL": spotify_result.album_url, "SPOTIFY_ALBUM_TYPE": spotify_result.album_type, "SPOTIFY_RELEASE_DATE": spotify_result.album_release_date, "SPOTIFY_RELEASE_PRECISION": spotify_result.album_release_precision, "SPOTIFY_ALBUM_TOTAL_TRACKS": spotify_result.album_total_tracks, "SPOTIFY_EXPLICIT": spotify_result.explicit, "SPOTIFY_LABEL": spotify_result.album_label, "SPOTIFY_COPYRIGHTS": " | ".join(spotify_result.album_copyrights) if spotify_result.album_copyrights else None, "SPOTIFY_ARTWORK_URL": spotify_result.album_artwork_url, "SPOTIFY_ARTWORK_SHA256": spotify_state.get("artwork_sha256"), "SPOTIFY_ARTIST_IDS": ",".join(spotify_result.artist_ids), "SPOTIFY_ALBUM_ARTIST_IDS": ",".join(spotify_result.album_artist_ids), "UNIFIED_YTM_AUDIO_VIDEO_ID": acq["ytm_video_id"], "UNIFIED_YTM_AUDIO_URL": acq["ytm_url"], "UNIFIED_YTM_AUDIO_QUERY": acq["ytm_query"], "UNIFIED_YT_VISUAL_VIDEO_ID": video.video_id, "UNIFIED_YT_VISUAL_VIDEO_URL": video.video_url, "UNIFIED_YT_OFFSET_MS": offset["candidate_offset_ms"], "UNIFIED_8D_SOURCE_SHA256": sha256_file(eightd), "UNIFIED_WORDLEVEL_LRC_SHA256": sha256_file(word_lrc)})
            shutil.copy2(lrc, final_lrc)
            shutil.copy2(word_lrc, final_wlrc)
            shutil.copy2(eightd, final_8d)
            pre = {"unified_pipeline": {"status": "PRE_HOOK_READY", "outputs": {"mp3": f"songs/final/{name}.mp3", "lrc": f"songs/final/{name}.lrc", "wordlevel_lrc": f"songs/final/{name}_wordlevel.lrc", "json": f"songs/final/{name}.json", "8d_mp3": f"songs/final/{name}_8D.mp3"}, "hashes": {"mp3": sha256_file(final_mp3), "lrc": sha256_file(final_lrc), "wordlevel_lrc": sha256_file(final_wlrc), "8d_mp3": sha256_file(final_8d)}}}
            write_json(final_json, pre)
            self.songs_db.update_song(key, pipeline_status="AUDIO_8D_READY", final_mp3_path=str(final_mp3.relative_to(self.cfg.root)), final_lrc_path=str(final_lrc.relative_to(self.cfg.root)), final_wordlevel_lrc_path=str(final_wlrc.relative_to(self.cfg.root)), final_json_path=str(final_json.relative_to(self.cfg.root)), final_8d_mp3_path=str(final_8d.relative_to(self.cfg.root)))
            eightd = final_8d

            # -------- Optional hook deferral --------
            if bool(self.cfg.get("hook.skip", False)):
                queue_entry = {
                    "status": "pending",
                    "song_name": name,
                    "song_key": key,
                    "playlist_serial": serial,
                    "song_path": str(final_mp3.relative_to(self.cfg.root)),
                    "mp3_path": str(final_mp3.relative_to(self.cfg.root)),
                    "lrc_path": str(final_lrc.relative_to(self.cfg.root)),
                    "wordlevel_lrc_path": str(final_wlrc.relative_to(self.cfg.root)),
                    "eightd_path": str(final_8d.relative_to(self.cfg.root)),
                    "json_path": str(final_json.relative_to(self.cfg.root)),
                    "work_dir": str(work.relative_to(self.cfg.root)),
                    "duration_ms": int(acq["duration_ms"]),
                    "hook_start_time": None, "hook_end_time": None,
                    "hook_start_ms": None, "hook_end_ms": None,
                    "youtube_video_id": video.video_id,
                    "youtube_video_url": video.video_url,
                    "youtube_offset_ms": int(offset["candidate_offset_ms"]),
                    "ytmusic_audio_video_id": acq.get("ytm_video_id"),
                    "ytmusic_audio_url": acq.get("ytm_url"),
                    "created_at": time.time(),
                    "updated_at": time.time(),
                }
                self._upsert_hook_queue_entry(queue_entry)
                state["hook"] = {"status": "pending", "start_ms": None, "end_ms": None, "queue_file": str(self._hook_queue_path().relative_to(self.cfg.root))}
                atomic_write_json(state_path, state)
                self.songs_db.update_song(key, pipeline_status="HOOK_PENDING", terminal=0, terminal_reason="HOOK_SELECTION_DEFERRED")
                self.playlist_db.set_status(serial, "pending", terminal=0, reason="HOOK_SELECTION_DEFERRED")
                self.logger.info("Song serial=%s pre-hook package ready; hook deferred to %s", serial, self._hook_queue_path())
                return "hook_pending"

            # -------- Manual hook --------
            reel_key = f"{key}_reel_v1"
            existing_hook = self.reels_db.one("SELECT * FROM reel_hooks WHERE reel_key=?", (reel_key,))
            state_hook = {"hook_start_ms": existing_hook["start_ms"], "hook_end_ms": existing_hook["end_ms"]} if existing_hook else state.get("hook", {})
            hs, he = prompt_or_existing(state_hook)
            from .hook import validate as validate_hook
            validate_hook(hs, he, int(acq["duration_ms"]))
            self.reels_db.ensure(reel_key, key)
            self.reels_db.upsert("reel_hooks", "reel_key", reel_key, {"start_ms": hs, "end_ms": he, "duration_ms": he - hs, "input_method": "terminal"})
            self.reels_db.update(reel_key, pipeline_status="HOOK_READY", hook_start_ms=hs, hook_end_ms=he, hook_duration_ms=he-hs, eight_d_source_path=str(eightd), eight_d_source_sha256=sha256_file(eightd), eight_d_whole_song_path=str(eightd), eight_d_whole_song_sha256=sha256_file(eightd))
            state["hook"] = {"start_ms": hs, "end_ms": he}
            atomic_write_json(state_path, state)
            self._upsert_hook_queue_entry({
                "status": "building", "song_name": name, "song_key": key, "playlist_serial": serial,
                "song_path": str(final_mp3.relative_to(self.cfg.root)), "mp3_path": str(final_mp3.relative_to(self.cfg.root)),
                "lrc_path": str(final_lrc.relative_to(self.cfg.root)), "wordlevel_lrc_path": str(final_wlrc.relative_to(self.cfg.root)),
                "eightd_path": str(final_8d.relative_to(self.cfg.root)), "json_path": str(final_json.relative_to(self.cfg.root)),
                "work_dir": str(work.relative_to(self.cfg.root)), "duration_ms": int(acq["duration_ms"]),
                "hook_start_time": fmt_ms(hs), "hook_end_time": fmt_ms(he), "hook_start_ms": hs, "hook_end_ms": he,
                "youtube_video_id": video.video_id, "youtube_video_url": video.video_url,
                "youtube_offset_ms": int(offset["candidate_offset_ms"]), "ytmusic_audio_video_id": acq.get("ytm_video_id"), "ytmusic_audio_url": acq.get("ytm_url"),
                "updated_at": time.time(),
            })

            # -------- Reel --------
            reel_fp = stable_json_hash({
                "song_key": key, "hook_start_ms": hs, "hook_end_ms": he,
                "video_id": video.video_id, "offset_ms": offset["candidate_offset_ms"],
                "word_lrc_sha256": sha256_file(word_lrc), "eightd_sha256": sha256_file(eightd), "config_hash": self.cfg.config_hash,
            })
            active_stage_id = self._stage(key, "reel", reel_fp)
            song_record = {"song_key": key, "basename": name, "duration_ms": acq["duration_ms"], "eight_d_path": str(eightd)}
            rf, rj, rmeta = build_reel(self.cfg, work, song_record, hs, he, video.video_url, offset["candidate_offset_ms"], word_lrc, words, self.logger)
            self._finish(active_stage_id, "completed", rf, sha256_file(rf))
            active_stage_id = None

            rv = json.loads((work / "validation" / "reel_validation.json").read_text(encoding="utf-8"))
            self.reels_db.upsert("reel_youtube_sync", "reel_key", reel_key, {
                "video_id": video.video_id, "video_url": video.video_url, "video_offset_ms": offset["candidate_offset_ms"],
                "mapped_start_ms": hs + offset["candidate_offset_ms"], "mapped_end_ms": he + offset["candidate_offset_ms"],
                "guard_before_ms": int(self.cfg.get("video_match.guard_before_ms", 1500)), "guard_after_ms": int(self.cfg.get("video_match.guard_after_ms", 1500)), "status": "validated",
            })
            self.reels_db.upsert("reel_audio", "reel_key", reel_key, {
                "source_8d_path": str(eightd), "source_8d_sha256": sha256_file(eightd), "crop_start_ms": hs, "crop_end_ms": he,
                "crop_duration_ms": he-hs, "output_path": str(work / "audio" / "hook_8d.mp3"), "output_sha256": sha256_file(work / "audio" / "hook_8d.mp3"), "status": "validated",
            })
            self.reels_db.upsert("reel_video", "reel_key", reel_key, {
                "video_source_id": video.video_id, "source_url": video.video_url, "source_start_ms": hs + offset["candidate_offset_ms"],
                "source_end_ms": he + offset["candidate_offset_ms"], "guard_before_ms": int(self.cfg.get("video_match.guard_before_ms", 1500)),
                "guard_after_ms": int(self.cfg.get("video_match.guard_after_ms", 1500)), "trim_start_ms": min(int(self.cfg.get("video_match.guard_before_ms", 1500)), hs + offset["candidate_offset_ms"]),
                "requested_duration_ms": he-hs, "actual_duration_ms": rv.get("probe", {}).get("duration_ms"), "width": 1080, "height": 1920,
                "panel_height_px": int(1920 * 0.8), "panel_height_percent": 80.0, "encoder": rv.get("probe", {}).get("video_codec"), "output_path": str(rf), "status": "validated",
            })
            self.reels_db.upsert("reel_lyrics", "reel_key", reel_key, {
                "wordlevel_lrc_path": str(word_lrc), "wordlevel_lrc_sha256": sha256_file(word_lrc),
                "font_name": "Baloo Tammudu 2 ExtraBold", "font_path": str(self.cfg.get("lyrics.font_path") or (self.cfg.fonts_dir / self.cfg.get("lyrics.font_filename", "BalooTammudu2-ExtraBold.ttf"))),
                "placement": "center", "line_behavior": "one_line_at_a_time", "word_highlight_enabled": 1, "status": "validated",
            })
            self.reels_db.upsert("reel_validation", "reel_key", reel_key, {
                "overall": int(bool(rv["overall"])), "failed_checks_json": json.dumps(rv.get("failed_checks", []), ensure_ascii=False),
                "output_exists": int(rv["checks"].get("exists", False)), "output_duration_matches": int(rv["checks"].get("duration_match", False)),
                "resolution_valid": int(rv["checks"].get("resolution_valid", False)), "aspect_ratio_valid": int(rv["checks"].get("aspect_ratio_valid", False)),
                "audio_stereo": int(rv["checks"].get("audio_stereo", False)), "codec_valid": int(str(rv.get("probe", {}).get("video_codec", "")).lower().startswith("h264")),
                "created_at": "__CURRENT_TIMESTAMP__",
            })

            # -------- Final metadata, hashes, and final JSON refresh --------
            final_dir = self.cfg.final_dir
            final_dir.mkdir(parents=True, exist_ok=True)
            final_lrc = final_dir / f"{name}.lrc"
            final_wlrc = final_dir / f"{name}_wordlevel.lrc"
            final_mp3 = final_dir / f"{name}.mp3"
            final_8d = final_dir / f"{name}_8D.mp3"
            final_json = final_dir / f"{name}.json"

            final_json_obj = build_json(
                source_info,
                playlist={
                    "provider": "spotify", "playlist_id": entry.get("playlist_id"), "playlist_position": entry.get("playlist_position"),
                    "playlist_serial": serial, "added_at": entry.get("added_at"), "snapshot_id": entry.get("playlist_snapshot_id"),
                },
                spotify={"track": SpotifyClient.to_json(spotify_result), "artwork": artwork, "metadata_source": "spotify_full_track_album_artist_api"},
                metadata=self._metadata_dict(md),
                ytmusic_audio={
                    "query": acq["ytm_query"], "selected_video_id": acq["ytm_video_id"], "selected_url": acq["ytm_url"],
                    "candidate": acq["ytm_candidate"], "candidates_considered": acq.get("ytm_candidates", []),
                    "selection_rule": "first result matching Spotify duration within tolerance", "spotify_duration_ms": spotify_result.duration_ms,
                    "actual_download_duration_ms": acq["duration_ms"],
                },
                youtube={
                    "visual_video": {"video_id": video.video_id, "url": video.video_url, "title": video.title, "query": query, "selection_rule": "first non-lyrics title result from title + album + video song hd; no duration", "selection_count": 1, "search_repeated_before_hook": False},
                    "offset": offset, "offset_candidates": candidates,
                },
                lyrics={"provider": "lrclib", "synced": True, "source_lrc": str(lrc), "wordlevel_lrc": str(word_lrc), "provider_record": lres.raw},
                demucs=dem,
                alignment=p2j.get("phase2", {}),
                audio_8d=manifest,
                hook={"start_ms": hs, "end_ms": he, "duration_ms": he-hs, "input_method": "terminal"},
                reel=rmeta,
                duplicate_detection={"detected": bool(duplicate), "resolution": duplicate_decision, "previous_song_key": duplicate.get("song_key") if duplicate else None, "identifier": "ISRC"},
                provenance={
                    "source_mp3_sha256": src_hash,
                    "source_workdir": str(work.relative_to(self.cfg.root)),
                    "spotify_authority": "full catalog metadata + artwork",
                    "ytmusic_media_authority": "audio/video source only",
                    "spotify_audio_downloaded": False,
                    "legacy_downloader_used": False,
                },
                status="FINALIZED",
            )
            final_json_obj["unified_pipeline"]["outputs"] = {
                "mp3": f"songs/final/{name}.mp3", "lrc": f"songs/final/{name}.lrc", "wordlevel_lrc": f"songs/final/{name}_wordlevel.lrc",
                "json": f"songs/final/{name}.json", "8d_mp3": f"songs/final/{name}_8D.mp3", "reel": f"reels/generated/{name}_reel.mp4", "reel_json": f"reels/generated/{name}_reel.json",
            }
            # Promote the generated Reel into the canonical final Reel location.
            # Keep the working render immutable and make the final paths explicit before
            # validation/database updates; these were accidentally referenced before
            # definition in v1.4.22.
            reel_final = self.cfg.reels_dir / f"{name}_reel.mp4"
            reel_json_final = self.cfg.reels_dir / f"{name}_reel.json"
            reel_final.parent.mkdir(parents=True, exist_ok=True)
            reel_json_final.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(rf, reel_final)
            shutil.copy2(rj, reel_json_final)

            final_json_obj["unified_pipeline"]["hashes"] = {
                "mp3": sha256_file(final_mp3), "lrc": sha256_file(final_lrc), "wordlevel_lrc": sha256_file(final_wlrc),
                "8d_mp3": sha256_file(final_8d), "reel": sha256_file(reel_final), "reel_json": sha256_file(reel_json_final),
            }
            jsonp = final_json
            write_json(jsonp, final_json_obj)
            self._validate_package(final_mp3, final_lrc, final_wlrc, final_8d, jsonp, spotify_result.duration_ms, reel_final, reel_json_final)

            self.songs_db.update_song(
                key,
                pipeline_status="FINALIZED",
                quality_status=p2j.get("phase2", {}).get("quality_status") or "passed",
                terminal=1,
                final_mp3_path=str(final_mp3.relative_to(self.cfg.root)),
                final_lrc_path=str(final_lrc.relative_to(self.cfg.root)),
                final_wordlevel_lrc_path=str(final_wlrc.relative_to(self.cfg.root)),
                final_json_path=str(final_json.relative_to(self.cfg.root)),
                final_8d_mp3_path=str(final_8d.relative_to(self.cfg.root)),
                canonical_isrc=spotify_result.isrc,
                spotify_track_id=spotify_result.track_id,
                ytm_video_id=acq["ytm_video_id"],
                ytm_url=acq["ytm_url"],
            )
            self.reels_db.update(
                reel_key,
                pipeline_status="FINALIZED",
                terminal=1,
                final_reel_path=str(reel_final.relative_to(self.cfg.root)),
                final_reel_json_path=str(reel_json_final.relative_to(self.cfg.root)),
                final_reel_sha256=sha256_file(reel_final),
                output_duration_ms=duration_ms(reel_final),
                output_width=1080,
                output_height=1920,
                quality_status="passed",
                youtube_video_id=video.video_id,
                youtube_offset_ms=offset["candidate_offset_ms"],
                youtube_mapped_start_ms=hs + offset["candidate_offset_ms"],
                youtube_mapped_end_ms=he + offset["candidate_offset_ms"],
                eight_d_source_path=str(final_8d.relative_to(self.cfg.root)),
                eight_d_source_sha256=sha256_file(final_8d),
            )

            if duplicate and duplicate_decision == "keep_current":
                old_key = duplicate["song_key"]
                old = self.songs_db.one("SELECT * FROM songs WHERE song_key=?", (old_key,)) or {}
                self.songs_db.update_song(old_key, pipeline_status=f"REPLACED_BY:{key}", terminal=1, terminal_reason=f"REPLACED_BY:{key}")
                for field in ("final_mp3_path", "final_lrc_path", "final_wordlevel_lrc_path", "final_json_path", "final_8d_mp3_path"):
                    if old.get(field):
                        (self.cfg.root / old[field]).unlink(missing_ok=True)
                old_base = old.get("basename")
                if old_base:
                    (self.cfg.reels_dir / f"{old_base}_reel.mp4").unlink(missing_ok=True)
                    (self.cfg.reels_dir / f"{old_base}_reel.json").unlink(missing_ok=True)
                self.songs_db.audit(old_key, "song_replaced", "duplicate_keep_current", "terminal", f"Replaced by {key}")

            self.playlist_db.set_status(serial, "completed", terminal=1, reason="FINALIZED")
            self.playlist_db.audit(entry.get("playlist_id"), "song_finalized", f"{name} finalized", serial=serial)
            self._mark_hook_queue_completed(key, reel_final, reel_json_final)
            return "completed"

        except KeyboardInterrupt:
            raise
        except Exception as exc:
            if active_stage_id:
                try:
                    self._finish(active_stage_id, "error", err=str(exc))
                except Exception:
                    pass
            state["last_error"] = {"type": type(exc).__name__, "message": str(exc), "code": str(exc).split(":", 1)[0], "at": time.time()}
            atomic_write_json(state_path, state)
            code = str(exc).split(":", 1)[0]
            self.logger.exception("Song serial=%s failed", serial)
            self.songs_db.audit(key, "song_failed", "pipeline", "error", f"{type(exc).__name__}: {exc}", code)
            self.songs_db.update_song(key, pipeline_status="error", quality_status="failed", terminal=0, terminal_reason=None)
            self.playlist_db.set_status(serial, "error", error_code=code, error_message=str(exc), terminal=0, reason=code)
            return "error"

    @staticmethod
    def _validate_package(mp3, lrc, word_lrc, eightd, json_path, expected_duration_ms, reel, reel_json):
        for path in (mp3, lrc, word_lrc, eightd, json_path, reel, reel_json):
            p = Path(path)
            if not p.exists() or p.stat().st_size == 0:
                raise RuntimeError(f"FINAL_VALIDATION_FAILED: missing/empty {p}")
        for path in (mp3, eightd):
            actual = duration_ms(path)
            if abs(actual - int(expected_duration_ms)) > 500:
                raise RuntimeError(f"FINAL_VALIDATION_FAILED: duration mismatch {path}: {actual} vs {expected_duration_ms}")
        data = json.loads(Path(json_path).read_text(encoding="utf-8"))
        hashes = data.get("unified_pipeline", {}).get("hashes", {})
        mapping = {
            "mp3": mp3,
            "lrc": lrc,
            "wordlevel_lrc": word_lrc,
            "8d_mp3": eightd,
            "reel": reel,
            "reel_json": reel_json,
        }
        for key, path in mapping.items():
            if hashes.get(key) != sha256_file(path):
                raise RuntimeError(f"FINAL_VALIDATION_FAILED: hash mismatch for {key}")


def _parse_int(value):
    try:
        return int(str(value).split("/", 1)[0]) if value is not None else None
    except Exception:
        return None
