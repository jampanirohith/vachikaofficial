from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


def test_custom_config_path_is_preserved(tmp_path):
    from src.config import Config
    source = json.loads(Path("config.json.example").read_text(encoding="utf-8"))
    custom = tmp_path / "nested" / "custom-settings.json"
    custom.parent.mkdir()
    custom.write_text(json.dumps(source), encoding="utf-8")
    cfg = Config.load(custom)
    assert cfg.config_path == custom.resolve()
    assert cfg.root == custom.parent.resolve()


def test_demucs_stems_are_only_reused_with_provenance_manifest(monkeypatch, tmp_path):
    from src.audio.demucs import DemucsRunner
    work = tmp_path / "work"
    source = tmp_path / "song.mp3"
    source.write_bytes(b"source-song")
    stems = ("vocals", "drums", "bass", "other")
    out = work / "audio" / "stems"
    out.mkdir(parents=True)
    # Legacy-looking files must never be adopted without a provenance manifest.
    for stem in stems:
        (out / f"{stem}.wav").write_bytes(b"stale" * 1000)
    calls = []
    runner = DemucsRunner({"demucs.model": "htdemucs", "runtime.device": "cpu"}, work, None)

    def fake_run(src, outroot, device, segment=None):
        calls.append((Path(outroot), device))
        (Path(outroot) / "htdemucs" / Path(src).stem).mkdir(parents=True)
        folder = Path(outroot) / "htdemucs" / Path(src).stem
        for stem in stems:
            (folder / f"{stem}.wav").write_bytes((stem.encode() * 400))

    monkeypatch.setattr(runner, "_run", fake_run)
    monkeypatch.setattr(runner, "_cuda_ok", lambda: False)
    first = runner.run(source)
    second = runner.run(source)
    assert len(calls) == 1
    assert first["run_id"] == second["run_id"]
    manifest = json.loads(Path(first["manifest"]).read_text(encoding="utf-8"))
    assert manifest["successful_attempt_dir"] == str(calls[0][0])
    assert all(entry["source_attempt_dir"] == str(calls[0][0]) for entry in manifest["stems"].values())


def test_offset_estimator_rejects_unrelated_low_score_audio():
    from src.video.synchronization import _scan_full_song
    rng = np.random.default_rng(7)
    ref = rng.normal(size=100).astype(np.float32)
    yt = rng.normal(size=360).astype(np.float32)
    result, _ = _scan_full_song(ref, yt, frame_ms=25, coarse_step_ms=50,
                                fine_step_ms=25, min_match_score=0.75)
    assert result["accepted"] is False
    assert "score_below_minimum" in result["rejection_reasons"]


def test_offset_estimator_rejects_two_equally_good_distant_matches():
    from src.video.synchronization import _scan_full_song
    rng = np.random.default_rng(29)
    ref = rng.normal(size=40).astype(np.float32)
    yt = np.concatenate([np.zeros(8), ref, np.zeros(60), ref, np.zeros(12)]).astype(np.float32)
    result, _ = _scan_full_song(ref, yt, frame_ms=25, coarse_step_ms=25,
                                fine_step_ms=25, min_match_score=0.5,
                                min_peak_margin=0.05, ambiguity_window_ms=500)
    assert result["accepted"] is False
    assert "ambiguous_competing_peak" in result["rejection_reasons"]


def test_source_16k_cache_rebuilds_when_content_changes(monkeypatch, tmp_path):
    from src.audio import source_audio as audio_unified
    source = tmp_path / "source.mp3"
    source.write_bytes(b"source-A" * 100)
    calls = []

    def fake_decode(cmd, destination, timeout=900):
        calls.append(str(cmd[cmd.index("-i") + 1]))
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"normalized-wav-data" * 1000)
        return destination

    monkeypatch.setattr(audio_unified, "_atomic_ffmpeg_wav", fake_decode)
    monkeypatch.setattr(audio_unified, "duration_ms", lambda _p: 120_000)
    first, first_meta = audio_unified.prepare_source(source, tmp_path / "work")
    second, second_meta = audio_unified.prepare_source(source, tmp_path / "work")
    assert first == second and len(calls) == 1
    source.write_bytes(b"source-B" * 100)  # preserve path and size while changing the content
    third, third_meta = audio_unified.prepare_source(source, tmp_path / "work")
    assert third == first and len(calls) == 2
    assert first_meta["derived_from_sha256"] != third_meta["derived_from_sha256"]


