from __future__ import annotations
import json, math, subprocess
from pathlib import Path
import numpy as np
import soundfile as sf
from .hashing import sha256_file
from .utils import run,duration_ms

STEMS=('vocals','drums','bass','other')

def _fit_stems(arrays):
 # Demucs stems can differ by a small number of samples after decoding/resampling.
 # Establish one canonical frame count and crop every stem to it before any mixing.
 if not arrays or any(a.ndim != 2 for a in arrays.values()):
  raise RuntimeError('8D_INPUT_INVALID: stems must be 2-D channel x frame arrays')
 n=min(a.shape[1] for a in arrays.values())
 if n <= 0:
  raise RuntimeError('8D_INPUT_INVALID: stems contain no audio frames')
 return {name: a[:, :n] for name, a in arrays.items()}, n
def _read(path,sr=44100):
 y,orig=sf.read(path,dtype='float32',always_2d=True)
 if orig!=sr:
  from scipy.signal import resample_poly
  g=math.gcd(orig,sr);y=resample_poly(y,sr//g,orig//g,axis=0).astype('float32');orig=sr
 return y.T

def _eq_pan(x,p):
 th=(p+1)*math.pi/4;return np.vstack((x*np.cos(th),x*np.sin(th)))
def _lfo(n,sr,f,amp,phase=0):return np.clip(np.sin(2*np.pi*f*np.arange(n,dtype=np.float32)/sr+phase)*amp,-1,1)
def _smooth(x,win):
 if win<=1:return x
 k=np.ones(win,dtype=np.float32)/win;return np.convolve(x,k,'same')

def _stem_paths(stems):
 # The Demucs result is a mixed metadata/path mapping and includes keys such as
 # device, model, run_id, and manifest. Only the four actual audio stems belong
 # in the 8D renderer or its output manifest. Never hash metadata values as files.
 missing=[s for s in STEMS if s not in stems]
 if missing:
  raise RuntimeError(f'8D_INPUT_INVALID: missing stems={missing}')
 return {s:Path(stems[s]) for s in STEMS}

def render(cfg,work,stems,expected_duration_ms,words,source_mp3):
 work=Path(work);outdir=work/'audio'/'8d';outdir.mkdir(parents=True,exist_ok=True);sr=int(cfg.get('audio_8d.sample_rate',44100));stem_paths=_stem_paths(stems); arrays={s:_read(stem_paths[s],sr) for s in STEMS};arrays,n=_fit_stems(arrays)
 # Activity/lyric density timeline drives width, while slow LFO keeps movement smooth.
 # Use ceil(n/block): floor division would make the repeated density/gain vector
 # shorter than the audio whenever n is not an exact block multiple.
 block=max(1,int(sr*0.1));blocks=max(1,int(math.ceil(n/block)));density=np.zeros(blocks,dtype=np.float32)
 for w in words or []:
  t=int(w.get('start_ms',0));i=min(blocks-1,max(0,int(t/100)));density[i]+=1
 density=_smooth(density,9);density/=max(float(density.max()),1.0);d=np.repeat(density,block)[:n]
 # Whole-song LFO; no pitch/time modification.
 other=_eq_pan(arrays['other'].mean(axis=0),_lfo(n,sr,float(cfg.get('audio_8d.other_frequency_hz',0.075)),float(cfg.get('audio_8d.other_width',0.75))))
 # Wider spatial send when lyric density / musical activity is high, still smooth.
 center=float(cfg.get('audio_8d.vocal_center',0.98));voc=_eq_pan(arrays['vocals'].mean(axis=0),0.0)*center
 bass=_eq_pan(arrays['bass'].mean(axis=0),float(cfg.get('audio_8d.bass_pan',0.0)))
 drums0=arrays['drums'][:,:n];dmix=0.88*drums0.mean(axis=0)[None,:].repeat(2,axis=0)+0.12*drums0[:2]
 dp=_lfo(n,sr,float(cfg.get('audio_8d.drums_frequency_hz',0.045)),float(cfg.get('audio_8d.drums_width',0.14)),0.7);dmix=0.9*dmix+0.1*_eq_pan(drums0.mean(axis=0),dp)
 mix=voc+bass+dmix+other
 # Subtle dynamic gain from word density; prevents movement from becoming distracting during sparse areas.
 gain=0.92+0.08*d;mix*=gain[None,:]
 fi=min(n,int(sr*max(0,float(cfg.get('audio_8d.fade_in_ms',100)))/1000));fo=min(n,int(sr*max(0,float(cfg.get('audio_8d.fade_out_ms',250)))/1000))
 if fi:mix[:,:fi]*=np.linspace(0,1,fi,dtype=np.float32)[None,:]
 if fo:mix[:,-fo:]*=np.linspace(1,0,fo,dtype=np.float32)[None,:]
 peak=float(np.max(np.abs(mix)) or 1);peak_after=min(1.0,0.95/peak)*peak
 mix*=min(1.0,0.95/peak)
 mono=(mix[0]+mix[1])*0.5
 stereo_rms=float(np.sqrt(np.mean(mix*mix))+1e-12);mono_rms=float(np.sqrt(np.mean(mono*mono))+1e-12)
 mono_ratio=mono_rms/stereo_rms
 if mix.shape[0] != 2: raise RuntimeError('8D_VALIDATION_FAILED: output is not stereo')
 if not np.isfinite(mix).all() or not np.isfinite(mono_ratio): raise RuntimeError('8D_VALIDATION_FAILED: non-finite audio')
 min_ratio=float(cfg.get('audio_8d.min_mono_compatibility_ratio',0.20))
 if mono_ratio < min_ratio: raise RuntimeError(f'8D_VALIDATION_FAILED: weak mono compatibility ratio={mono_ratio:.4f} < {min_ratio:.4f}')
 wav=outdir/'SongName_8D.wav';sf.write(wav,mix.T,sr,subtype='PCM_24')
 mp3=outdir/'SongName_8D.mp3';run(['ffmpeg','-y','-v','error','-i',str(wav),'-c:a','libmp3lame','-b:a',str(cfg.get('audio_8d.bitrate','320k')),'-map_metadata','0','-id3v2_version','4',str(mp3)],timeout=1800)
 actual_duration_ms=duration_ms(mp3)
 if abs(actual_duration_ms-expected_duration_ms)>400:raise RuntimeError(f'8D duration mismatch: expected {expected_duration_ms}, got {actual_duration_ms}')
 manifest={'engine_version':'unified-8d-v2','scope':'whole_song','config_hash':cfg.get('_config_hash'),'source_mp3_sha256':sha256_file(source_mp3),'stems':{k:sha256_file(v) for k,v in stem_paths.items()},'duration_ms':int(expected_duration_ms),'sample_rate':sr,'stem_roles':{'vocals':'stable_center','bass':'center_mono_compatible','drums':'center_weighted_slow_motion','other':'main_smooth_lfo_motion'},'pitch_or_speed_change':False,'lyric_density_used':bool(words),'output_path':str(mp3),'output_sha256':sha256_file(mp3)}
 (outdir/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8');return mp3,manifest
