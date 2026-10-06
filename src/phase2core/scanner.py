from __future__ import annotations

from pathlib import Path
from typing import Any

from mutagen.id3 import ID3
from mutagen.mp3 import MP3

from .db import Database
from .json_manager import JSONManager
from .types import InputPackage, ScanIssue
from .utils import sha256_file


class Scanner:
    def __init__(self, db: Database, json_manager: JSONManager):
        self.db = db
        self.json_manager = json_manager
        self.issues: list[ScanIssue] = []

    @staticmethod
    def _find_case_insensitive(directory: Path, basename: str, extension: str) -> Path | None:
        direct = directory / f"{basename}{extension}"
        if direct.exists():
            return direct
        target = f"{basename}{extension}".casefold()
        for path in directory.iterdir():
            if path.is_file() and path.name.casefold() == target:
                return path
        return None

    def discover(self, original_dir: Path) -> list[InputPackage]:
        if not hasattr(self, "issues"):
            self.issues = []
        else:
            self.issues.clear()
        packages: list[InputPackage] = []
        mp3s = sorted(
            p for p in original_dir.iterdir()
            if p.is_file() and p.suffix.casefold() == ".mp3"
        )
        seen: set[str] = set()
        mp3_basenames = {p.stem for p in mp3s}
        for orphan in sorted(p for p in original_dir.iterdir() if p.is_file() and p.suffix.casefold() in {".lrc", ".json"}):
            if orphan.stem not in mp3_basenames:
                self.issues.append(ScanIssue(orphan.stem, str(orphan), "ORPHAN_SIDECAR", "warning"))
        for mp3 in mp3s:
            basename = mp3.stem
            if basename in seen:
                self.issues.append(ScanIssue(basename, str(mp3), "DUPLICATE_BASENAME", "error"))
                continue
            seen.add(basename)
            lrc = self._find_case_insensitive(original_dir, basename, ".lrc")
            js = self._find_case_insensitive(original_dir, basename, ".json")
            if lrc is None:
                self.issues.append(ScanIssue(basename, str(mp3), "MISSING_LRC", "error"))
                continue
            if js is None:
                self.issues.append(ScanIssue(basename, str(mp3), "MISSING_JSON", "error"))
                continue
            packages.append(InputPackage(basename, mp3, lrc, js))
        return packages

    def inventory(
        self,
        package: InputPackage,
        *,
        pipeline_version: str,
        normalizer_version: str,
        config_hash: str,
        model_name: str,
        model_revision: str | None,
    ) -> int:
        mp3_hash = sha256_file(package.mp3_path)
        lrc_hash = sha256_file(package.lrc_path)
        json_hash = sha256_file(package.json_path)

        try:
            json_data = self.json_manager.load(package.json_path)
        except Exception as exc:
            self.db.log(None, "scanner", "ERROR", str(exc), metadata={"path": str(package.json_path)})
            raise

        song_obj = json_data.get("song", {})
        normalized = song_obj.get("normalized", {}) if isinstance(song_obj, dict) else {}
        if not isinstance(normalized, dict):
            normalized = {}
        playlist = json_data.get("playlist", {})
        playlist_item = playlist.get("playlist_item", {}) if isinstance(playlist, dict) else {}
        if not isinstance(playlist_item, dict):
            playlist_item = {}
        playlist_album = playlist_item.get("album", {})
        if not isinstance(playlist_album, dict):
            playlist_album = {}

        title = str(normalized.get("title") or playlist_item.get("title") or package.basename)
        artist = str(normalized.get("artist") or normalized.get("primary_artist") or "")
        album = str(normalized.get("album") or playlist_album.get("name") or "")

        try:
            serial = json_data.get("playlist", {}).get("playlist_position")
            serial = int(serial) if serial is not None else None
        except (TypeError, ValueError):
            serial = None
        video_id = None
        if isinstance(json_data.get("playlist"), dict):
            video_id = json_data["playlist"].get("ytmusic_video_id")
        if not video_id and isinstance(playlist_item, dict):
            video_id = playlist_item.get("videoId")

        try:
            info = MP3(package.mp3_path, ID3=ID3)
            duration_ms = int(round(info.info.length * 1000))
        except Exception as exc:
            raise ValueError(f"INVALID_MP3: {package.mp3_path}: {exc}") from exc

        values: dict[str, Any] = {
            "basename": package.basename,
            "serial_number": serial,
            "video_id": video_id,
            "original_mp3_path": str(package.mp3_path),
            "original_lrc_path": str(package.lrc_path),
            "original_json_path": str(package.json_path),
            "original_mp3_sha256": mp3_hash,
            "original_lrc_sha256": lrc_hash,
            "original_json_sha256": json_hash,
            "title": title,
            "artist": artist,
            "album": album,
            "duration_ms": duration_ms,
            "pipeline_version": pipeline_version,
            "model_name": model_name,
            "model_revision": model_revision,
            "normalizer_version": normalizer_version,
            "config_hash": config_hash,
        }
        return self.db.upsert_song(values)
