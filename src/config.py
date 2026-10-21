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
    def load(cls, path=None, *, require_playlist=True):
        if path is None:
            project_root = Path(__file__).resolve().parent.parent
            preferred = project_root / 'config.json'
            template = project_root / 'config.json.example'
            p = (preferred if preferred.is_file() else template).resolve()
            if not p.is_file():
                raise FileNotFoundError(f"Neither runtime config nor template exists: {preferred} / {template}")
        else:
            p = Path(path).expanduser().resolve()
        d = json.loads(p.read_text(encoding='utf-8'))
        c = cls(p.parent, d, stable_json_hash(d), p)
        c.validate(require_playlist=require_playlist)
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

        Config.load() records whichever file it actually loaded: config.json when
        present, otherwise config.json.example for fresh source extractions.
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

    def validate(self, *, require_playlist=True):
        pid = self.get('playlist.spotify_playlist_id')
        if require_playlist and not pid:
            raise ValueError('playlist.spotify_playlist_id is required')
        if self.get('playlist.provider', 'spotify') != 'spotify':
            raise ValueError('playlist.provider must be spotify')
        # MMS/CTC lyric-anchor search settings. These values apply to the LRCLIB
        # transcript searched in both the acquired YT Music reference audio and the
        # selected YouTube video's audio; the offset no longer uses energy matching.
        anchor_words = int(self.get('youtube_offset.anchor_word_count', 10))
        min_anchor_words = int(self.get('youtube_offset.anchor_min_matched_words', 5))
        min_offset_words = int(self.get('youtube_offset.offset_min_matched_words', 5))
        window_seconds = int(self.get('youtube_offset.search_window_seconds', 28))
        stride_seconds = int(self.get('youtube_offset.search_stride_seconds', 12))
        max_search_seconds = int(self.get('youtube_offset.search_max_seconds', 300))
        anchor_conf = float(self.get('youtube_offset.anchor_min_word_confidence', 0.003))
        anchor_relative_score = float(self.get('youtube_offset.anchor_min_relative_score', 0.75))
        max_mad_ms = int(self.get('youtube_offset.offset_max_mad_ms', 800))
        if not 5 <= anchor_words <= 24:
            raise ValueError('youtube_offset.anchor_word_count must be between 5 and 24')
        if not 3 <= min_anchor_words <= anchor_words:
            raise ValueError('youtube_offset.anchor_min_matched_words must be between 3 and anchor_word_count')
        if not 3 <= min_offset_words <= anchor_words:
            raise ValueError('youtube_offset.offset_min_matched_words must be between 3 and anchor_word_count')
        if not 12 <= window_seconds <= 120:
            raise ValueError('youtube_offset.search_window_seconds must be between 12 and 120')
        if not 3 <= stride_seconds <= window_seconds:
            raise ValueError('youtube_offset.search_stride_seconds must be between 3 and search_window_seconds')
        if not window_seconds <= max_search_seconds <= 3600:
            raise ValueError('youtube_offset.search_max_seconds must be between search_window_seconds and 3600')
        if not 0.0 < anchor_conf <= 1.0:
            raise ValueError('youtube_offset.anchor_min_word_confidence must be in (0, 1]')
        if not 0.0 < anchor_relative_score <= 1.0:
            raise ValueError('youtube_offset.anchor_min_relative_score must be in (0, 1]')
        if not 100 <= max_mad_ms <= 5000:
            raise ValueError('youtube_offset.offset_max_mad_ms must be between 100 and 5000')
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
        # Reel intro values are optional for backwards compatibility with older configs.
        # Validate present values so JSON strings such as "false" cannot turn a flag on.
        for key in ('reel_intro.enabled', 'reel_intro.full_bleed_video', 'reel_intro.footer_enabled'):
            value = self.get(key)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f'{key} must be boolean')
        assets_dir = self.get('reel_intro.assets_dir', 'assets/reel_intro')
        if not isinstance(assets_dir, str) or not assets_dir.strip():
            raise ValueError('reel_intro.assets_dir must be a non-empty path string')
        for key, default in (('reel_intro.duration_ms', 3800),
                             ('reel_intro.silent_duration_ms', 2000),
                             ('reel_intro.fade_in_ms', 300),
                             ('reel_intro.fade_out_ms', 500)):
            try:
                value = int(self.get(key, default))
            except (TypeError, ValueError) as exc:
                raise ValueError(f'{key} must be an integer millisecond value') from exc
            maximum = 6000 if key == 'reel_intro.silent_duration_ms' else 10000
            if not 0 <= value <= maximum:
                raise ValueError(f'{key} must be between 0 and {maximum} ms')
        footer_text = self.get('reel_intro.footer_text', 'Vachika')
        if not isinstance(footer_text, str) or not footer_text.strip():
            raise ValueError('reel_intro.footer_text must be a non-empty string')
        try:
            footer_center_y = int(self.get('reel_intro.footer_center_y', 1800))
        except (TypeError, ValueError) as exc:
            raise ValueError('reel_intro.footer_center_y must be an integer pixel value') from exc
        if not 0 <= footer_center_y < int(self.get('video_layout.output_height', 1920)):
            raise ValueError('reel_intro.footer_center_y must be within the output frame height')
        hp_lines = self.get('reel_intro.headphone_lines')
        if hp_lines is not None:
            if isinstance(hp_lines, str):
                valid_lines = bool(hp_lines.strip())
            elif isinstance(hp_lines, list):
                valid_lines = bool(hp_lines) and all(isinstance(x, str) and x.strip() for x in hp_lines)
            else:
                valid_lines = False
            if not valid_lines:
                raise ValueError('reel_intro.headphone_lines must be a non-empty string or list of non-empty strings')
