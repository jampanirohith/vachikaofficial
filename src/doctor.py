from __future__ import annotations

import importlib.util
import shutil


def run_doctor(cfg):
    ok = True
    print("Unified Spotify -> YT Music -> Telugu Word LRC -> 8D -> Reel doctor")
    for exe in ("ffmpeg", "ffprobe"):
        hit = shutil.which(exe)
        print(f"{exe:20} {hit or 'MISSING'}")
        ok = ok and bool(hit)

    modules = ("ytmusicapi", "yt_dlp", "mutagen", "requests", "PIL", "numpy", "soundfile", "scipy", "torch", "transformers")
    for mod in modules:
        hit = importlib.util.find_spec(mod) is not None
        print(f"python:{mod:14} {'OK' if hit else 'MISSING'}")
        ok = ok and hit

    spotify_auth = cfg.path("spotify.auth_file") if cfg.get("spotify.auth_file") else None
    spotify_creds = bool((cfg.get("spotify.client_id") or "").strip() and (cfg.get("spotify.client_secret") or "").strip())
    spotify_file_ok = bool(spotify_auth and spotify_auth.exists() and spotify_auth.stat().st_size > 0)
    spotify_user_ok = False
    if spotify_file_ok:
        try:
            import json
            auth_payload=json.loads(spotify_auth.read_text(encoding='utf-8'))
            spotify_user_ok=bool((auth_payload.get('access_token') or '').strip() or (auth_payload.get('refresh_token') or '').strip())
        except Exception:
            spotify_user_ok=False
    spotify_ok = spotify_user_ok if cfg.get('spotify.require_user_auth', True) else (spotify_file_ok or spotify_creds)
    print(f"Spotify auth        {spotify_auth if spotify_file_ok else ('client credentials' if spotify_creds else 'MISSING')}")
    if cfg.get('spotify.require_user_auth', True) and not spotify_user_ok:
        print('Spotify user auth   MISSING (required for playlist/catalog)')
    ok = ok and spotify_ok

    ytm_auth = cfg.path("ytmusic.auth_file") if cfg.get("ytmusic.auth_file") else None
    ytm_ok = bool(ytm_auth and ytm_auth.exists() and ytm_auth.stat().st_size > 0) if cfg.get("ytmusic.require_auth", True) else True
    print(f"YTMusic auth        {ytm_auth if ytm_ok and ytm_auth else ('optional' if not cfg.get('ytmusic.require_auth', True) else 'MISSING')}")
    ok = ok and ytm_ok

    try:
        import torch
        cuda = bool(torch.cuda.is_available())
        print("torch CUDA          ", cuda)
        if cfg.get("runtime.require_cuda", False) and cfg.get("runtime.device", "cpu") == "cuda" and not cfg.get("runtime.allow_cpu_fallback", True):
            ok = ok and cuda
    except Exception as exc:
        print("torch CUDA          ERROR", exc)
        ok = False

    if cfg.get("playlist.provider", "spotify") != "spotify":
        print("Playlist provider   INVALID")
        ok = False
    if cfg.get("playlist.scan_complete_once", True) is not True:
        print("Playlist scan mode  INVALID")
        ok = False
    if cfg.get("duplicate_detection.identifier", "isrc") != "isrc":
        print("Duplicate key       INVALID")
        ok = False

    print("yt-dlp audio       REQUIRED / selected YT Music URL only")
    print("Spotify audio       FORBIDDEN / not downloaded")
    print("Result              ", "READY" if ok else "NOT READY")
    return ok
