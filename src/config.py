from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from src.common.hashing import stable_json_hash

@dataclass
class Config:
    root: Path
    data: dict
    config_hash: str
    config_file: Path | None = None

    @classmethod
    def load(cls, path=None):
        if path is None:
            p = (Path(__file__).resolve().parent.parent / 'config.json').resolve()
        else:
            p = Path(path).expanduser().resolve()
        d = json.loads(p.read_text(encoding='utf-8'))
        c = cls(p.parent, d, stable_json_hash(d), p)
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
        p = Path(v).expanduser()
        return p if p.is_absolute() else self.root / p

    @property
    def config_path(self):
        """Return the exact configuration file used to construct this object.

        Directly constructed Config objects retain the historical root/config.json
        default, while Config.load(custom_path) preserves that custom path.
        """
        return Path(self.config_file).resolve() if self.config_file else self.root / 'config.json'

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
        if int(self.get('youtube_offset.frame_ms', 25)) < 10:
            raise ValueError('youtube_offset.frame_ms must be >= 10')
        if int(self.get('youtube_offset.coarse_step_ms', 250)) < 1:
            raise ValueError('youtube_offset.coarse_step_ms must be >= 1')
        if int(self.get('youtube_offset.fine_step_ms', 25)) < 1:
            raise ValueError('youtube_offset.fine_step_ms must be >= 1')
        min_score = float(self.get('youtube_offset.min_match_score', 0.20))
        min_margin = float(self.get('youtube_offset.min_peak_margin', 0.04))
        if not -1.0 <= min_score <= 1.0:
            raise ValueError('youtube_offset.min_match_score must be in [-1, 1]')
        if not 0.0 <= min_margin <= 2.0:
            raise ValueError('youtube_offset.min_peak_margin must be in [0, 2]')
        if int(self.get('youtube_offset.ambiguity_window_ms', 1000)) < 0:
            raise ValueError('youtube_offset.ambiguity_window_ms must be >= 0')
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
        runtime_device = str(self.get('runtime.device', 'cuda')).strip().lower()
        fallback_device = str(self.get('runtime.fallback_device', 'cpu')).strip().lower()
        if runtime_device != 'cpu' and not runtime_device.startswith('cuda'):
            raise ValueError("runtime.device must be 'cpu', 'cuda', or a CUDA device such as 'cuda:0'")
        if fallback_device != 'cpu' and not fallback_device.startswith('cuda'):
            raise ValueError("runtime.fallback_device must be 'cpu', 'cuda', or a CUDA device such as 'cuda:0'")
        require_cuda = self.get('runtime.require_cuda', False)
        allow_cpu_fallback = self.get('runtime.allow_cpu_fallback', True)
        if not isinstance(require_cuda, bool):
            raise ValueError('runtime.require_cuda must be boolean')
        if not isinstance(allow_cpu_fallback, bool):
            raise ValueError('runtime.allow_cpu_fallback must be boolean')
        if require_cuda and not runtime_device.startswith('cuda'):
            raise ValueError('runtime.require_cuda=true requires runtime.device to be a CUDA device')
        if require_cuda and allow_cpu_fallback:
            raise ValueError('runtime.require_cuda and runtime.allow_cpu_fallback are contradictory; disable CPU fallback when CUDA is mandatory')
