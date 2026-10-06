from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"
DEFAULT_SCOPES = "playlist-read-private playlist-read-collaborative"


class SpotifyAuthError(RuntimeError):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _pkce_verifier() -> str:
    return _b64url(secrets.token_bytes(48))


def _challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def _callback_server(redirect_uri: str, timeout_seconds: int = 300):
    parsed = urlparse(redirect_uri)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.port:
        raise SpotifyAuthError("Spotify PKCE redirect_uri must use a local HTTP callback, e.g. http://127.0.0.1:8888/callback")

    holder: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            query = parse_qs(urlparse(self.path).query)
            if "error" in query:
                holder["error"] = query["error"][0]
            if "code" in query:
                holder["code"] = query["code"][0]
            if "state" in query:
                holder["state"] = query["state"][0]
            body = b"Spotify authorization received. You can close this browser tab."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            holder["done"] = True

        def log_message(self, *_args):
            return

    server = HTTPServer((parsed.hostname, parsed.port), Handler)
    server.timeout = 1

    def serve():
        deadline = time.time() + timeout_seconds
        while time.time() < deadline and not holder.get("done"):
            server.handle_request()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return server, thread, holder


def authorize_pkce(client_id: str, redirect_uri: str = DEFAULT_REDIRECT_URI, scopes: str = DEFAULT_SCOPES, out_file: str | Path = "spotify_auth.json") -> Path:
    client_id = str(client_id or "").strip()
    if not client_id:
        raise SpotifyAuthError("A Spotify app client_id is required")
    verifier = _pkce_verifier()
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "code_challenge_method": "S256",
        "code_challenge": _challenge(verifier),
        "state": state,
    }
    auth_url = f"{AUTH_URL}?{urlencode(params)}"
    server, thread, holder = _callback_server(redirect_uri)
    print("Open this URL in a browser if it did not open automatically:")
    print(auth_url)
    try:
        webbrowser.open(auth_url)
        thread.join(timeout=305)
    finally:
        server.server_close()

    if holder.get("error"):
        raise SpotifyAuthError(f"Spotify authorization failed: {holder['error']}")
    code = holder.get("code")
    returned_state = holder.get("state")
    if not code:
        raise SpotifyAuthError("Spotify authorization timed out or no authorization code was returned")
    if returned_state and returned_state != state:
        raise SpotifyAuthError("Spotify OAuth state mismatch")

    data = {
        "client_id": client_id,
        "grant_type": "authorization_code",
        "code": str(code),
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    response = requests.post(TOKEN_URL, data=data, timeout=30)
    if not response.ok:
        raise SpotifyAuthError(f"Spotify token exchange failed: HTTP {response.status_code}: {response.text[:500]}")
    token = response.json()
    access_token = token.get("access_token")
    if not access_token:
        raise SpotifyAuthError("Spotify token response did not contain access_token")

    payload = {
        "access_token": access_token,
        "refresh_token": token.get("refresh_token"),
        "token_type": token.get("token_type", "Bearer"),
        "scope": token.get("scope", scopes),
        "expires_in": token.get("expires_in"),
        "expires_at": int(time.time() + int(token.get("expires_in") or 3600)),
        "client_id": client_id,
        "redirect_uri": redirect_uri,
    }
    out = Path(out_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(out)
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass
    return out
