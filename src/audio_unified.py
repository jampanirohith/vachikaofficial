from __future__ import annotations
from pathlib import Path
import json, os
from mutagen.id3 import ID3
from mutagen.mp3 import MP3
from .utils import run, duration_ms
from .hashing import sha256_file


def _atomic_ffmpeg_wav(cmd, destination: Path, timeout=900) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    suffix = destination.suffix or '.wav'
    tmp = destination.with_name(f'.{destination.stem}.tmp-{os.getpid()}{suffix}')
    run([str(x) for x in (cmd[:-1] + [str(tmp)]) if x != 'PLACEHOLDER'], timeout=timeout)
    if not tmp.exists() or tmp.stat().st_size < 10000:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f'AUDIO_DECODE_FAILED: FFmpeg produced no usable WAV at {tmp}')
    try:
        tmp.replace(destination)
    finally:
        tmp.unlink(missing_ok=True)
    return destination


def prepare_full_source(mp3: Path, work: Path):
    """Decode the source MP3 once into a full-resolution lossless PCM WAV.

    The WAV keeps the source MP3's sample-rate/channel layout so Demucs operates
    on a lossless working representation. Downstream alignment derives its 16 kHz
    mono working audio from this WAV instead of decoding the MP3 again.
    """
    mp3 = Path(mp3); work = Path(work)
    out = work / 'audio' / 'source_full.wav'
    out.parent.mkdir(parents=True, exist_ok=True)
    info = MP3(mp3, ID3=ID3)
    sample_rate = int(getattr(info.info, 'sample_rate', 0) or 0)
    channels = int(getattr(info.info, 'channels', 0) or 0)
    if sample_rate <= 0 or channels <= 0:
        raise RuntimeError(f'SOURCE_DECODE_FAILED: invalid source MP3 sample layout sr={sample_rate} channels={channels}')
    manifest_path=out.parent/'source_full_manifest.json'
    valid_cached=False
    if out.exists() and out.stat().st_size >= 10000 and manifest_path.exists():
        try:
            cached=json.loads(manifest_path.read_text(encoding='utf-8'))
            valid_cached=(cached.get('source_mp3_sha256')==sha256_file(mp3) and cached.get('sha256')==sha256_file(out))
        except Exception:
            valid_cached=False
    if not valid_cached:
        _atomic_ffmpeg_wav([
            'ffmpeg','-y','-v','error','-i',str(mp3),'-vn','-map','0:a:0',
            '-ar',str(sample_rate),'-ac',str(channels),'-c:a','pcm_s24le','-f','wav','PLACEHOLDER'
        ], out, timeout=900)
    decoded_ms = duration_ms(out); source_ms = duration_ms(mp3)
    if decoded_ms <= 0 or abs(decoded_ms - source_ms) > 500:
        raise RuntimeError(f'SOURCE_DECODE_FAILED: duration mismatch source={source_ms} decoded={decoded_ms}')
    meta = {
        'path': str(out), 'sha256': sha256_file(out), 'duration_ms': decoded_ms,
        'sample_rate': sample_rate, 'channels': channels, 'source_mp3_sha256': sha256_file(mp3),
        'decode_once': True,
    }
    manifest_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding='utf-8')
    return out, meta


def prepare_source(source: Path, work: Path):
    """Derive the 16 kHz mono lossless alignment representation from working audio."""
    source = Path(source); work = Path(work)
    out = work/'audio'/'source_16k.wav'; out.parent.mkdir(parents=True,exist_ok=True)
    if not out.exists() or out.stat().st_size<10000:
        _atomic_ffmpeg_wav([
            'ffmpeg','-y','-v','error','-i',str(source),'-vn','-map','0:a:0',
            '-ar','16000','-ac','1','-c:a','pcm_s16le','-f','wav','PLACEHOLDER'
        ], out, timeout=900)
    d=duration_ms(out);src=duration_ms(source)
    if d<=0 or abs(d-src)>500: raise RuntimeError(f'SOURCE_DECODE_FAILED: duration mismatch source={src} decoded={d}')
    meta={'path':str(out),'sha256':sha256_file(out),'duration_ms':d,'sample_rate':16000,'channels':1,'derived_from':str(source),'derived_from_sha256':sha256_file(source)}
    (out.parent/'source_manifest.json').write_text(json.dumps(meta,indent=2,ensure_ascii=False),encoding='utf-8'); return out,meta
