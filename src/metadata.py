from __future__ import annotations

from dataclasses import dataclass, field, fields
import json
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


@dataclass(frozen=True)
class NormalizedMetadata:
    title: str
    title_original: str | None
    primary_artist: str | None
    artist: str
    artists: list[str]
    album: str | None
    album_artist: str | None
    isrc: str | None
    track_number: str | None
    disc_number: str | None
    release_date: str | None
    release_date_source: str | None
    upload_date: str | None
    upload_timestamp: int | None
    release_timestamp: int | None
    modified_date: str | None
    modified_timestamp: int | None
    description: str | None
    genre: str | None
    composer: str | None
    publisher: str | None
    copyright: str | None
    license: str | None
    comment: str | None
    language: str | None
    bpm: int | None
    compilation: bool | None
    encoder: str | None
    duration: int | None
    source_duration: int | None
    source_ext: str | None
    source_container: str | None
    source_codec: str | None
    source_format_id: str | None
    source_format_note: str | None
    source_bitrate: int | None
    source_sample_rate: int | None
    source_channels: int | None
    source_filesize: int | None
    source_filesize_approx: int | None
    source_language: str | None
    source_video_id: str | None
    source_webpage_url: str | None
    source_original_url: str | None
    source_display_id: str | None
    source_webpage_url_basename: str | None
    source_webpage_url_domain: str | None
    source_extractor: str | None
    source_extractor_key: str | None
    source_channel: str | None
    source_channel_id: str | None
    source_channel_url: str | None
    source_channel_follower_count: int | None
    source_channel_is_verified: bool | None
    source_uploader: str | None
    source_uploader_id: str | None
    source_uploader_url: str | None
    source_views: int | None
    source_location: str | None
    source_availability: str | None
    source_age_limit: int | None
    source_live_status: str | None
    source_media_type: str | None
    source_thumbnail: str | None
    source_thumbnails: list[dict[str, Any]]
    source_categories: list[str]
    source_tags: list[str]
    source_playlist: str | None
    source_playlist_id: str | None
    source_playlist_count: int | None
    source_playlist_index: int | None
    source_playlist_uploader: str | None
    source_playlist_uploader_id: str | None
    source_playlist_channel: str | None
    source_playlist_channel_id: str | None
    source_playlist_webpage_url: str | None
    raw: Mapping[str, Any] = field(repr=False)


class MetadataError(ValueError):
    pass