def test_reference_audio_cache_is_bound_to_selected_video(monkeypatch, tmp_path):
    from src.video import youtube_source as youtube_video
    from src.common import utils
    calls = []

    class FakeClient:
        def download_youtube_reference_mp3(self, url, destination, info_path=None):
            calls.append(url)
            Path(destination).write_bytes((url.encode() * 2000))
            Path(info_path).write_text(json.dumps({"webpage_url": url}), encoding="utf-8")

    cfg = SimpleNamespace(root=tmp_path, get=lambda _key, default=None: default)
    monkeypatch.setattr(youtube_video, "_client", lambda _cfg: FakeClient())
    monkeypatch.setattr(utils, "run", lambda cmd, timeout=900: Path(cmd[-1]).write_bytes(b"normalized-wav" * 2000))
    monkeypatch.setattr(utils, "duration_ms", lambda _path: 180_000)

    a = youtube_video.download_audio(cfg, "VIDEO_A", tmp_path / "work", "https://www.youtube.com/watch?v=VIDEO_A")
    a_again = youtube_video.download_audio(cfg, "VIDEO_A", tmp_path / "work", "https://www.youtube.com/watch?v=VIDEO_A")
    b = youtube_video.download_audio(cfg, "VIDEO_B", tmp_path / "work", "https://www.youtube.com/watch?v=VIDEO_B")
    assert a == a_again == b
    assert calls == ["https://www.youtube.com/watch?v=VIDEO_A", "https://www.youtube.com/watch?v=VIDEO_B"]
    manifest = json.loads((tmp_path / "work/youtube/reference_audio.manifest.json").read_text(encoding="utf-8"))
    assert manifest["video_id"] == "VIDEO_B"
    assert manifest["source_url"].endswith("VIDEO_B")


def test_spotify_oauth_requires_present_matching_state():
    from src.providers.spotify_auth import SpotifyAuthError, validate_oauth_response
    assert validate_oauth_response("code", "state", "state") == "code"
    with pytest.raises(SpotifyAuthError, match="did not include"):
        validate_oauth_response("code", None, "state")
    with pytest.raises(SpotifyAuthError, match="mismatch"):
        validate_oauth_response("code", "other", "state")
    with pytest.raises(SpotifyAuthError, match="no authorization code"):
        validate_oauth_response(None, "state", "state")


def test_reel_trimming_uses_absolute_mapped_source_timeline(monkeypatch, tmp_path):
    from src.video import reel
    calls = {}
    work = tmp_path / "work"
    work.mkdir()
    lrc = tmp_path / "wordlevel.lrc"
    lrc.write_text("[00:10.000]hello\n", encoding="utf-8")
    source_audio = tmp_path / "8d.mp3"
    source_audio.write_bytes(b"audio" * 3000)
    source_video = tmp_path / "selected.mp4"
    source_video.write_bytes(b"video" * 3000)

    class Grabber:
        def __init__(self, *_a, **_kw): pass
        def download_section(self, src, start, end):
            calls["section"] = (start, end)
            return source_video
        def trim_exact(self, src, dst, duration_ms, trim_start_ms=0):
            calls["trim"] = trim_start_ms
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            Path(dst).write_bytes(b"trim" * 3000)
            return Path(dst)

    class Renderer:
        def __init__(self, *_a, **_kw): pass
        def render(self, _src):
            out = work / "video/rendered.mp4"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"render" * 3000)
            return out

    class ASS:
        def __init__(self, *_a, **_kw): pass
        def generate(self, *_a, **_kw):
            out = work / "lyrics.ass"
            out.write_text("ass", encoding="utf-8")
            return out

    class Assembler:
        def __init__(self, *_a, **_kw): pass
        def assemble(self, _video, _audio, _ass, destination, _duration):
            Path(destination).write_bytes(b"assembled" * 3000)
            return Path(destination)

    monkeypatch.setattr(reel, "VideoGrabber", Grabber)
    monkeypatch.setattr(reel, "VideoRenderer", Renderer)
    monkeypatch.setattr(reel, "Assembler", Assembler)
    monkeypatch.setattr(reel, "crop_8d", lambda _src, out, _start, _end: (Path(out).parent.mkdir(parents=True, exist_ok=True), Path(out).write_bytes(b"audio-crop" * 3000), Path(out))[2])
    monkeypatch.setattr(reel, "duration_ms", lambda _path: 3000)
    monkeypatch.setattr(reel, "_validate_reel", lambda _path, _duration: {"overall": True, "probe": {"video_codec": "test"}, "checks": {}, "failed_checks": []})
    monkeypatch.setattr("src.rendercore.ass_generator.ASSGenerator", ASS)
    monkeypatch.setattr("src.rendercore.font_manager.ensure_telugu_font", lambda *_a, **_kw: tmp_path / "font.ttf")
    (tmp_path / "font.ttf").write_bytes(b"font")
    cfg = SimpleNamespace(root=tmp_path, reels_dir=tmp_path / "reels" / "generated", fonts_dir=tmp_path, get=lambda key, default=None: default)
    cfg.reels_dir.mkdir(parents=True, exist_ok=True)
    song = {"song_key": "1_A", "duration_ms": 60_000, "basename": "song", "eight_d_path": str(source_audio)}
    reel.build_reel(cfg, work, song, 10_000, 13_000, "VIDEO_A", 2_500, lrc, [], None)
    assert calls["section"] == (12_500, 15_500)
    assert calls["trim"] == 12_500


