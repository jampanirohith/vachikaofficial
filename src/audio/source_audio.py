from __future__ import annotations
from pathlib import Path
import json, os
from mutagen.id3 import ID3
from mutagen.mp3 import MP3
from src.common.utils import run, duration_ms
from src.common.hashing import sha256_file


def _atomic_ffmpeg_wav(cmd, destination: Path, timeout=900) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    suffix = destination.suffix or '.wav'
    tmp = destination.with_name(f'.{destination.stem}.tmp-{os.getpid()}{suffix}')
    try:
        run([str(x) for x in (cmd[:-1] + [str(tmp)]) if x != 'PLACEHOLDER'], timeout=timeout)
        if not tmp.exists() or tmp.stat().st_size < 10000:
            raise RuntimeError(f'AUDIO_DECODE_FAILED: FFmpeg produced no usable WAV at {tmp}')
        tmp.replace(destination)
    finally:
        # FFmpeg failures/timeouts may leave a partial temporary output behind.
        tmp.unlink(missing_ok=True)
    return destination


def prepare_full_source(mp3: Path, work: Path):
    """Decode the source MP3 once into a full-resolution lossless PCM WAV.

    Cache identity includes source bytes, source layout and decode policy. The WAV
    keeps the MP3 sample-rate/channel layout so Demucs uses a lossless working
    representation; a stale/corrupt cache is rebuilt rather than trusted.
    """
    mp3 = Path(mp3); work = Path(work)
    if not mp3.is_file():
        raise RuntimeError(f'SOURCE_DECODE_FAILED: input MP3 does not exist: {mp3}')
    out = work / 'audio' / 'source_full.wav'
    out.parent.mkdir(parents=True, exist_ok=True)
    info = MP3(mp3, ID3=ID3)
    sample_rate = int(getattr(info.info, 'sample_rate', 0) or 0)
    channels = int(getattr(info.info, 'channels', 0) or 0)
    if sample_rate <= 0 or channels <= 0:
        raise RuntimeError(f'SOURCE_DECODE_FAILED: invalid source MP3 sample layout sr={sample_rate} channels={channels}')
    source_hash = sha256_file(mp3)
    source_ms = duration_ms(mp3)
    derivation = {
        'schema_version': 2,
        'derivation_version': 'ffmpeg-full-resolution-pcm-s24le-v2',
        'source_mp3_sha256': source_hash,
        'sample_rate': sample_rate,
        'channels': channels,
        'codec': 'pcm_s24le',
    }
    manifest_path = out.parent / 'source_full_manifest.json'
    valid_cached = False
    if out.exists() and out.stat().st_size >= 10000 and manifest_path.exists():
        try:
            cached = json.loads(manifest_path.read_text(encoding='utf-8'))
            cached_duration = duration_ms(out)
            valid_cached = (
                all(cached.get(k) == v for k, v in derivation.items())
                and cached.get('sha256') == sha256_file(out)
                and cached_duration > 0
                and abs(cached_duration - source_ms) <= 500
                and abs(int(cached.get('duration_ms') or 0) - cached_duration) <= 500
            )
        except Exception:
            # A partially written/corrupt cache can make FFprobe fail; treat that
            # as an invalid cache entry and rebuild from the authoritative source.
            valid_cached = False
    if not valid_cached:
        out.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        _atomic_ffmpeg_wav([
            'ffmpeg', '-y', '-v', 'error', '-i', str(mp3), '-vn', '-map', '0:a:0',
            '-ar', str(sample_rate), '-ac', str(channels), '-c:a', 'pcm_s24le', '-f', 'wav', 'PLACEHOLDER'
        ], out, timeout=900)
    decoded_ms = duration_ms(out)
    if decoded_ms <= 0 or abs(decoded_ms - source_ms) > 500:
        out.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        raise RuntimeError(f'SOURCE_DECODE_FAILED: duration mismatch source={source_ms} decoded={decoded_ms}')
    meta = {
        **derivation,
        'path': str(out), 'sha256': sha256_file(out), 'duration_ms': decoded_ms,
        'decode_once': True,
    }
    tmp_manifest = manifest_path.with_name(manifest_path.name + '.tmp')
    tmp_manifest.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding='utf-8')
    tmp_manifest.replace(manifest_path)
    return out, meta


def prepare_source(source: Path, work: Path):
    """Create/reuse a 16 kHz mono WAV only when its provenance is valid.

    The cache manifest binds the normalized file to the exact source content and
    derivation settings. A source replacement at the same path can therefore never
    silently reuse the old WAV.
    """
    source = Path(source)
    work = Path(work)
    if not source.exists() or not source.is_file():
        raise RuntimeError(f'SOURCE_DECODE_FAILED: input audio does not exist: {source}')
    out = work / 'audio' / 'source_16k.wav'
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = out.parent / 'source_manifest.json'
    source_hash = sha256_file(source)
    source_ms = duration_ms(source)
    derivation = {
        'schema_version': 2,
        'sample_rate': 16000,
        'channels': 1,
        'codec': 'pcm_s16le',
        'derivation_version': 'ffmpeg-mono-16k-v2',
        'derived_from': str(source.resolve()),
        'derived_from_sha256': source_hash,
    }
    cached_valid = False
    if out.exists() and out.stat().st_size >= 10000 and manifest_path.exists():
        try:
            cached = json.loads(manifest_path.read_text(encoding='utf-8'))
            cached_duration = duration_ms(out)
            cached_valid = (
                all(cached.get(k) == v for k, v in derivation.items())
                and cached.get('sha256') == sha256_file(out)
                and cached_duration > 0
                and abs(cached_duration - source_ms) <= 500
                and abs(int(cached.get('duration_ms') or 0) - cached_duration) <= 500
            )
        except Exception:
            # Corrupt media often raises subprocess/probe errors outside JSON/I/O
            # exceptions. Cache validation must fall through to a clean re-decode.
            cached_valid = False
    if not cached_valid:
        out.unlink(missing_ok=True)
        # Manifest is invalidated before replacement so an interrupted decode cannot
        # leave a stale manifest authorizing a partially replaced WAV.
        manifest_path.unlink(missing_ok=True)
        _atomic_ffmpeg_wav([
            'ffmpeg', '-y', '-v', 'error', '-i', str(source), '-vn', '-map', '0:a:0',
            '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', '-f', 'wav', 'PLACEHOLDER'
        ], out, timeout=900)
    decoded_ms = duration_ms(out)
    if decoded_ms <= 0 or abs(decoded_ms - source_ms) > 500:
        out.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        raise RuntimeError(f'SOURCE_DECODE_FAILED: duration mismatch source={source_ms} decoded={decoded_ms}')
    meta = {
        **derivation,
        'path': str(out),
        'sha256': sha256_file(out),
        'duration_ms': decoded_ms,
        'created_by': 'src.audio.source_audio.prepare_source',
    }
    tmp_manifest = manifest_path.with_name(manifest_path.name + '.tmp')
    tmp_manifest.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding='utf-8')
    tmp_manifest.replace(manifest_path)
    return out, meta