def load_info_json(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MetadataError(f"Invalid JSON: {path}: {exc}") from exc
    except OSError as exc:
        raise MetadataError(f"Could not read info JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise MetadataError(f"Expected top-level JSON object: {path}")
    return data


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return str(value).strip() or None


def normalize_isrc(value: Any) -> str | None:
    """Normalize an ISRC for storage/comparison without inventing one."""
    text = _as_text(value)
    if not text:
        return None
    compact = re.sub(r"[\s-]+", "", text.upper())
    match = re.search(r"(?<![A-Z0-9])([A-Z]{2}[A-Z0-9]{3}\d{7})(?![A-Z0-9])", compact)
    if not match:
        return None
    return match.group(1)


def _artists(value: Any) -> list[str]:
    result: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                name = _as_text(item.get("name"))
            else:
                name = _as_text(item)
            if name and name not in result:
                result.append(name)
    elif value is not None:
        text = _as_text(value)
        if text:
            result.append(text)
    return result


def _string_list(value: Any) -> list[str]:
    return [x for x in _artists(value) if x]


def _thumbnail_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, Mapping):
            result.append(dict(item))
    return result


def _first_nonempty(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", []):
            return value
    return None


def _album_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return _as_text(value.get("name"))
    return _as_text(value)


def _integer(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _boolean(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def _normalize_date(value: Any) -> str | None:
    text = _as_text(value)
    if not text:
        return None
    text = text.replace("/", "-")
    if re.fullmatch(r"\d{8}", text):
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    if re.fullmatch(r"\d{4}-\d{2}", text):
        return text
    if re.fullmatch(r"\d{4}", text):
        return text
    return text


def _track_number(info: Mapping[str, Any]) -> str | None:
    for key in ("track_number", "track"):
        text = _as_text(info.get(key))
        if text and re.fullmatch(r"\d+(?:/\d+)?", text):
            return text
    return None


def _disc_number(info: Mapping[str, Any]) -> str | None:
    for key in ("disc_number", "disc"):
        text = _as_text(info.get(key))
        if text and re.fullmatch(r"\d+(?:/\d+)?", text):
            return text
    return None


def _url_parts(value: Any) -> tuple[str | None, str | None]:
    text = _as_text(value)
    if not text:
        return None, None
    try:
        parsed = urlparse(text)
        return parsed.path.rsplit("/", 1)[-1] or None, parsed.hostname or None
    except ValueError:
        return None, None


def _flatten_source_info(info: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize acquisition metadata from both legacy and current info.json layouts.

    yt-dlp provenance is stored under ``stream``/``yt_dlp`` in the active release,
    while older files may expose ``videoDetails`` or a flat payload.  Metadata
    consumers must not depend on one downloader-specific nesting shape.
    Top-level values always win; nested values fill only missing fields.
    """
    merged: dict[str, Any] = dict(info)
    layers: list[Mapping[str, Any]] = []
    for key in ("yt_dlp", "stream", "videoDetails", "video_details"):
        value = info.get(key)
        if isinstance(value, Mapping):
            layers.append(value)
    search = info.get("ytmusic_search")
    if isinstance(search, Mapping):
        candidate = search.get("selected_candidate")
        if isinstance(candidate, Mapping):
            layers.append(candidate)
    # Candidate/source records are useful for catalog fields that yt-dlp does not
    # expose (artists/album), so they are deliberately considered last.
    for layer in layers:
        for key, value in layer.items():
            if merged.get(key) in (None, "", []):
                merged[key] = value
    # Common downloader aliases.
    if not merged.get("title") and merged.get("name"):
        merged["title"] = merged["name"]
    if not merged.get("artists") and merged.get("artist"):
        merged["artists"] = merged["artist"]
    return merged


def _merge_fallbacks(info: Mapping[str, Any], playlist_item: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = _flatten_source_info(info)
    if not playlist_item:
        return merged
    fallback_title = _as_text(playlist_item.get("title"))
    if not merged.get("title") and fallback_title:
        merged["title"] = fallback_title
    fallback_artists = playlist_item.get("artists")
    if not merged.get("artists") and fallback_artists:
        merged["artists"] = fallback_artists
    if not merged.get("artist"):
        artist_names = _artists(fallback_artists)
        if artist_names:
            merged["artist"] = ", ".join(artist_names)
    fallback_album = _album_name(playlist_item.get("album"))
    if not merged.get("album") and fallback_album:
        merged["album"] = fallback_album
    if merged.get("duration") in (None, "") and playlist_item.get("duration_seconds") is not None:
        merged["duration"] = playlist_item.get("duration_seconds")
    return merged


def _extract_isrc(source: Mapping[str, Any]) -> str | None:
    candidates = [source.get("isrc"), source.get("isrc_code"), source.get("recording_isrc")]
    external_ids = source.get("external_ids")
    if isinstance(external_ids, Mapping):
        candidates.append(external_ids.get("isrc"))
    for candidate in candidates:
        normalized = normalize_isrc(candidate)
        if normalized:
            return normalized
    return None


def normalize_metadata(
    info: Mapping[str, Any],
    *,
    playlist_item: Mapping[str, Any] | None = None,
    actual_duration: int | None = None,
) -> NormalizedMetadata:
    source = _merge_fallbacks(info, playlist_item)
    title = _as_text(source.get("title")) or _as_text(source.get("track"))
    if not title:
        raise MetadataError("Source info.json contains no usable title")

    artists = _artists(source.get("artists")) or _artists(source.get("artist")) or _artists(source.get("creator"))
    artist = ", ".join(artists)
    if not artist:
        raise MetadataError("Source info.json contains no usable artist")

    album = _album_name(source.get("album"))
    album_artist = _as_text(source.get("album_artist"))
    isrc = _extract_isrc(source)
    release_date = _normalize_date(_first_nonempty(source, ("release_date", "release_year")))
    release_date_source = "source.info_json:release_date" if source.get("release_date") else (
        "source.info_json:release_year" if source.get("release_year") else None
    )
    upload_date = _normalize_date(source.get("upload_date"))
    modified_date = _normalize_date(source.get("modified_date"))
    source_duration = _integer(source.get("duration"))
    duration = actual_duration if actual_duration is not None else source_duration
    source_webpage_url_basename, source_webpage_url_domain = _url_parts(source.get("webpage_url"))

    thumbnails = _thumbnail_list(source.get("thumbnails"))
    source_thumbnail = _as_text(source.get("thumbnail"))
    if not source_thumbnail and thumbnails:
        def thumb_area(item: dict[str, Any]) -> int:
            width = _integer(item.get("width")) or 0
            height = _integer(item.get("height")) or 0
            return width * height
        source_thumbnail = _as_text(max(thumbnails, key=thumb_area).get("url"))

    return NormalizedMetadata(
        title=title,
        title_original=_as_text(source.get("title")),
        primary_artist=artists[0] if artists else None,
        artist=artist,
        artists=artists,
        album=album,
        album_artist=album_artist,
        isrc=isrc,
        track_number=_track_number(source),
        disc_number=_disc_number(source),
        release_date=release_date,
        release_date_source=release_date_source,
        upload_date=upload_date,
        upload_timestamp=_integer(source.get("timestamp")),
        release_timestamp=_integer(source.get("release_timestamp")),
        modified_date=modified_date,
        modified_timestamp=_integer(source.get("modified_timestamp")),
        description=_as_text(source.get("description")),
        genre=_as_text(source.get("genre")) or (", ".join(_string_list(source.get("categories"))) if source.get("categories") else None),
        composer=_as_text(source.get("composer")),
        publisher=_as_text(source.get("publisher")) or _as_text(source.get("label")),
        copyright=_as_text(source.get("copyright")),
        license=_as_text(source.get("license")),
        comment=_as_text(source.get("comment")),
        language=_as_text(source.get("language")),
        bpm=_integer(source.get("bpm")),
        compilation=_boolean(source.get("compilation")),
        encoder=_as_text(source.get("encoder")),
        duration=duration,
        source_duration=source_duration,
        source_ext=_as_text(source.get("ext")),
        source_container=_as_text(source.get("container")),
        source_codec=_as_text(source.get("acodec")),
        source_format_id=_as_text(source.get("format_id")),
        source_format_note=_as_text(source.get("format_note")),
        source_bitrate=_integer(source.get("abr")) or _integer(source.get("tbr")),
        source_sample_rate=_integer(source.get("asr")) or _integer(source.get("sampling_rate")),
        source_channels=_integer(source.get("channels")) or _integer(source.get("audio_channels")),
        source_filesize=_integer(source.get("filesize")),
        source_filesize_approx=_integer(source.get("filesize_approx")),
        source_language=_as_text(source.get("language")),
        source_video_id=_as_text(source.get("id")),
        source_webpage_url=_as_text(source.get("webpage_url")),
        source_original_url=_as_text(source.get("original_url")),
        source_display_id=_as_text(source.get("display_id")),
        source_webpage_url_basename=source_webpage_url_basename,
        source_webpage_url_domain=source_webpage_url_domain,
        source_extractor=_as_text(source.get("extractor")),
        source_extractor_key=_as_text(source.get("extractor_key")),
        source_channel=_as_text(source.get("channel")),
        source_channel_id=_as_text(source.get("channel_id")),
        source_channel_url=_as_text(source.get("channel_url")),
        source_channel_follower_count=_integer(source.get("channel_follower_count")),
        source_channel_is_verified=_boolean(source.get("channel_is_verified")),
        source_uploader=_as_text(source.get("uploader")),
        source_uploader_id=_as_text(source.get("uploader_id")),
        source_uploader_url=_as_text(source.get("uploader_url")),
        source_views=_integer(source.get("view_count")),
        source_location=_as_text(source.get("location")),
        source_availability=_as_text(source.get("availability")),
        source_age_limit=_integer(source.get("age_limit")),
        source_live_status=_as_text(source.get("live_status")),
        source_media_type=_as_text(source.get("media_type")),
        source_thumbnail=source_thumbnail,
        source_thumbnails=thumbnails,
        source_categories=_string_list(source.get("categories")),
        source_tags=_string_list(source.get("tags")),
        source_playlist=_as_text(source.get("playlist")),
        source_playlist_id=_as_text(source.get("playlist_id")),
        source_playlist_count=_integer(source.get("playlist_count")),
        source_playlist_index=_integer(source.get("playlist_index")),
        source_playlist_uploader=_as_text(source.get("playlist_uploader")),
        source_playlist_uploader_id=_as_text(source.get("playlist_uploader_id")),
        source_playlist_channel=_as_text(source.get("playlist_channel")),
        source_playlist_channel_id=_as_text(source.get("playlist_channel_id")),
        source_playlist_webpage_url=_as_text(source.get("playlist_webpage_url")),
        raw=dict(info),
    )


def metadata_field_names(*, include_raw: bool = False) -> list[str]:
    names = [f.name for f in fields(NormalizedMetadata)]
    return names if include_raw else [name for name in names if name != "raw"]


def normalized_metadata_from_record(record: Mapping[str, Any]) -> NormalizedMetadata:
    """Rebuild normalized metadata from a stored songs.db row for library re-tagging."""
    artists_raw = record.get("artists_json")
    if isinstance(artists_raw, str):
        try:
            artists = json.loads(artists_raw)
        except json.JSONDecodeError:
            artists = []
    else:
        artists = artists_raw or []
    if not isinstance(artists, list):
        artists = []

    def as_list_json(name: str) -> list[dict[str, Any]]:
        value = record.get(name)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                return []
        return value if isinstance(value, list) else []

    def as_list_text(name: str) -> list[str]:
        value = record.get(name)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(v) for v in parsed]
            except json.JSONDecodeError:
                return [value] if value else []
        if isinstance(value, list):
            return [str(v) for v in value]
        return []

    raw_value = record.get("source_info_json") or "{}"
    try:
        raw = json.loads(raw_value) if isinstance(raw_value, str) else dict(raw_value)
    except (json.JSONDecodeError, TypeError, ValueError):
        raw = {}

    kwargs: dict[str, Any] = {}
    for name in metadata_field_names():
        if name in {"artists", "source_thumbnails", "source_categories", "source_tags"}:
            continue
        kwargs[name] = record.get(name)
    kwargs["artists"] = [str(v) for v in artists]
    kwargs["source_thumbnails"] = as_list_json("source_thumbnails_json") if "source_thumbnails_json" in record else []
    kwargs["source_categories"] = as_list_text("source_categories_json") if "source_categories_json" in record else []
    kwargs["source_tags"] = as_list_text("source_tags_json") if "source_tags_json" in record else []
    kwargs["raw"] = raw
    kwargs["title"] = str(record.get("title") or "Untitled")
    kwargs["artist"] = str(record.get("artist") or "Unknown Artist")
    return NormalizedMetadata(**kwargs)
