from __future__ import annotations
from pathlib import Path
import numpy as np
from .utils import run,duration_ms

def _decode(path,sr=8000):
 import soundfile as sf
 raw=Path(path).with_suffix('.offsetsrc.wav')
 run(['ffmpeg','-y','-v','error','-i',str(path),'-ac','1','-ar',str(sr),'-f','wav',str(raw)],timeout=900)
 y,_=sf.read(raw,dtype='float32');raw.unlink(missing_ok=True);return np.asarray(y,dtype=np.float32)
def _norm(x):
 x=x-np.mean(x);n=np.linalg.norm(x);return x/n if n>1e-9 else x
def _env(x,n=256):
 if len(x)<n:return np.array([np.sqrt(np.mean(x*x)+1e-12)])
 z=x[:len(x)//n*n].reshape(-1,n);return np.sqrt(np.mean(z*z,axis=1)+1e-12)
def _feature_score(a,b):
 # Downsample long raw waveforms to keep the global scan bounded while retaining deterministic structure.
 if len(a)>8000:a=a[np.linspace(0,len(a)-1,8000,dtype=int)]
 if len(b)>8000:b=b[np.linspace(0,len(b)-1,8000,dtype=int)]
 na,nb=_norm(a),_norm(b);m=min(len(na),len(nb));
 if m<=10:return 0.0
 return float((np.dot(na[:m],nb[:m])/m+1)/2)
def _scan(ref,yt,sr,max_offset,step_ms=250):
 L=min(len(ref),sr*30);ref=ref[:L];re= _env(ref);rp=np.sign(np.diff(re));best=[]
 for off in range(0,max(0,len(yt)-L)+1,int(step_ms*sr/1000)):
  seg=yt[off:off+L]
  if len(seg)<L:break
  ye=_env(seg);m=min(len(re),len(ye));energy=_feature_score(re[:m],ye[:m]);wave=_feature_score(ref,seg)
  yp=np.sign(np.diff(ye));pv=_feature_score(rp[:min(len(rp),len(yp))],yp)
  # transition score uses normalized first difference of energy envelopes.
  tr=_feature_score(np.diff(re)[:m-1],np.diff(ye)[:m-1]) if m>2 else 0
  total=.40*wave+.30*energy+.15*pv+.15*tr
  best.append({'candidate_offset_ms':int(round(off*1000/sr)),'total_score':total,'waveform_score':wave,'energy_score':energy,'peak_valley_score':pv,'transition_score':tr})
 return sorted(best,key=lambda x:x['total_score'],reverse=True)

def find_offset(original_mp3,youtube_audio):
 ref=_decode(original_mp3);yt=_decode(youtube_audio);cands=_scan(ref,yt,8000,30000)
 valid=None
 for i,c in enumerate(cands):
  if 0<=c['candidate_offset_ms']<=30000: valid=c;break
 for c in cands:
  c['accepted']=bool(valid is c);c['rejection_reason']=None if valid is c else ('offset_above_30000ms' if c['candidate_offset_ms']>30000 else 'lower_score_than_selected')
 if valid is None:raise RuntimeError('YOUTUBE_OFFSET_FAILED: no candidate within hard 0..30000 ms bound')
 return valid,cands
