from __future__ import annotations

import json
from pathlib import Path


class Recovery:
    def __init__(self, cfg, playlist_db, songs_db, reels_db, logger):
        self.cfg = cfg
        self.p = playlist_db
        self.s = songs_db
        self.r = reels_db
        self.logger = logger

    def _song_key(self, entry):
        return f"{int(entry['playlist_serial'])}_{entry.get('spotify_track_id') or 'unavailable'}"

    def _final_files_valid(self, song):
        fields = ('final_mp3_path','final_lrc_path','final_wordlevel_lrc_path','final_json_path','final_8d_mp3_path')
        for field in fields:
            if not song.get(field):
                return False
            path = self.cfg.root / song[field]
            if not path.exists() or path.stat().st_size == 0:
                return False
        try:
            data = json.loads((self.cfg.root / song['final_json_path']).read_text(encoding='utf-8'))
            hashes = data.get('unified_pipeline', {}).get('hashes', {})
            from .hashing import sha256_file
            for name, field in (('mp3','final_mp3_path'),('lrc','final_lrc_path'),('wordlevel_lrc','final_wordlevel_lrc_path'),('8d_mp3','final_8d_mp3_path')):
                if hashes.get(name) != sha256_file(self.cfg.root / song[field]):
                    return False
            reel_key = f"{song['song_key']}_reel_v1"
            reel = self.r.one('SELECT * FROM reels WHERE reel_key=?', (reel_key,))
            if not reel or reel.get('pipeline_status') != 'FINALIZED':
                return False
            for field in ('final_reel_path','final_reel_json_path'):
                if not reel.get(field):
                    return False
                path = self.cfg.root / reel[field]
                if not path.exists() or path.stat().st_size == 0:
                    return False
            return True
        except Exception:
            return False

    def reconcile(self, playlist_id=None):
        fixed = []
        entries = self.p.all_entries(playlist_id) if playlist_id else []
        for entry in entries:
            if entry.get('status') != 'processing':
                continue
            key = self._song_key(entry)
            song = self.s.one('SELECT * FROM songs WHERE song_key=?', (key,))
            if not song:
                self.p.set_status(entry['playlist_serial'], 'pending', terminal=0, reason='RECOVERED_RETRY_MISSING_SONG_ROW')
                fixed.append(key)
                continue
            if song.get('pipeline_status') == 'FINALIZED' and self._final_files_valid(song):
                self.p.set_status(entry['playlist_serial'], 'completed', terminal=1, reason='RECOVERED_FINALIZED')
                fixed.append(key)
                continue
            if song.get('pipeline_status') == 'SKIPPED_NO_SYNCED_LRC':
                self.p.set_status(entry['playlist_serial'], 'skipped', terminal=1, reason='SKIPPED_NO_SYNCED_LRC')
                fixed.append(key)
                continue
            if str(song.get('pipeline_status') or '').startswith('DUPLICATE_KEEP_PREVIOUS'):
                self.p.set_status(entry['playlist_serial'], 'duplicate', terminal=1, reason='DUPLICATE_KEEP_PREVIOUS')
                fixed.append(key)
                continue
            self.p.set_status(entry['playlist_serial'], 'pending', terminal=0, reason='RECOVERED_RETRY')
            self.s.update_song(key, pipeline_status='pending', terminal=0, terminal_reason='RECOVERED_RETRY')
            fixed.append(key)
        if playlist_id:
            self.p.mark_playlist_done_if_complete(playlist_id)
        return fixed
