from __future__ import annotations
import json,re,subprocess,os,tempfile
from pathlib import Path

def atomic_write_text(path,text):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 fd,tmp=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent);os.close(fd)
 Path(tmp).write_text(text,encoding='utf-8');os.replace(tmp,path)
def atomic_write_json(path,obj):atomic_write_text(path,json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n')
def parse_timecode(s):
 s=str(s).strip();m=re.fullmatch(r'(\d+):(\d{2})(?:\.(\d{1,3}))?',s)
 if not m:raise ValueError('Expected MM:SS.xxx')
 ms=int(m.group(1))*60000+int(m.group(2))*1000+int((m.group(3) or '0').ljust(3,'0'));return ms
def fmt_ms(ms):
 ms=int(ms);return f'{ms//60000:02d}:{(ms%60000)//1000:02d}.{ms%1000:03d}'
def safe_name(x,max_len=180):
 x=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',str(x or '').strip());x=re.sub(r'\s+',' ',x).strip(' .');x=x or 'Untitled';return x[:max_len]
def run(cmd,timeout=1800,cwd=None,env=None):
 p=subprocess.run(cmd,cwd=str(cwd) if cwd else None,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout)
 if p.returncode!=0:raise RuntimeError(f'command failed ({p.returncode}): {p.stderr[-4000:]}')
 return p
def ffprobe(path):
 p=run(['ffprobe','-v','error','-print_format','json','-show_format','-show_streams',str(path)],timeout=120);return json.loads(p.stdout)
def duration_ms(path):
 d=ffprobe(path).get('format',{}).get('duration');return int(round(float(d)*1000)) if d else 0
