PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT OR REPLACE INTO schema_meta(key,value) VALUES
('schema_name','spotify_playlist_snapshot'),
('schema_version','4.0.0');

CREATE TABLE IF NOT EXISTS playlists(
 playlist_id TEXT PRIMARY KEY,
 provider TEXT NOT NULL DEFAULT 'spotify',
 playlist_url TEXT,
 display_name TEXT,
 owner_display_name TEXT,
 owner_id TEXT,
 snapshot_id TEXT,
 snapshot_scanned_once INTEGER NOT NULL DEFAULT 0,
 ingestion_status TEXT NOT NULL DEFAULT 'pending',
 ingestion_started_at TEXT,
 ingestion_completed_at TEXT,
 playlist_done INTEGER NOT NULL DEFAULT 0,
 entry_count INTEGER NOT NULL DEFAULT 0,
 completed_count INTEGER NOT NULL DEFAULT 0,
 skipped_count INTEGER NOT NULL DEFAULT 0,
 duplicate_count INTEGER NOT NULL DEFAULT 0,
 error_count INTEGER NOT NULL DEFAULT 0,
 notes TEXT
);

CREATE TABLE IF NOT EXISTS playlist_entries(
 playlist_serial INTEGER PRIMARY KEY,
 playlist_id TEXT NOT NULL,
 playlist_position INTEGER NOT NULL,
 added_at TEXT,
 added_by_user_id TEXT,
 added_by_user_name TEXT,
 spotify_track_id TEXT,
 spotify_uri TEXT,
 spotify_url TEXT,
 title TEXT,
 artist TEXT,
 album TEXT,
 spotify_duration_ms INTEGER,
 spotify_isrc TEXT,
 track_type TEXT,
 is_local INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN('pending','processing','completed','skipped','duplicate','error','needs_review')),
 terminal INTEGER NOT NULL DEFAULT 0,
 terminal_reason TEXT,
 error_code TEXT,
 error_message TEXT,
 raw_item_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(playlist_id,playlist_position),
 FOREIGN KEY(playlist_id) REFERENCES playlists(playlist_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_playlist_entries_queue ON playlist_entries(playlist_id,status,terminal,playlist_position);
CREATE INDEX IF NOT EXISTS idx_playlist_entries_spotify_track ON playlist_entries(spotify_track_id);
CREATE INDEX IF NOT EXISTS idx_playlist_entries_isrc ON playlist_entries(spotify_isrc);

CREATE TABLE IF NOT EXISTS playlist_snapshots(
 playlist_id TEXT PRIMARY KEY,
 snapshot_id TEXT,
 raw_playlist_json TEXT,
 fetched_entry_count INTEGER NOT NULL,
 committed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(playlist_id) REFERENCES playlists(playlist_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS playlist_audit_events(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 playlist_id TEXT,
 playlist_serial INTEGER,
 event_type TEXT NOT NULL,
 message TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(playlist_id) REFERENCES playlists(playlist_id) ON DELETE SET NULL,
 FOREIGN KEY(playlist_serial) REFERENCES playlist_entries(playlist_serial) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_playlist_audit_time ON playlist_audit_events(playlist_id,created_at);
