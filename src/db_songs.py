from __future__ import annotations
import sqlite3, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .metadata import normalize_isrc

class SongsDB:
    def __init__(self,path,schema_path=None):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.schema_path=Path(schema_path) if schema_path else Path(__file__).resolve().parent.parent/'database'/'songs_schema.sql'
        self.initialize()
    def connect(self):
        c=sqlite3.connect(self.path,timeout=60);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA busy_timeout=60000');c.execute('PRAGMA synchronous=FULL');return c
    DB_SCHEMA_NAME = 'unified_songs'
    DB_SCHEMA_VERSION = '4.0.0'
    DB_USER_VERSION = 400
    def _schema_identity(self):
        if not self.path.exists() or self.path.stat().st_size == 0:return None,None
        try:
            with self.connect() as c:
                rows=dict(c.execute("SELECT key,value FROM schema_meta WHERE key IN ('schema_name','schema_version')").fetchall())
                return rows.get('schema_name'),rows.get('schema_version')
        except sqlite3.DatabaseError:return None,None
    def _quarantine_legacy(self):
        legacy=self.path.with_name(self.path.stem+'.legacy.db');idx=1
        while legacy.exists():
            legacy=self.path.with_name(self.path.stem+f'.legacy-{idx}.db');idx+=1
        self.path.replace(legacy);return legacy
    def initialize(self):
        name,version=self._schema_identity()
        if name and (name!=self.DB_SCHEMA_NAME or version!=self.DB_SCHEMA_VERSION):self._quarantine_legacy()
        elif self.path.exists() and self.path.stat().st_size>0 and not name:self._quarantine_legacy()
        with self.connect() as c:
            c.executescript(self.schema_path.read_text(encoding='utf-8'));c.execute(f'PRAGMA user_version={self.DB_USER_VERSION}');c.commit()
    def one(self,q,args=()):
        with self.connect() as c:
            r=c.execute(q,args).fetchone();return dict(r) if r else None
    def ensure_song(self,rec):
        self._upsert_song(rec)
        return self.one('SELECT * FROM songs WHERE song_key=?',(rec['song_key'],))
    def _upsert_song(self,rec):
        allowed={'song_key','playlist_serial','spotify_track_id','canonical_isrc','ytm_video_id','ytm_url','basename','title','artist','album','duration_ms','source_mp3_path','source_lrc_path','source_json_path','final_mp3_path','final_lrc_path','final_wordlevel_lrc_path','final_json_path','final_8d_mp3_path','source_mp3_sha256','source_lrc_sha256','source_json_sha256','pipeline_status','quality_status','terminal','terminal_reason'}
        d={k:(str(v) if isinstance(v,Path) else v) for k,v in rec.items() if k in allowed};cols=list(d)
        # The songs table is the parent of stage_runs/audit_events and has required
        # bootstrap fields. Keep the entire existence check/upsert in one connection so
        # Windows SQLite cannot encounter a nested reader while a writer transaction is open.
        d.setdefault("basename", d.get("title") or f"{d.get('song_key','song')}")
        d.setdefault("pipeline_status", "PROCESSING")
        # Frozen Spotify playlist rows may omit catalog duration. Keep the parent row
        # schema-safe with a temporary zero; authoritative Spotify duration is populated later.
        if d.get("duration_ms") is None:
            d["duration_ms"] = 0
        d.setdefault("terminal", 0)
        with self.connect() as c:
            existing = c.execute('SELECT song_key FROM songs WHERE song_key=?',(d['song_key'],)).fetchone()
            if existing:
                sets=', '.join(f'{k}=?' for k in cols if k!='song_key')
                args=[d[k] for k in cols if k!='song_key']+[d['song_key']]
                if sets:c.execute(f'UPDATE songs SET {sets},updated_at=CURRENT_TIMESTAMP WHERE song_key=?',args)
            else:
                c.execute(f"INSERT INTO songs({','.join(d.keys())}) VALUES({','.join('?' for _ in d)})",[d[k] for k in d])
            c.commit()
    def update_song(self,key,**fields):
        allowed={'playlist_serial','spotify_track_id','canonical_isrc','ytm_video_id','ytm_url','basename','title','artist','album','duration_ms','source_mp3_path','source_lrc_path','source_json_path','final_mp3_path','final_lrc_path','final_wordlevel_lrc_path','final_json_path','final_8d_mp3_path','source_mp3_sha256','source_lrc_sha256','source_json_sha256','pipeline_status','quality_status','terminal','terminal_reason'}
        d={k:(str(v) if isinstance(v,Path) else v) for k,v in fields.items() if k in allowed}
        if not d:return
        sets=', '.join(f'{k}=?' for k in d);args=list(d.values())+[key]
        with self.connect() as c:c.execute(f'UPDATE songs SET {sets},updated_at=CURRENT_TIMESTAMP WHERE song_key=?',args);c.commit()
    def isrc_match(self,isrc,exclude=None):
        isrc=normalize_isrc(isrc)
        if not isrc:return None
        q="SELECT * FROM songs WHERE canonical_isrc=? AND pipeline_status='FINALIZED'"
        args=[isrc]
        if exclude:q+=' AND song_key<>?';args.append(exclude)
        q+=' ORDER BY created_at LIMIT 1';return self.one(q,args)
    def record_isrc_duplicate(self,key,dup_key,isrc,decision=None):
        with self.connect() as c:c.execute('INSERT INTO isrc_duplicates(song_key,duplicate_song_key,isrc,decision) VALUES(?,?,?,?)',(key,dup_key,isrc,decision));c.commit()
    def upsert_metadata(self,key,values):self._upsert('metadata','song_key',key,values)
    def upsert_source(self,key,values):self._upsert('source_metadata','song_key',key,values)
    def _upsert(self,table,pk,key,values):
        allowed=self._columns(table);d={k:(str(v) if isinstance(v,Path) else v) for k,v in values.items() if k in allowed and k!=pk};d[pk]=str(key);
        if 'created_at' in allowed and not d.get('created_at'):
            d['created_at']=datetime.now(timezone.utc).isoformat()
        cols=list(d);upd=', '.join(f'{c}=excluded.{c}' for c in cols if c!=pk)
        with self.connect() as c:c.execute(f'INSERT INTO {table}({",".join(cols)}) VALUES({",".join("?" for _ in cols)}) ON CONFLICT({pk}) DO UPDATE SET {upd}',[d[x] for x in cols]);c.commit()
    def _columns(self,table):
        with self.connect() as c:return {r['name'] for r in c.execute(f'PRAGMA table_info({table})')}
    def stage_start(self,key,stage,fingerprint,artifact=None):
        rid=uuid.uuid4().hex
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO songs(song_key,basename,duration_ms,pipeline_status,terminal) VALUES(?,?,?,?,?)",(str(key),str(key),0,"PROCESSING",0))
            c.execute('INSERT INTO stage_runs(run_id,song_key,stage_name,dependency_fingerprint,status,artifact_path,started_at) VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)',(rid,key,stage,fingerprint,'running',str(artifact) if artifact else None))
            c.commit()
        return rid
    def stage_finish(self,rid,status,artifact=None,sha=None,reused=0,error_code=None,error_message=None):
        with self.connect() as c:c.execute('UPDATE stage_runs SET status=?,artifact_path=COALESCE(?,artifact_path),artifact_sha256=?,reused=?,error_code=?,error_message=?,finished_at=CURRENT_TIMESTAMP WHERE run_id=?',(status,str(artifact) if artifact else None,str(sha) if sha else None,int(reused),error_code,str(error_message) if error_message else None,rid));c.commit()
    def latest_stage(self,key,stage,fingerprint,status='completed'):
        return self.one('SELECT * FROM stage_runs WHERE song_key=? AND stage_name=? AND dependency_fingerprint=? AND status=? ORDER BY finished_at DESC LIMIT 1',(key,stage,fingerprint,status))
    def audit(self,key,event,stage=None,status=None,message='',error_code=None):
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO songs(song_key,basename,duration_ms,pipeline_status,terminal) VALUES(?,?,?,?,?)",(str(key),str(key),0,"PROCESSING",0))
            c.execute('INSERT INTO audit_events(song_key,event_type,stage_name,status,message,error_code,created_at) VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)',(key,event,stage,status,message,error_code))
            c.commit()
    def upsert_youtube(self,key,values):self._upsert('youtube_matches','song_key',key,values)
    def upsert_ytmusic_audio(self,key,values):self._upsert('ytmusic_audio_matches','song_key',key,values)
    def add_youtube_candidates(self,key,candidates):
        with self.connect() as c:
            c.execute('DELETE FROM youtube_offset_candidates WHERE song_key=?',(key,))
            for x in candidates:c.execute('INSERT INTO youtube_offset_candidates(song_key,candidate_offset_ms,total_score,waveform_score,energy_score,peak_valley_score,transition_score,accepted,rejection_reason) VALUES(?,?,?,?,?,?,?,?,?)',(key,x['candidate_offset_ms'],x['total_score'],x.get('waveform_score'),x.get('energy_score'),x.get('peak_valley_score'),x.get('transition_score'),int(x.get('accepted',False)),x.get('rejection_reason')))
            c.commit()
    def upsert_lyric_source(self,key,values):self._upsert('lyric_sources','song_key',key,values)
    def upsert_demucs(self,key,values):
        rid=values.get('run_id') or uuid.uuid4().hex;d={**values,'run_id':rid,'song_key':key};self._upsert('demucs_runs','run_id',rid,d);return rid
    def upsert_alignment(self,key,values):
        rid=values.get('run_id') or uuid.uuid4().hex;d={**values,'run_id':rid,'song_key':key};self._upsert('alignment_runs','run_id',rid,d);return rid
    def upsert_8d(self,key,values):
        rid=values.get('run_id') or uuid.uuid4().hex;d={**values,'run_id':rid,'song_key':key};self._upsert('audio_8d_runs','run_id',rid,d);return rid

    def set_lyrics_words(self, key, alignment_run_id, lines, words):
        """Replace the normalized lyric line/word rows for one alignment run atomically.

        The pipeline can execute the alignment stage more than once (including cache
        reuse), so this method is intentionally idempotent: existing rows for the
        alignment run are removed and recreated in one transaction.
        """
        key = str(key)
        alignment_run_id = str(alignment_run_id)
        lines = list(lines or [])
        words = list(words or [])
        with self.connect() as c:
            # Delete children first because lyric_words may reference lyric_lines.
            c.execute("DELETE FROM lyric_words WHERE alignment_run_id=?", (alignment_run_id,))
            c.execute("DELETE FROM lyric_lines WHERE alignment_run_id=?", (alignment_run_id,))

            line_ids = {}
            for fallback_index, line in enumerate(lines):
                line_index = line.get("line_index", fallback_index)
                if line_index is None:
                    line_index = fallback_index
                line_index = int(line_index)
                text = str(line.get("text") or line.get("original") or "")
                if not text:
                    # The database requires line text; retain the row even for an
                    # empty/placeholder source line so word line indices stay stable.
                    text = " "
                source_start = line.get("source_start_ms")
                if source_start is None:
                    source_start = line.get("timestamp_ms")
                aligned_start = line.get("aligned_start_ms")
                if aligned_start is None:
                    aligned_start = line.get("start_ms")
                aligned_end = line.get("aligned_end_ms")
                if aligned_end is None:
                    aligned_end = line.get("end_ms")
                cur = c.execute(
                    "INSERT INTO lyric_lines(song_key,alignment_run_id,line_index,source_start_ms,aligned_start_ms,aligned_end_ms,text) VALUES(?,?,?,?,?,?,?)",
                    (key, alignment_run_id, line_index, source_start, aligned_start, aligned_end, text),
                )
                line_ids[line_index] = cur.lastrowid

            # If a Phase-2 word does not carry a line_index, preserve it rather than
            # attaching it to an arbitrary line. The schema allows line_id to be NULL.
            for fallback_index, word in enumerate(words):
                line_index = word.get("line_index")
                line_id = line_ids.get(int(line_index)) if line_index is not None else None
                word_index = word.get("word_index", fallback_index)
                if word_index is None:
                    word_index = fallback_index
                original = str(word.get("original_word") or word.get("original") or "")
                normalized = str(word.get("normalized_word") or word.get("normalized") or original)
                start_ms = word.get("start_ms")
                end_ms = word.get("end_ms")
                if start_ms is None or end_ms is None:
                    raise ValueError(f"LYRIC_WORD_TIMING_MISSING: alignment_run_id={alignment_run_id}, word_index={word_index}")
                source = str(word.get("source") or "aligned")
                interpolated = int(bool(word.get("interpolated", False)))
                c.execute(
                    "INSERT INTO lyric_words(song_key,alignment_run_id,line_id,word_index,original_word,normalized_word,start_ms,end_ms,score,source,interpolated,review_status,review_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, alignment_run_id, line_id, int(word_index), original, normalized, int(start_ms), int(end_ms), word.get("score"), source, interpolated, word.get("review_status"), word.get("review_reason")),
                )
            c.commit()

