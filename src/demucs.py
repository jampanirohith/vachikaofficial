from __future__ import annotations
import shutil, subprocess, uuid, json, os
from pathlib import Path
from .hashing import sha256_file
from .utils import run
from .model_cache import configure_model_cache

class DemucsRunner:
 STEMS=('vocals','drums','bass','other')
 def __init__(self,cfg,work,logger,model_cache_dir=None):
  self.cfg=cfg;self.work=Path(work);self.logger=logger
  configured=cfg.get('paths.models_dir') if model_cache_dir is None else model_cache_dir
  self.model_cache_dir=Path(configured) if configured else None
 def _cuda_ok(self):
  try:
   import torch;return bool(torch.cuda.is_available())
  except Exception:return False
 def _run(self,source,outroot,device,segment=None):
  py=shutil.which('python') or shutil.which('python3') or 'python'
  env=None
  if self.model_cache_dir is not None:
   configure_model_cache(self.model_cache_dir)
   env=dict(os.environ)
  cmd=[py,'-m','demucs.separate','-n',str(self.cfg.get('demucs.model','htdemucs')),'--float32','--device',device,'-o',str(outroot)]
  if segment:cmd+=['--segment',str(int(segment))]
  cmd.append(str(source));p=run(cmd,timeout=int(self.cfg.get('demucs.timeout_seconds',7200)),env=env);return p
 def run(self,source):
  out=self.work/'audio'/'stems';out.mkdir(parents=True,exist_ok=True)
  expected={s:out/f'{s}.wav' for s in self.STEMS}
  if all(x.exists() and x.stat().st_size>1000 for x in expected.values()):
   manifest_path=out/'demucs_manifest.json'
   manifest={}
   if manifest_path.exists():
    try: manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    except Exception: manifest={}
   rid=str(manifest.get('run_id') or uuid.uuid4().hex)
   # Reused stems are still a per-song run identity. Never use a shared literal
   # such as 'reused' because demucs_runs.run_id is a database primary key.
   if not manifest_path.exists() or manifest.get('run_id')!=rid:
    manifest={'run_id':rid,'device':manifest.get('device','reused'),'model':self.cfg.get('demucs.model','htdemucs'),'attempts':manifest.get('attempts',[]),'source_sha256':sha256_file(source),'stems':{k:{'path':str(v),'sha256':sha256_file(v)} for k,v in expected.items()}}
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
   return {**{k:str(v) for k,v in expected.items()},'device':manifest.get('device','reused'),'model':manifest.get('model',self.cfg.get('demucs.model','htdemucs')),'run_id':rid,'manifest':str(manifest_path)}
  req=str(self.cfg.get('runtime.device','cpu')).lower();allow=bool(self.cfg.get('runtime.allow_cpu_fallback',True));fallback=str(self.cfg.get('runtime.fallback_device','cpu')).lower()
  if req=='cuda' and not self._cuda_ok():
   if allow and fallback != 'cuda':
    req=fallback
   else:
    # CPU is the supported fallback/default runtime. Never fail the unified
    # pipeline merely because a CUDA-enabled PyTorch build is unavailable.
    req=fallback
  attempts=[];root=self.work/'audio'/'demucs_raw';root.mkdir(parents=True,exist_ok=True);chosen=req
  try:self._run(source,root,req,self.cfg.get('demucs.segment_seconds'));attempts.append({'device':req,'status':'success'})
  except Exception as e:
   attempts.append({'device':req,'status':'failed','error':str(e)})
   if req=='cuda' and 'out of memory' in str(e).lower() and self.cfg.get('demucs.retry_segment_seconds',8):
    try:self._run(source,root,'cuda',self.cfg.get('demucs.retry_segment_seconds',8));attempts.append({'device':'cuda','segment':'retry','status':'success'});chosen='cuda'
    except Exception as e2:attempts.append({'device':'cuda','segment':'retry','status':'failed','error':str(e2)});e=e2
   if not any(a.get('status')=='success' for a in attempts):
    if allow and fallback != req:
     try:
      self._run(source,root,fallback,self.cfg.get('demucs.segment_seconds'));attempts.append({'device':fallback,'status':'success','reason':'configured_cpu_fallback'});chosen=fallback
     except Exception as e3:
      attempts.append({'device':fallback,'status':'failed','error':str(e3)})
      raise RuntimeError(f'DEMUCS_FAILED: {e3}') from e3
    else:raise RuntimeError(f'DEMUCS_FAILED: {e}')
  found=list(root.rglob('*.wav'))
  for s in self.STEMS:
   c=next((p for p in found if p.stem.lower()==s),None)
   if c is None:raise RuntimeError(f'DEMUCS_FAILED: missing {s}.wav')
   shutil.copy2(c,expected[s])
  rid=uuid.uuid4().hex
  manifest={'run_id':rid,'device':chosen,'model':self.cfg.get('demucs.model','htdemucs'),'attempts':attempts,'source_sha256':sha256_file(source),'stems':{k:{'path':str(v),'sha256':sha256_file(v)} for k,v in expected.items()}}
  (out/'demucs_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8');return {**{k:str(v) for k,v in expected.items()},'device':chosen,'model':self.cfg.get('demucs.model','htdemucs'),'run_id':rid,'manifest':str(out/'demucs_manifest.json')}
