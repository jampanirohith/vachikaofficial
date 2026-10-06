from __future__ import annotations

from typing import Any, Mapping

from .metadata import normalize_isrc
from .spotify import SpotifyClient


def _artist_string(artists: Any) -> str | None:
    names = []
    for artist in artists or []:
        if isinstance(artist, Mapping) and artist.get("name"):
            name = str(artist["name"]).strip()
            if name and name not in names:
                names.append(name)
    return ", ".join(names) or None


def normalize_playlist_item(item: Mapping[str, Any], position: int) -> dict[str, Any]:
    """Normalize one already-fetched Spotify playlist item without issuing any API calls."""
    track = item.get("track")
    if not isinstance(track, Mapping):
        track = item.get("item")
    if not isinstance(track, Mapping):
        return {
            "playlist_position": position,
            "track_type": None,
            "is_local": False,
            "status": "skipped",
            "terminal_reason": "NON_TRACK_ITEM",
            "raw_item_json": dict(item),
        }

    track_type = str(track.get("type") or "track")
    is_local = bool(track.get("is_local"))
    track_id = str(track.get("id") or "").strip() or None
    album = track.get("album") if isinstance(track.get("album"), Mapping) else {}
    external_ids = track.get("external_ids") if isinstance(track.get("external_ids"), Mapping) else {}
    external_urls = track.get("external_urls") if isinstance(track.get("external_urls"), Mapping) else {}
    added_by = item.get("added_by") if isinstance(item.get("added_by"), Mapping) else {}

    if track_type != "track" or is_local or not track_id:
        return {
            "playlist_position": position,
            "added_at": item.get("added_at"),
            "added_by_user_id": added_by.get("id"),
            "added_by_user_name": added_by.get("display_name") or added_by.get("name"),
            "spotify_track_id": track_id,
            "spotify_uri": track.get("uri"),
            "spotify_url": external_urls.get("spotify"),
            "title": track.get("name"),
            "artist": _artist_string(track.get("artists")),
            "album": album.get("name"),
            "duration_ms": track.get("duration_ms"),
            "isrc": normalize_isrc(external_ids.get("isrc")),
            "track_type": track_type,
            "is_local": is_local,
            "status": "skipped",
            "terminal_reason": "LOCAL_TRACK" if is_local else "NON_TRACK_ITEM",
            "raw_item_json": dict(item),
        }

    return {
        "playlist_position": position,
        "added_at": item.get("added_at"),
        "added_by_user_id": added_by.get("id"),
        "added_by_user_name": added_by.get("display_name") or added_by.get("name"),
        "spotify_track_id": track_id,
        "spotify_uri": str(track.get("uri") or "").strip() or None,
        "spotify_url": str(external_urls.get("spotify") or "").strip() or None,
        "title": str(track.get("name") or "").strip() or None,
        "artist": _artist_string(track.get("artists")),
        "album": str(album.get("name") or "").strip() or None,
        "duration_ms": int(track.get("duration_ms") or 0) or None,
        "isrc": normalize_isrc(external_ids.get("isrc")),
        "track_type": "track",
        "is_local": False,
        "status": "pending",
        "terminal_reason": None,
        "raw_item_json": dict(item),
    }


def fetch_complete_playlist(client: SpotifyClient, playlist_id: str, progress=None):
    """Fetch playlist metadata/items once; normalization itself makes zero additional API calls."""
    meta, items, pid = client.fetch_playlist_snapshot(playlist_id, progress=progress)
    normalized = [normalize_playlist_item(item, i) for i, item in enumerate(items, 1)]
    return pid, meta, normalized
