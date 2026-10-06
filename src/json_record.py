from __future__ import annotations
import copy, json
from pathlib import Path
from .hashing import sha256_file, stable_json_hash
from .utils import atomic_write_json

def build(source_json, **namespaces):
 data=copy.deepcopy(source_json) if isinstance(source_json,dict) else {}
 unified=data.setdefault('unified_pipeline',{})
 for k,v in namespaces.items(): unified[k]=v
 unified['record_schema']='unified_song_master_v1'
 return data

def write(path,data):
 atomic_write_json(path,data);return sha256_file(path)