def test_dry_run_is_read_only_when_database_does_not_exist(monkeypatch, tmp_path):
    import sys
    import main as app
    config = json.loads(Path("config.json.example").read_text(encoding="utf-8"))
    config["playlist"]["spotify_playlist_id"] = "TEST_PLAYLIST"
    config["paths"]["database_dir"] = "db"
    path = tmp_path / "custom.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["main.py", "--config", str(path), "--dry-run"])
    app.main()
    assert not (tmp_path / "db").exists()


def test_duplicate_isrc_handler_has_consistent_bound_call_signature(monkeypatch):
    import ast
    import builtins
    from src.orchestration.pipeline import Pipeline

    monkeypatch.setattr(builtins, "input", lambda _prompt="": "1")
    metadata = SimpleNamespace(title="Song B", artist="Artist B")
    duplicate = {"canonical_isrc": "USABC2600001", "title": "Song A", "artist": "Artist A"}
    # Exercise the user decision path and inspect the actual call site so the original
    # missing-argument TypeError cannot return unnoticed.
    assert Pipeline._terminal_duplicate(object(), "2_B", {"playlist_serial": 2}, metadata, duplicate) == "keep_previous"
    source = Path("src/orchestration/pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "_terminal_duplicate")
    definition = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_terminal_duplicate")
    assert len(call.args) == len(definition.args.args) - 1


def test_db_backup_restore_uses_custom_configured_directory(tmp_path):
    from src.storage.maintenance import backup_databases, restore_databases, integrity_check
    from src.storage.playlist_db import PlaylistDB
    from src.storage.song_db import SongsDB
    from src.storage.reel_db import ReelsDB
    custom_db = tmp_path / "configured-data" / "sqlite"
    custom_db.mkdir(parents=True)
    PlaylistDB(custom_db / "playlist.db")
    SongsDB(custom_db / "songs.db")
    ReelsDB(custom_db / "reels.db")
    backup = backup_databases(tmp_path, tmp_path / "backups", custom_db)
    (custom_db / "songs.db").write_bytes(b"invalid")
    restore_databases(tmp_path, backup, custom_db)
    assert all(integrity_check(custom_db / f"{name}.db") == "ok" for name in ("playlist", "songs", "reels"))
    assert (backup / "manifest.json").is_file()


def test_model_prepare_cli_exits_without_playlist_db_or_network(monkeypatch, tmp_path):
    import sys
    import main as app
    from src.common import model_cache
    config = json.loads(Path("config.json.example").read_text(encoding="utf-8"))
    config["playlist"]["spotify_playlist_id"] = ""
    config["paths"]["database_dir"] = "db"
    config["paths"]["models_dir"] = "models"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["main.py", "--config", str(config_path), "--prepare-models"])
    monkeypatch.setattr(model_cache, "prepare_models", lambda _cfg: {"ok": True, "models_dir": str(tmp_path / "models"), "models": {"mms_ctc": {"status": "ready"}}})
    result = app.main()
    assert result == 0
    assert not (tmp_path / "db").exists()


