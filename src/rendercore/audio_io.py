from __future__ import annotations
import json, subprocess
from pathlib import Path
import numpy as np
from .utils import Phase3Error, run_cmd

def probe(path:Path)->dict:
    cmd=['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]
    p=run_cmd(cmd,check=True)
    if not p.stdout or not p.stdout.strip():
        raise Phase3Error('FFPROBE_EMPTY_OUTPUT', f'ffprobe returned no JSON output for {path}', details={'stderr': p.stderr[-4000:]})
    try:
        data=json.loads(p.stdout)
    except json.JSONDecodeError as exc:
        raise Phase3Error(
            'FFPROBE_JSON_INVALID',
            f'ffprobe returned invalid JSON for {path}: {exc}',
            details={'stdout': p.stdout[-4000:], 'stderr': p.stderr[-4000:]},
        ) from exc
    streams=data.get('streams',[]); audio=next((s for s in streams if s.get('codec_type')=='audio'),None); video=next((s for s in streams if s.get('codec_type')=='video'),None)
    out={'path':str(path),'duration_ms':int(round(float(data.get('format',{}).get('duration') or (audio or video or {}).get('duration') or 0)*1000)),'size':path.stat().st_size}
    if audio: out.update({'sample_rate':int(audio.get('sample_rate') or 0),'channels':int(audio.get('channels') or 0),'codec':audio.get('codec_name'),'bit_rate':int(float(audio.get('bit_rate') or 0)) if audio.get('bit_rate') else None})
    if video: out.update({'width':int(video.get('width') or 0),'height':int(video.get('height') or 0),'fps':video.get('r_frame_rate'),'video_codec':video.get('codec_name')})
    return out

def decode_wav(path:Path,sr:int=16000,mono:bool=True)->tuple[np.ndarray,int]:
    import soundfile as sf
    # soundfile cannot decode m4a/webm; use ffmpeg when not WAV.
    if path.suffix.lower()=='.wav':
        y,rate=sf.read(str(path),dtype='float32',always_2d=True)
        if rate!=sr:
            from scipy.signal import resample_poly
            g=np.gcd(rate,sr); y=resample_poly(y,sr//g,rate//g,axis=0); rate=sr
        if mono: y=y.mean(axis=1)
        else: y=y.T
        return np.asarray(y,dtype=np.float32),rate
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp=Path(td)/'decode.wav'
        cmd=['ffmpeg','-y','-v','error','-i',str(path),'-ac','1' if mono else '2','-ar',str(sr),str(tmp)]
        run_cmd(cmd)
        return decode_wav(tmp,sr,mono)

def extract_segment(src:Path,dst:Path,start_ms:int,end_ms:int,sample_rate:int=44100,channels:int=2):
    if end_ms<=start_ms: raise ValueError('end must be > start')
    dst.parent.mkdir(parents=True,exist_ok=True)
    cmd=['ffmpeg','-y','-v','error','-ss',f'{start_ms/1000:.6f}','-i',str(src),'-t',f'{(end_ms-start_ms)/1000:.6f}','-ac',str(channels),'-ar',str(sample_rate),'-c:a','pcm_s16le',str(dst)]
    run_cmd(cmd)

def ensure_mono_16k(src:Path,dst:Path):
    dst.parent.mkdir(parents=True,exist_ok=True); run_cmd(['ffmpeg','-y','-v','error','-i',str(src),'-ac','1','-ar','16000','-c:a','pcm_s16le',str(dst)])

def write_float_wav(path:Path,audio:np.ndarray,sr:int):
    import soundfile as sf
    path.parent.mkdir(parents=True,exist_ok=True); sf.write(str(path),audio.astype(np.float32),sr,subtype='PCM_24')

def read_stereo_wav(path:Path)->tuple[np.ndarray,int]:
    import soundfile as sf
    y,sr=sf.read(str(path),dtype='float32',always_2d=True)
    return y.T.astype(np.float32),sr

def write_stereo_wav(path:Path,stereo:np.ndarray,sr:int):
    import soundfile as sf
    sf.write(str(path),stereo.T.astype(np.float32),sr,subtype='PCM_24')

def loudness_normalize(src:Path,dst:Path,target_lufs:float=-14.0,true_peak:float=-1.0):
    dst.parent.mkdir(parents=True,exist_ok=True)
    filt=f'loudnorm=I={target_lufs}:TP={true_peak}:LRA=7:print_format=none'
    run_cmd(['ffmpeg','-y','-v','error','-i',str(src),'-af',filt,'-c:a','pcm_s16le',str(dst)])
