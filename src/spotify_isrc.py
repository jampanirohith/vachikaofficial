from __future__ import annotations

from .spotify import SpotifyClient, SpotifyError


def lookup_isrc(cfg, metadata):
    """Compatibility wrapper: only the Spotify ISRC is returned."""
    settings = cfg.get("spotify", {}) or {}
    if not settings.get("enabled"):
        return None, {"status": "disabled"}
    client = SpotifyClient(settings, project_root=str(cfg.root))
    result = client.lookup_isrc(title=metadata.title, album=metadata.album)
    if not result:
        return None, {"status": "no_match"}
    return result, {"status": "matched", "isrc": result.isrc}
