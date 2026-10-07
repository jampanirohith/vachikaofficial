from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from .hashing import stable_json_hash

@dataclass
class Config:
    root: Path
    data: dict
    config_hash: str

    @classmethod
    def load(cls, path='config.json'):
        p = Path(path).resolve()
        d = json.loads(p.read_text(encoding='utf-8'))
        c = cls(p.parent, d, stable_json_hash(d))
        c.validate()
        return c

    def get(self, key, default=None):
        v = self.data
        for part in key.split('.'):
            if not isinstance(v, dict) or part not in v:
                return default
            v = v[part]
        return v

    def path(self, key, default=None):
        v = self.get(key, default)
        if not v:
            return None
        p = Path(v)
        return p if p.is_absolute() else self.root / p

    @property
    def config_path(self): return self.root / 'config.json'

    @property
    def db_dir(self): return self.path('paths.database_dir')
    @property
    def work_dir(self): return self.path('paths.work_dir')
    @property
    def final_dir(self): return self.path('paths.songs_final')
    @property
    def skipped_dir(self): return self.path('paths.skipped_no_sync')
    @property
    def reels_dir(self): return self.path('paths.reels_generated')
    @property
    def fonts_dir(self): return self.path('paths.fonts_dir')

    @property
    def hook_queue_file(self): return self.path('hook.queue_file', 'songs/final/hook_queue.json')

    def validate(self):
        pid = self.get('playlist.spotify_playlist_id')
        if pid is None:
            raise ValueError('playlist.spotify_playlist_id is required')
        if self.get('playlist.provider', 'spotify') != 'spotify':
            raise ValueError('playlist.provider must be spotify')
        if int(self.get('youtube_offset.reference_duration_ms', 30000)) != 30000:
            raise ValueError('youtube_offset.reference_duration_ms must be 30000')
        if int(self.get('youtube_offset.max_accepted_offset_ms', 30000)) != 30000:
            raise ValueError('youtube_offset.max_accepted_offset_ms must be 30000')
        if str(self.get('lyrics.endpoint', '/api/get')) != '/api/get':
            raise ValueError('lyrics.endpoint must remain /api/get')
        if str(self.get('duplicate_detection.identifier', 'isrc')).lower() != 'isrc':
            raise ValueError('duplicate_detection.identifier must be isrc')
        if self.get('hook.max_duration_ms') is not None:
            raise ValueError('Hook duration cap must not be configured')
        if not isinstance(self.get('hook.skip', False), bool):
            raise ValueError('hook.skip must be boolean')
        if not str(self.get('hook.queue_file', 'songs/final/hook_queue.json')).strip():
            raise ValueError('hook.queue_file must be non-empty')
        tol = int(self.get('ytmusic.duration_tolerance_ms', 2000))
        if tol < 0 or tol > 10000:
            raise ValueError('ytmusic.duration_tolerance_ms must be 0..10000')
        if not isinstance(self.get('spotify.enabled', True), bool):
            raise ValueError('spotify.enabled must be boolean')