def test_spotify_oauth_callback_rejects_unexpected_path():
    import http.client
    import socket
    from src.providers.spotify_auth import _callback_server
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server, thread, holder = _callback_server(f"http://127.0.0.1:{port}/callback", timeout_seconds=10)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        connection.request("GET", "/wrong?code=code&state=state")
        response = connection.getresponse()
        assert response.status == 404
        response.read()
        connection.close()
        assert "done" not in holder
    finally:
        holder["done"] = True
        thread.join(timeout=2)
        server.server_close()


def test_source_16k_cache_rebuilds_when_cached_duration_is_inconsistent(monkeypatch, tmp_path):
    from src.audio import source_audio as audio_unified
    from src.common.hashing import sha256_file

    source = tmp_path / "source.mp3"
    source.write_bytes(b"real-source")
    calls = []

    def fake_decode(cmd, destination, timeout=900):
        calls.append(True)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"normalized-wav" * 2000)
        return destination

    def duration(path):
        path = Path(path)
        if path == source:
            return 120_000
        return 30_000 if path.exists() and path.read_bytes().startswith(b"stale-wav") else 120_000

    monkeypatch.setattr(audio_unified, "_atomic_ffmpeg_wav", fake_decode)
    monkeypatch.setattr(audio_unified, "duration_ms", duration)
    output, meta = audio_unified.prepare_source(source, tmp_path / "work")
    assert len(calls) == 1

    # Simulate a stale but internally self-consistent cache from a different decode.
    output.write_bytes(b"stale-wav" * 2000)
    manifest_path = output.parent / "source_manifest.json"
    cache_meta = json.loads(manifest_path.read_text(encoding="utf-8"))
    cache_meta["sha256"] = sha256_file(output)
    cache_meta["duration_ms"] = 30_000
    manifest_path.write_text(json.dumps(cache_meta), encoding="utf-8")

    rebuilt, rebuilt_meta = audio_unified.prepare_source(source, tmp_path / "work")
    assert rebuilt == output
    assert len(calls) == 2
    assert rebuilt_meta["duration_ms"] == 120_000


def test_video_source_cache_checks_media_hash(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace
    from src.rendercore import video_grabber

    work = tmp_path / "work"
    downloads = []

    class FakeYDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def download(self, urls):
            downloads.append(list(urls))
            Path(self.options["outtmpl"]).write_bytes(b"selected-video-content" * 1000)

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYDL))
    monkeypatch.setattr(video_grabber, "probe", lambda _path: {"duration_ms": 30_000})
    cfg = SimpleNamespace(get=lambda _key, default=None: default, root=tmp_path)
    grabber = video_grabber.VideoGrabber(cfg, work, None)

    first = grabber.download_section("https://www.youtube.com/watch?v=VIDEO_A", 5_000, 10_000)
    second = grabber.download_section("https://www.youtube.com/watch?v=VIDEO_A", 5_000, 10_000)
    assert first == second
    assert len(downloads) == 1

    # A readable/probeable replacement file is still not a valid cache hit.
    first.write_bytes(b"tampered-video-content" * 1000)
    rebuilt = grabber.download_section("https://www.youtube.com/watch?v=VIDEO_A", 5_000, 10_000)
    assert rebuilt == first
    assert len(downloads) == 2
    manifest = json.loads((work / "video" / "downloaded_source.request.json").read_text(encoding="utf-8"))
    assert manifest["sha256"]


def test_config_rejects_contradictory_cuda_and_cpu_fallback_policy(tmp_path):
    from src.config import Config
    payload = json.loads(Path("config.json.example").read_text(encoding="utf-8"))
    payload["runtime"]["require_cuda"] = True
    payload["runtime"]["allow_cpu_fallback"] = True
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="contradictory"):
        Config.load(path)


