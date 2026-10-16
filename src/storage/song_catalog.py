"""Generate a clean, human-readable catalog export from the operational database.

SQLite remains authoritative. This file is a derived export and must never be edited
as an operational data source.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _relative(value: Any, root: Path) -> str | None:
    if value in (None, ""):
        return None
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        candidate = (root / path).resolve()
        # Keep relative catalog paths relative to the project root.
        try:
            return candidate.relative_to(root.resolve()).as_posix()
        except ValueError:
            return str(candidate)
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def sync_songs_json(cfg, playlist_db, songs_db, reels_db=None) -> Path:
    """Atomically regenerate db/songs.json from SQLite rows.

    Catalog data intentionally excludes raw provider payloads, stack traces, search
    candidate dumps, and other debugging noise. Full per-song provenance belongs in
    songs/final/<song>.json.
    """
    root = Path(cfg.root).resolve()
    out = Path(cfg.db_dir) / "songs.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    pid = str(cfg.get("playlist.spotify_playlist_id") or "").strip()

    with playlist_db.connect() as conn:
        if pid:
            entries = [dict(r) for r in conn.execute(
                "SELECT * FROM playlist_entries WHERE playlist_id=? ORDER BY playlist_position,playlist_serial", (pid,)
            ).fetchall()]
        else:
            entries = [dict(r) for r in conn.execute(
                "SELECT * FROM playlist_entries ORDER BY playlist_id,playlist_position,playlist_serial"
            ).fetchall()]
        playlists = {str(r["playlist_id"]): dict(r) for r in conn.execute("SELECT * FROM playlists").fetchall()}

    with songs_db.connect() as conn:
        song_rows = {str(r["song_key"]): dict(r) for r in conn.execute("SELECT * FROM songs").fetchall()}
        metadata_rows = {str(r["song_key"]): dict(r) for r in conn.execute("SELECT * FROM metadata").fetchall()}
        source_rows = {str(r["song_key"]): dict(r) for r in conn.execute("SELECT * FROM source_metadata").fetchall()}
        lyric_rows = {str(r["song_key"]): dict(r) for r in conn.execute("SELECT * FROM lyric_sources").fetchall()}
        youtube_rows = {str(r["song_key"]): dict(r) for r in conn.execute("SELECT * FROM youtube_matches").fetchall()}
        ytm_rows = {str(r["song_key"]): dict(r) for r in conn.execute("SELECT * FROM ytmusic_audio_matches").fetchall()}
    reel_rows = {}
    if reels_db is not None:
        try:
            with reels_db.connect() as conn:
                reel_rows = {str(r["song_key"]): dict(r) for r in conn.execute(
                    "SELECT r.* FROM reels r JOIN (SELECT song_key,MAX(updated_at) updated_at FROM reels GROUP BY song_key) x "
                    "ON r.song_key=x.song_key AND r.updated_at=x.updated_at"
                ).fetchall()}
        except Exception:
            reel_rows = {}

    catalog: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        serial = int(entry.get("playlist_serial") or 0)
        track_id = str(entry.get("spotify_track_id") or "unavailable")
        key = f"{serial}_{track_id}"
        song = song_rows.get(key, {})
        meta = metadata_rows.get(key, {})
        src = source_rows.get(key, {})
        lyrics = lyric_rows.get(key, {})
        yt = youtube_rows.get(key, {})
        ytm = ytm_rows.get(key, {})
        reel = reel_rows.get(key, {})
        paths = {
            "source_audio": _relative(song.get("source_mp3_path") or src.get("ytm_audio_path"), root),
            "source_metadata": _relative(song.get("source_json_path"), root),
            "synced_lrc": _relative(song.get("final_lrc_path") or lyrics.get("raw_lrc_path"), root),
            "wordlevel_lrc": _relative(song.get("final_wordlevel_lrc_path"), root),
            "song_audio": _relative(song.get("final_mp3_path"), root),
            "audio_8d": _relative(song.get("final_8d_mp3_path"), root),
            "song_json": _relative(song.get("final_json_path"), root),
            "youtube_reference_audio": _relative(yt.get("audio_path"), root),
            "reel_video": _relative(reel.get("final_reel_path"), root),
            "reel_json": _relative(reel.get("final_reel_json_path"), root),
        }
        record = {
            "song_key": key,
            "spotify": {
                "track_id": entry.get("spotify_track_id"),
                "track_url": entry.get("spotify_url") or meta.get("spotify_track_url"),
                "album_id": meta.get("spotify_album_id"),
                "isrc": entry.get("spotify_isrc") or meta.get("spotify_isrc") or song.get("canonical_isrc"),
            },
            "title": song.get("title") or meta.get("normalized_title") or entry.get("title"),
            "artists": song.get("artist") or meta.get("normalized_artist") or entry.get("artist"),
            "album": song.get("album") or meta.get("normalized_album") or entry.get("album"),
            "duration_ms": song.get("duration_ms") or entry.get("spotify_duration_ms"),
            "details": {
                "album_artist": meta.get("album_artist"),
                "release_date": meta.get("release_date"),
                "track_number": meta.get("track_number"),
                "disc_number": meta.get("disc_number"),
                "isrc": entry.get("spotify_isrc") or meta.get("spotify_isrc") or song.get("canonical_isrc"),
                "spotify_album_id": meta.get("spotify_album_id"),
                "spotify_track_uri": meta.get("spotify_track_uri"),
                "explicit": meta.get("explicit"),
                "album_type": meta.get("album_type"),
                "album_release_precision": meta.get("album_release_precision"),
                "album_total_tracks": meta.get("album_total_tracks"),
                "album_label": meta.get("album_label"),
            },
            "playlist": {
                "id": entry.get("playlist_id"),
                "name": (playlists.get(str(entry.get("playlist_id")), {}) or {}).get("display_name"),
                "position": entry.get("playlist_position"),
                "serial": serial,
                "added_at": entry.get("added_at"),
            },
            "status": {
                "playlist": entry.get("status"),
                "pipeline": song.get("pipeline_status") or "pending",
                "quality": song.get("quality_status"),
                "terminal": bool(song.get("terminal", entry.get("terminal", 0))),
                "terminal_reason": song.get("terminal_reason") or entry.get("terminal_reason"),
            },
            "sources": {
                "ytmusic_audio_video_id": song.get("ytm_video_id") or ytm.get("video_id"),
                "ytmusic_audio_url": song.get("ytm_url") or ytm.get("video_url"),
                "youtube_video_id": yt.get("video_id"),
                "youtube_video_url": yt.get("video_url"),
                "youtube_offset_ms": yt.get("offset_ms"),
                "youtube_offset_method": yt.get("method_version"),
                "youtube_offset_confidence": yt.get("match_score"),
                "youtube_offset_status": yt.get("status"),
                "lyrics_provider": lyrics.get("provider"),
                "lyrics_status": lyrics.get("status"),
            },
            "paths": paths,
            "record_path": paths["song_json"],
            "hashes": {
                "source_audio_sha256": song.get("source_mp3_sha256") or src.get("ytm_audio_sha256"),
                "synced_lrc_sha256": song.get("source_lrc_sha256") or lyrics.get("raw_lrc_sha256"),
                "source_metadata_sha256": song.get("source_json_sha256"),
            },
        }
        catalog.append(record)
        seen.add(key)

    # Keep previously registered songs visible even if the configured playlist ID changed.
    for key, song in song_rows.items():
        if key in seen:
            continue
        meta = metadata_rows.get(key, {})
        src = source_rows.get(key, {})
        catalog.append({
            "song_key": key,
            "spotify": {"track_id": song.get("spotify_track_id"), "isrc": song.get("canonical_isrc"), "album_id": meta.get("spotify_album_id"), "track_url": meta.get("spotify_track_url")},
            "title": song.get("title"), "artists": song.get("artist"), "album": song.get("album"), "duration_ms": song.get("duration_ms"),
            "details": {"album_artist": meta.get("album_artist"), "release_date": meta.get("release_date"),
                        "track_number": meta.get("track_number"), "disc_number": meta.get("disc_number"),
                        "isrc": song.get("canonical_isrc"), "spotify_album_id": meta.get("spotify_album_id")},
            "playlist": {"id": None, "name": None, "position": None, "serial": song.get("playlist_serial"), "added_at": None},
            "status": {"playlist": None, "pipeline": song.get("pipeline_status"), "quality": song.get("quality_status"), "terminal": bool(song.get("terminal")), "terminal_reason": song.get("terminal_reason")},
            "sources": {"ytmusic_audio_video_id": song.get("ytm_video_id"), "ytmusic_audio_url": song.get("ytm_url"), "lyrics_provider": None, "lyrics_status": None},
            "paths": {"source_audio": _relative(song.get("source_mp3_path") or src.get("ytm_audio_path"), root), "source_metadata": _relative(song.get("source_json_path"), root), "synced_lrc": _relative(song.get("final_lrc_path"), root), "wordlevel_lrc": _relative(song.get("final_wordlevel_lrc_path"), root), "song_audio": _relative(song.get("final_mp3_path"), root), "audio_8d": _relative(song.get("final_8d_mp3_path"), root), "song_json": _relative(song.get("final_json_path"), root), "reel_video": None, "reel_json": None},
            "record_path": _relative(song.get("final_json_path"), root),
            "hashes": {"source_audio_sha256": song.get("source_mp3_sha256"), "synced_lrc_sha256": song.get("source_lrc_sha256"), "source_metadata_sha256": song.get("source_json_sha256")},
        })

    catalog.sort(key=lambda x: (str((x.get("playlist") or {}).get("id") or ""), int((x.get("playlist") or {}).get("position") or 10**9), str(x.get("song_key"))))
    payload = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "authority": "db/vachika.db (songs.json is a generated catalog export; do not edit directly)",
        "song_count": len(catalog),
        "songs": catalog,
    }
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    os.replace(tmp, out)
    return out
