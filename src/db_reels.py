from __future__ import annotations
import sqlite3,uuid
from pathlib import Path
class ReelsDB:
 DB_SCHEMA_NAME="unified_reels"
 DB_SCHEMA_VERSION="4.0.0"
 DB_USER_VERSION=400
 def __init__(self,path,schema_path=None):
  self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.schema_path=Path(schema_path) if schema_path else Path(__file__).resolve().parent.parent/'database'/'reels_schema.sql';self.initialize()
 def connect(self):
  c=sqlite3.connect(self.path,timeout=60);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA busy_timeout=60000');c.execute('PRAGMA journal_mode=DELETE');c.execute('PRAGMA synchronous=FULL');return c
 def initialize(self):
  with self.connect() as c:c.executescript(self.schema_path.read_text(encoding='utf-8'));c.execute('PRAGMA user_version=400');c.commit()
 def one(self,q,args=()):
  with self.connect() as c:r=c.execute(q,args).fetchone();return dict(r) if r else None
 def ensure(self,key,song_key):
  with self.connect() as c:c.execute('INSERT OR IGNORE INTO reels(reel_key,song_key,reel_version,pipeline_status,created_at,updated_at) VALUES(?,?,1,"pending",CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)',(key,song_key));c.commit()
 def update(self,key,**fields):
  allowed={'pipeline_status','quality_status','hook_start_ms','hook_end_ms','hook_duration_ms','youtube_video_id','youtube_offset_ms','youtube_mapped_start_ms','youtube_mapped_end_ms','eight_d_source_path','eight_d_source_sha256','eight_d_whole_song_path','eight_d_whole_song_sha256','final_reel_path','final_reel_json_path','final_reel_sha256','output_duration_ms','output_width','output_height','fps','video_encoder','audio_codec','audio_channels','terminal','terminal_reason'}
  fs={k:(str(v) if isinstance(v,Path) else v) for k,v in fields.items() if k in allowed}
  if not fs:return
  sets=', '.join(f'{k}=?' for k in fs)+', updated_at=CURRENT_TIMESTAMP'
  with self.connect() as c:c.execute(f'UPDATE reels SET {sets} WHERE reel_key=?',[*fs.values(),key]);c.commit()
 def upsert(self,table,pk,key,data):
  with self.connect() as c:
   cols={r['name'] for r in c.execute(f'PRAGMA table_info({table})')};d={pk:str(key),**{k:(str(v) if isinstance(v,Path) else v) for k,v in data.items() if k in cols and k!=pk}}
   for required in ('created_at','entered_at','started_at'):
    if required in cols and required not in d:d[required]='__CURRENT_TIMESTAMP__'
   cols_sql=[]; vals_sql=[]; args=[]
   for col,val in d.items():
    cols_sql.append(col)
    if val=='__CURRENT_TIMESTAMP__': vals_sql.append('CURRENT_TIMESTAMP')
    else: vals_sql.append('?'); args.append(val)
   sql=f'INSERT INTO {table}({", ".join(cols_sql)}) VALUES({", ".join(vals_sql)}) ON CONFLICT({pk}) DO UPDATE SET '+', '.join(f'{k}=excluded.{k}' for k in d if k!=pk)
   c.execute(sql,args);c.commit()
 def audit(self,key,event,stage=None,status=None,message='',error_code=None):
  with self.connect() as c:c.execute('INSERT INTO reel_audit_events(reel_key,event_type,stage_name,status,message,error_code) VALUES(?,?,?,?,?,?)',(key,event,stage,status,message,error_code));c.commit()