def test_phase2_demucs_isolator_resolves_cpu_without_undefined_device_reference(monkeypatch, tmp_path):
    from src.phase2core import demucs_isolator
    monkeypatch.setattr(demucs_isolator, "resolve_device", lambda requested, fallback, allow_cpu_fallback=True: "cpu")
    isolator = demucs_isolator.DemucsIsolator("htdemucs", "cuda", "cpu", allow_cpu_fallback=True)
    source = tmp_path / "source.wav"
    source.write_bytes(b"source" * 1000)
    destination = tmp_path / "out" / "vocals.wav"

    def fake_run(src, output_root, device, segment=None):
        output_root.mkdir(parents=True, exist_ok=True)
        result = output_root / "vocals.wav"
        result.write_bytes(b"vocals" * 2000)
        return result

    monkeypatch.setattr(isolator, "_run", fake_run)
    result = isolator.isolate(source, destination)
    assert result["device"] == "cpu"
    assert destination.is_file()


def test_phase2_demucs_retries_use_isolated_directories_and_successful_output(monkeypatch, tmp_path):
    from src.phase2core import demucs_isolator

    monkeypatch.setattr(demucs_isolator, "resolve_device", lambda requested, fallback, allow_cpu_fallback=True: "cuda")
    isolator = demucs_isolator.DemucsIsolator(
        "htdemucs", "cuda", "cpu", segment=8, retry_segment=4, allow_cpu_fallback=True
    )
    source = tmp_path / "source.wav"
    source.write_bytes(b"source" * 1000)
    destination = tmp_path / "out" / "vocals.wav"
    outputs = []

    def fake_run(_src, output_root, device, segment=None):
        outputs.append((Path(output_root), device, segment))
        if len(outputs) == 1:
            # A partial stale-looking output from the failed CUDA attempt must be ignored.
            (Path(output_root) / "vocals.wav").write_bytes(b"stale" * 1000)
            raise RuntimeError("CUDA out of memory")
        stem = Path(output_root) / "vocals.wav"
        stem.write_bytes(b"fresh-vocals" * 1000)
        return stem

    monkeypatch.setattr(isolator, "_run", fake_run)
    result = isolator.isolate(source, destination)
    assert result["device"] == "cuda"
    assert len(outputs) == 2
    assert outputs[0][0] != outputs[1][0]
    assert outputs[0][2] == 8 and outputs[1][2] == 4
    assert destination.read_bytes().startswith(b"fresh-vocals")


def test_ytdlp_deadline_hook_aborts_overdue_download():
    from types import SimpleNamespace
    from src.common.ytdlp_utils import deadline_progress_hook

    fake_ytdlp = SimpleNamespace(utils=SimpleNamespace(DownloadError=RuntimeError))
    hook = deadline_progress_hook(fake_ytdlp, 0, "selected test URL")
    with pytest.raises(RuntimeError, match="YTDLP_DOWNLOAD_TIMEOUT"):
        hook({"status": "downloading"})


def test_reference_audio_rejects_video_id_url_mismatch(tmp_path):
    from types import SimpleNamespace
    from src.video.youtube_source import download_audio

    cfg = SimpleNamespace(root=tmp_path, get=lambda _key, default=None: default)
    with pytest.raises(RuntimeError, match="VIDEO_ID_MISMATCH"):
        download_audio(cfg, "VIDEO_A", tmp_path / "work", "https://www.youtube.com/watch?v=VIDEO_B")


def test_manifest_writer_versions_hashes_and_does_not_mutate_input(tmp_path):
    import hashlib
    from src.storage.json_record import MANIFEST_RECORD_SCHEMA, MANIFEST_SCHEMA_VERSION, write

    payload_file = tmp_path / "songs" / "song.mp3"
    payload_file.parent.mkdir(parents=True)
    payload_file.write_bytes(b"known-artifact")
    target = tmp_path / "songs" / "song.json"
    source = {
        "title": "Example",
        "unified_pipeline": {
            "status": "FINALIZED",
            "provenance": {"pipeline_version": "test-version", "config_sha256": "config-hash", "source_mp3_sha256": "source-hash"},
            "outputs": {"mp3": "songs/song.mp3", "json": "songs/song.json"},
        },
    }
    original = json.loads(json.dumps(source))

    returned_hash = write(target, source, project_root=tmp_path)

    written = json.loads(target.read_text(encoding="utf-8"))
    up = written["unified_pipeline"]
    assert source == original
    assert up["schema_version"] == MANIFEST_SCHEMA_VERSION == 2
    assert up["record_schema"] == MANIFEST_RECORD_SCHEMA
    assert up["manifest"]["validation_status"] == "FINALIZED"
    assert up["manifest"]["pipeline_version"] == "test-version"
    assert up["manifest"]["config_sha256"] == "config-hash"
    assert up["manifest"]["source_sha256"] == "source-hash"
    assert up["hashes"]["mp3"] == hashlib.sha256(b"known-artifact").hexdigest()
    assert up["manifest"]["artifacts"]["json"]["self_reference"] is True
    assert up["manifest"]["artifacts"]["json"]["hash_location"] == "song.json.sha256"
    sidecar = target.with_suffix(".json.sha256")
    assert sidecar.read_text(encoding="utf-8").split()[0] == returned_hash
    assert returned_hash == hashlib.sha256(target.read_bytes()).hexdigest()
    assert up["manifest"]["content_fingerprint"]


