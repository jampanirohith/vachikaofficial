#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import Config
from src.spotify_auth import authorize_pkce


def main():
    parser = argparse.ArgumentParser(description="Authorize the Spotify Web API for playlist reads.")
    parser.add_argument("--client-id", help="Spotify application Client ID; defaults to config/environment")
    parser.add_argument("--redirect-uri", help="Must exactly match the redirect URI registered in Spotify Developer Dashboard")
    parser.add_argument("--output", default="spotify_auth.json")
    args = parser.parse_args()
    cfg = Config.load()
    client_id = args.client_id or cfg.get("spotify.client_id")
    if not client_id:
        import os
        client_id = os.getenv("SPOTIFY_CLIENT_ID", "")
    redirect = args.redirect_uri or cfg.get("spotify.redirect_uri", "http://127.0.0.1:8888/callback")
    out = authorize_pkce(client_id, redirect_uri=redirect, out_file=Path(cfg.root) / args.output)
    print(f"Spotify authorization saved to: {out}")


if __name__ == "__main__":
    main()
