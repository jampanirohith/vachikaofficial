from __future__ import annotations
import hashlib, json, math, os, re, subprocess, time, uuid
from pathlib import Path
from typing import Any, Iterable

class Phase3Error(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message); self.code=code; self.details=details or {}

ERROR_CODES = {
    "INPUT_MP3_MISSING","INPUT_LRC_MISSING","INPUT_JSON_MISSING","INPUT_JSON_INVALID","INPUT_BASENAME_COLLISION",
    "INPUT_PACKAGE_INCOMPLETE","INPUT_CHANGED_DURING_RUN","WORD_TIMELINE_MISSING","YOUTUBE_VIDEO_ID_MISSING",
    "YOUTUBE_VIDEO_UNAVAILABLE","DEMUCS_FAILED","DEMUCS_OOM","HOOK_NO_CANDIDATES","HOOK_INSUFFICIENT_FINALISTS",
    "HOOK_SELECTION_FAILED","YOUTUBE_AUDIO_DOWNLOAD_FAILED","VIDEO_MATCH_LOW_CONFIDENCE","VIDEO_MATCH_INCONSISTENT",
    "VIDEO_SECTION_DOWNLOAD_FAILED","VIDEO_TRIM_FAILED","TRACKING_FAILED","VIDEO_RENDER_FAILED","AUDIO_RENDER_FAILED",
    "LYRIC_RENDER_FAILED","ASSEMBLY_FAILED","OUTPUT_VALIDATION_FAILED","FINALIZATION_FAILED","STATE_RECONCILIATION_REQUIRED",
}

def sha256_file(path: Path, chunk_size: int=1024*1024) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        while True:
            b=f.read(chunk_size)
            if not b: break
            h.update(b)
    return h.hexdigest()

def stable_json_hash(obj: Any) -> str:
    raw=json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',',':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()

def now_iso() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

def uuid4_str() -> str: return str(uuid.uuid4())

def clamp(x: float, lo: float, hi: float) -> float: return max(lo, min(hi, x))

def safe_name(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', s).strip() or 'song'

def parse_timecode(value: str) -> int:
    value=value.strip().replace(',', '.')
    parts=value.split(':')
    if len(parts)==3: h,m,s=parts
    elif len(parts)==2: h='0'; m,s=parts
    elif len(parts)==1: h='0'; m='0'; s=parts[0]
    else: raise ValueError(f'Invalid timecode: {value}')
    sec=float(s); total=(int(h)*3600+int(m)*60+sec)*1000
    if total<0: raise ValueError('negative time')
    return int(round(total))

def fmt_ms(ms: int) -> str:
    ms=max(0,int(ms)); h=ms//3600000; ms%=3600000; m=ms//60000; ms%=60000; s=ms//1000; rem=ms%1000
    return f'{h:02d}:{m:02d}:{s:02d}.{rem:03d}'

def run_cmd(cmd: list[str], *, timeout: float|None=None, cwd: Path|None=None, check: bool=True, env: dict|None=None) -> subprocess.CompletedProcess:
    """Run an external command with Windows-safe UTF-8 decoding.

    FFmpeg/FFprobe may emit UTF-8 metadata (including Telugu text) even when
    Python is running on Windows with a legacy console code page. Explicitly
    decoding stdout/stderr as UTF-8 avoids UnicodeDecodeError in subprocess
    reader threads and keeps diagnostic output usable across platforms.
    """
    p=subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=timeout,
    )
    if check and p.returncode != 0:
        raise Phase3Error(
            'COMMAND_FAILED',
            f'Command failed ({p.returncode}): {cmd[0]}',
            details={'cmd':cmd,'stdout':p.stdout[-4000:],'stderr':p.stderr[-8000:]},
        )
    return p

def which(name: str) -> str|None:
    import shutil; return shutil.which(name)

_FFMPEG_ENCODER_CACHE: dict[str,bool] = {}

def ffmpeg_has_encoder(codec: str) -> bool:
    """Return whether the installed FFmpeg exposes the requested encoder.

    In particular, this detects NVENC support in the actual FFmpeg binary on
    the user's Windows machine instead of assuming that an NVIDIA GPU alone
    guarantees a matching FFmpeg build.
    """
    codec=str(codec).strip()
    if not codec: return False
    if codec in _FFMPEG_ENCODER_CACHE: return _FFMPEG_ENCODER_CACHE[codec]
    if which('ffmpeg') is None:
        _FFMPEG_ENCODER_CACHE[codec]=False
        return False
    try:
        p=run_cmd(['ffmpeg','-hide_banner','-encoders'],timeout=30,check=False)
        found=any(line.strip().split() and codec in line.split() for line in p.stdout.splitlines())
    except Exception:
        found=False
    _FFMPEG_ENCODER_CACHE[codec]=found
    return found

def nvenc_video_args(cfg, codec: str = 'h264_nvenc') -> list[str]:
    """Return conservative NVENC arguments compatible with older Windows FFmpeg builds.

    Some FFmpeg/NVENC builds expose advanced options with different spellings or
    not at all. The core h264_nvenc rate-control options below are intentionally
    kept to the broadly supported set. This avoids a failed encoder startup
    before raw video frames can be consumed.
    """
    if codec != 'h264_nvenc':
        return []
    args = [
        '-c:v', 'h264_nvenc',
        '-preset', str(cfg.get('render.nvenc_preset', cfg.get('render.preset', 'p4'))),
        '-rc', 'vbr',
        '-cq', str(cfg.get('render.crf', 18)),
        '-b:v', '0',
        '-pix_fmt', 'yuv420p',
    ]
    gpu_index = cfg.get('render.nvenc_gpu', None)
    if gpu_index not in (None, '', 'auto'):
        args[2:2] = ['-gpu', str(gpu_index)]
    return args


def json_dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def json_load(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))

def robust_zscore(values: list[float]) -> list[float]:
    if not values: return []
    import numpy as np
    a=np.asarray(values,dtype=float); med=float(np.median(a)); mad=float(np.median(np.abs(a-med)))
    scale=max(mad*1.4826, 1e-9); return np.clip((a-med)/(4*scale)+0.5,0,1).tolist()

def percentile_ranks(values: list[float]) -> list[float]:
    if not values: return []
    import numpy as np
    a=np.asarray(values,dtype=float); order=np.argsort(np.argsort(a,kind='mergesort'),kind='mergesort')
    denom=max(len(a)-1,1); return (order/denom).tolist()

def atomic_replace(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True); os.replace(src,dst)
