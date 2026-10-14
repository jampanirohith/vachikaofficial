from __future__ import annotations
import hashlib,json
from pathlib import Path

def sha256_file(path,chunk=1024*1024):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  while b:=f.read(chunk):h.update(b)
 return h.hexdigest()
def stable_json_hash(obj):return hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
