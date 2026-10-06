from __future__ import annotations
import json, shutil, tempfile
from pathlib import Path
from .hashing import sha256_file
from .model_cache import configure_model_cache

def run_alignment(cfg,work,mp3,lrc,source_json,predecoded_source,precomputed_vocals,logger):
 from .phase2core.config import load_config as p2_load
 from .phase2core.db import Database
 from .phase2core.pipeline import Pipeline as P2Pipeline
 from .phase2core.types import InputPackage
 base=Path(work)/'alignment';base.mkdir(parents=True,exist_ok=True)
 inp=base/'input';inp.mkdir(exist_ok=True);final=base/'final';final.mkdir(exist_ok=True);db=base/'db';db.mkdir(exist_ok=True)
 models=configure_model_cache(cfg.path('paths.models_dir'))
 local_cfg=json.loads((Path(__file__).resolve().parent.parent/'archives'/'phase2'/'phase2_final_build'/'config.json').read_text(encoding='utf-8'))
 local_cfg['paths']={'original_dir':str(inp),'final_dir':str(final),'temp_dir':str(base/'temp'),'db_dir':str(db),'models_dir':str(models)}
 local_cfg['runtime']={**local_cfg.get('runtime',{}),'device':cfg.get('runtime.device','cuda'),'fallback_device':cfg.get('runtime.fallback_device','cpu'),'require_cuda':False,'allow_cpu_fallback':True}
 local_cfg['models']=dict(local_cfg.get('models',{}));local_cfg['models']['demucs_model']=cfg.get('demucs.model','htdemucs')
 local_cfg['audio']=dict(local_cfg.get('audio',{}));local_cfg['audio']['predecoded_source_16k']=str(predecoded_source)
 local_cfg['demucs']={**local_cfg.get('demucs',{}),'precomputed_vocals_path':str(precomputed_vocals),'precomputed_vocals_device':'unified_shared_demucs'}
 local_cfg['project']['pipeline_version']='unified-phase2-adapter-1.3.0'
 cpath=base/'config.json';cpath.write_text(json.dumps(local_cfg,ensure_ascii=False,indent=2),encoding='utf-8')
 p2= p2_load(cpath); dbobj=Database(p2.path('paths.db_dir')/'phase2.sqlite')
 for src,target in ((mp3,inp/'master.mp3'),(lrc,inp/'master.lrc'),(source_json,inp/'master.json')):shutil.copy2(src,target)
 package=InputPackage('master',inp/'master.mp3',inp/'master.lrc',inp/'master.json')
 pipe=P2Pipeline(p2,dbobj,allow_cpu_fallback=True)
 try: result=pipe.process(package,force=False,keep_temp=True)
 finally: dbobj.close()
 out={'mp3':final/'master.mp3','lrc':final/'master.lrc','json':final/'master.json','result':result}
 for k,p in list(out.items())[:3]:
  if not Path(p).exists():raise RuntimeError(f'ALIGNMENT_FAILED: missing {p}')
 return out