def test_per_client_timeout_session_does_not_patch_global_requests(monkeypatch):
    import functools
    import requests
    from src.common.http_utils import make_timeout_session

    original = requests.sessions.Session.request
    session = make_timeout_session(17)
    try:
        assert requests.sessions.Session.request is original
        assert isinstance(session.request, functools.partial)
        assert session.request.keywords["timeout"] == 17
        another_session = make_timeout_session(29)
        assert another_session is not session
        assert another_session.request.keywords["timeout"] == 29
        assert requests.sessions.Session.request is original
    finally:
        session.close()
        if 'another_session' in locals():
            another_session.close()


def test_packaged_telugu_font_metadata_uses_embedded_family_name():
    from src.rendercore.font_manager import describe_font

    suravaram = describe_font(Path("assets/fonts/Suravaram-Regular.ttf"), "Suravaram")
    baloo = describe_font(Path("assets/fonts/BalooTammudu2-ExtraBold.ttf"), "Baloo Tammudu 2")
    assert suravaram["family"] == "Suravaram"
    assert suravaram["filename"] == "Suravaram-Regular.ttf"
    assert suravaram["family_source"] == "font_sfnt_name_table"
    assert baloo["family"] == "Baloo Tammudu 2"
    assert baloo["filename"] == "BalooTammudu2-ExtraBold.ttf"


def test_config_default_path_is_independent_of_current_directory(monkeypatch, tmp_path):
    from src.config import Config

    monkeypatch.chdir(tmp_path)
    cfg = Config.load()
    assert cfg.config_file == (Path(__file__).resolve().parents[1] / "config.json").resolve()
    assert cfg.root == cfg.config_file.parent


def test_manifest_json_schema_matches_runtime_contract():
    from src.storage.json_record import MANIFEST_RECORD_SCHEMA, MANIFEST_SCHEMA_VERSION

    schema = json.loads(Path("schemas/unified_song_master_v2.schema.json").read_text(encoding="utf-8"))
    unified = schema["properties"]["unified_pipeline"]
    manifest = unified["properties"]["manifest"]
    assert unified["properties"]["schema_version"]["const"] == MANIFEST_SCHEMA_VERSION
    assert unified["properties"]["record_schema"]["const"] == MANIFEST_RECORD_SCHEMA
    assert manifest["properties"]["schema_version"]["const"] == MANIFEST_SCHEMA_VERSION
    assert set(("created_at_utc", "algorithm_versions", "artifacts", "validation_status", "content_fingerprint")).issubset(
        manifest["required"]
    )


def test_source_implementations_use_canonical_packages_not_root_aliases():
    source_root = Path("src")
    expected_packages = {
        "providers", "audio", "video", "storage", "orchestration", "common",
        "diagnostics", "alignment", "phase2core", "rendercore", "sql",
    }
    assert expected_packages.issubset({p.name for p in source_root.iterdir() if p.is_dir()})
    root_python = {p.name for p in source_root.glob("*.py")}
    assert root_python == {"__init__.py", "config.py"}
    for obsolete in (
        "acquisition.py", "audio_unified.py", "pipeline.py", "reel.py", "spotify.py",
        "youtube_video.py", "youtube_sync.py", "db_songs.py", "db_reels.py",
    ):
        assert not (source_root / obsolete).exists()
